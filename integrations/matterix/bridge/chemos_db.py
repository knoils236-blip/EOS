"""Mirror local Matterix records into the ChemOS 2.0 database (paper: device / job / devicelog).

The JSON files under sessions/ and runs/ stay the source of truth. Every pass upserts what changed,
so a database outage only delays the copy: the next pass backfills it. Each task becomes one `job`
row; each worker event in events.jsonl and each status change becomes one `devicelog` row.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import threading
import time

import psycopg2
from psycopg2.extras import Json

from .common import ROOT, dumps, now, read_json, write_json
from .web_data import load_session

CONFIG_PATH = ROOT / "database.json"
STATUS_PATH = ROOT / "dbsync_status.json"
DEVICE_NAME = "matterix_simulation"
LOCATION = "simulation"

# device, deviceinformation, job and devicelog keep the column layout of ChemOS's database.py.
SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    id SERIAL PRIMARY KEY, name VARCHAR(64), type VARCHAR(64), descriptions VARCHAR(64),
    manifacturing VARCHAR(64), timestamp TIMESTAMP, location VARCHAR(64));
CREATE TABLE IF NOT EXISTS deviceinformation (
    id SERIAL PRIMARY KEY, device_id INTEGER REFERENCES device(id), name VARCHAR(64), details VARCHAR(64),
    version_driver VARCHAR(64), timestamp TIMESTAMP, location VARCHAR(64));
CREATE TABLE IF NOT EXISTS job (
    id SERIAL PRIMARY KEY, name VARCHAR(64), devices_id INTEGER REFERENCES device(id), code JSON,
    descriptions VARCHAR(4000), timestamp TIMESTAMP, location VARCHAR(64));
CREATE TABLE IF NOT EXISTS devicelog (
    id SERIAL PRIMARY KEY, device_id INTEGER REFERENCES device(id), job_id INTEGER REFERENCES job(id),
    name VARCHAR(64), status VARCHAR(2000), timestamp TIMESTAMP, location VARCHAR(64));
CREATE TABLE IF NOT EXISTS matterix_session (
    session_id VARCHAR(64) PRIMARY KEY, experiment_id VARCHAR(64) NOT NULL, scene_id VARCHAR(64),
    status VARCHAR(32) NOT NULL, status_logged VARCHAR(32), generation INTEGER, sim_time_s DOUBLE PRECISION,
    error TEXT, config_version VARCHAR(64), provenance JSONB,
    source VARCHAR(16) NOT NULL DEFAULT 'simulation' CHECK (source = 'simulation'),
    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ, synced_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS matterix_task (
    request_id VARCHAR(64) PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE REFERENCES job(id),
    session_id VARCHAR(64), experiment_id VARCHAR(64), capability_id VARCHAR(64), capability_version INTEGER,
    config_version VARCHAR(64), parameters JSONB, status VARCHAR(32) NOT NULL, status_logged VARCHAR(32),
    error TEXT, passed BOOLEAN, acceptance JSONB, elapsed_sim_s DOUBLE PRECISION, elapsed_wall_s DOUBLE PRECISION,
    result_path TEXT, result_sha256 CHAR(64), samples_path TEXT, samples_sha256 CHAR(64),
    video_path TEXT, video_sha256 CHAR(64), events_synced INTEGER NOT NULL DEFAULT 0,
    source VARCHAR(16) NOT NULL DEFAULT 'simulation' CHECK (source = 'simulation'),
    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, synced_at TIMESTAMPTZ NOT NULL);
CREATE INDEX IF NOT EXISTS matterix_task_session ON matterix_task (session_id);
CREATE INDEX IF NOT EXISTS devicelog_job ON devicelog (job_id);
"""
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out", "interrupted"})
# A dropped server surfaces as either class, depending on whether the rollback also fails.
CONNECTION_LOST = (psycopg2.OperationalError, psycopg2.InterfaceError)


def load_config(path=CONFIG_PATH):
    """Return connection keywords, or None when the database is not configured."""
    if not path.is_file():
        return None
    config = read_json(path)
    allowed = {"host", "port", "dbname", "user", "password"}
    if not isinstance(config, dict) or not {"host", "port", "dbname", "user"} <= set(config) <= allowed:
        raise ValueError("database.json needs host, port, dbname, user and optional password")
    return config


def connect(config):
    return psycopg2.connect(connect_timeout=2, application_name="matterix-bridge", **config)


def naive_utc(value):
    """ChemOS's TIMESTAMP columns hold UTC without a zone, as datetime.now() did in the paper code."""
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc).replace(tzinfo=None)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def event_summary(event):
    """Keep devicelog.status readable and under 2000 characters; bulky payloads stay in events.jsonl."""
    summary = {key: event[key] for key in ("event", "sim_time_s", "status", "error", "reason") if key in event}
    steps = event.get("steps")
    if isinstance(steps, list) and steps and isinstance(steps[0], dict):
        summary["action"] = steps[0].get("action_name")
    return dumps(summary)[:2000]


def ensure_device(cursor):
    cursor.execute(SCHEMA)
    cursor.execute("SELECT id FROM device WHERE name = %s ORDER BY id LIMIT 1", (DEVICE_NAME,))
    row = cursor.fetchone()
    if row:
        return row[0]
    stamp = datetime.utcnow()
    cursor.execute(
        "INSERT INTO device (name, type, descriptions, manifacturing, timestamp, location) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (DEVICE_NAME, "simulation", "Matterix Isaac Sim digital twin", "Matterix", stamp, "127.0.0.1:65200"),
    )
    device_id = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO deviceinformation (device_id, name, details, version_driver, timestamp, location) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (device_id, "sila2_feature", "Matterix.sila.xml", "bridge 0.1.0", stamp, "127.0.0.1:65200"),
    )
    return device_id


class DatabaseSync:
    def __init__(self, config, *, session_root=ROOT / "sessions", run_root=ROOT / "runs", status_path=STATUS_PATH):
        self.config = config
        self.session_root = session_root
        self.run_root = run_root
        self.status_path = status_path
        self.connection = None
        self.device_id = None
        self.seen = {}
        self.errors = {}
        self.stopping = threading.Event()
        self.thread = None

    # -- connection ---------------------------------------------------------------------------
    def _cursor(self):
        if self.connection is None or self.connection.closed:
            self.connection = connect(self.config)
            self.seen.clear()
            with self.connection, self.connection.cursor() as cursor:
                self.device_id = ensure_device(cursor)
        return self.connection

    def close(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=10)
        if self.connection is not None and not self.connection.closed:
            self.connection.close()

    # -- one pass -----------------------------------------------------------------------------
    def sync_once(self):
        """Copy every changed session and task; return (records written, records still failing).

        A bad record is reported and skipped until its files change; losing the connection aborts the pass.
        """
        connection = self._cursor()
        # Probe every pass: an idle connection to a stopped server looks healthy until it is used.
        with connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        written = 0
        jobs = []
        if self.session_root.is_dir():
            jobs += [(("session", folder.name), folder, ("session.json", "runtime.json"), self._sync_session)
                     for folder in sorted(self.session_root.iterdir()) if (folder / "session.json").is_file()]
        if self.run_root.is_dir():
            jobs += [(("task", folder.name), folder,
                      ("state.json", "events.jsonl", "result.json", "simulation.mp4"), self._sync_task)
                     for folder in sorted(self.run_root.iterdir()) if (folder / "state.json").is_file()]
        for key, folder, names, sync in jobs:
            if not self._changed(key, folder, *names):
                continue
            try:
                with connection, connection.cursor() as cursor:
                    sync(cursor, folder)
                written += 1
                self.errors.pop(f"{key[0]}:{folder.name}", None)
            except CONNECTION_LOST:
                raise
            except (psycopg2.Error, OSError, ValueError, KeyError, TypeError) as exc:
                self.errors[f"{key[0]}:{folder.name}"] = str(exc).strip()[:300]
        return written, dict(self.errors)

    def _changed(self, key, folder, *names):
        signature = tuple(
            (stat.st_size, stat.st_mtime_ns) if (folder / name).is_file() and (stat := (folder / name).stat()) else None
            for name in names
        )
        if self.seen.get(key) == signature:
            return False
        self.seen[key] = signature
        return True

    def _sync_session(self, cursor, folder):
        state = load_session(folder.name, self.session_root)
        provenance_path = folder / "session_provenance.json"
        provenance = read_json(provenance_path) if provenance_path.is_file() else None
        cursor.execute("SELECT status_logged FROM matterix_session WHERE session_id = %s FOR UPDATE",
                       (state["session_id"],))
        row = cursor.fetchone()
        cursor.execute(
            """INSERT INTO matterix_session (session_id, experiment_id, scene_id, status, generation, sim_time_s,
                   error, config_version, provenance, created_at, updated_at, synced_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
               ON CONFLICT (session_id) DO UPDATE SET status = EXCLUDED.status, generation = EXCLUDED.generation,
                   sim_time_s = EXCLUDED.sim_time_s, error = EXCLUDED.error, provenance = EXCLUDED.provenance,
                   updated_at = EXCLUDED.updated_at, synced_at = now()""",
            (state["session_id"], state.get("experiment_id", "unknown"), state.get("scene_id"), state["status"],
             state.get("generation"), state.get("sim_time_s"), state.get("error"), state.get("config_version"),
             Json(provenance) if provenance is not None else None, state.get("created_at"), state.get("updated_at")),
        )
        if row is None or row[0] != state["status"]:
            # Session lifecycle has no job of its own: job_id stays NULL, the name carries the session.
            cursor.execute(
                "INSERT INTO devicelog (device_id, job_id, name, status, timestamp, location) "
                "VALUES (%s, NULL, %s, %s, %s, %s)",
                (self.device_id, f"session:{state['session_id']}"[:64],
                 dumps({"event": "session_status", "status": state["status"], "error": state.get("error")}),
                 naive_utc(state.get("updated_at")) or datetime.utcnow(), LOCATION),
            )
            cursor.execute("UPDATE matterix_session SET status_logged = %s WHERE session_id = %s",
                           (state["status"], state["session_id"]))

    def _sync_task(self, cursor, folder):
        state = read_json(folder / "state.json")
        if state.get("request_id") != folder.name:
            raise ValueError(f"{folder.name}: state.json belongs to another request")
        request = state.get("request") or {}
        result_path = folder / "result.json"
        result = read_json(result_path) if state.get("status") in TERMINAL and result_path.is_file() else {}
        acceptance = result.get("acceptance") if isinstance(result.get("acceptance"), dict) else None

        cursor.execute("SELECT job_id, events_synced, status_logged, result_sha256, samples_sha256, video_sha256 "
                       "FROM matterix_task WHERE request_id = %s FOR UPDATE", (folder.name,))
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                "INSERT INTO job (name, devices_id, code, descriptions, timestamp, location) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (folder.name, self.device_id, Json(request),
                 f"Matterix 仿真任务 {state.get('capability_id') or request.get('workflow_id', '')}".strip(),
                 naive_utc(state.get("created_at")) or datetime.utcnow(), LOCATION),
            )
            job_id, events_synced, status_logged, hashes = cursor.fetchone()[0], 0, None, (None, None, None)
        else:
            job_id, events_synced, status_logged, hashes = row[0], row[1], row[2], row[3:]

        terminal = state.get("status") in TERMINAL
        files = {}
        for key, name, known in (("result", "result.json", hashes[0]), ("samples", "samples.jsonl", hashes[1]),
                                 ("video", "simulation.mp4", hashes[2])):
            path = folder / name
            # Hash once the task has ended; files still being written would hash differently next pass.
            files[key] = (str(path), known or sha256(path)) if terminal and path.is_file() else (None, None)

        events = self._read_events(folder / "events.jsonl", events_synced)
        for event in events:
            cursor.execute(
                "INSERT INTO devicelog (device_id, job_id, name, status, timestamp, location) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (self.device_id, job_id, str(event.get("event", "event"))[:64], event_summary(event),
                 naive_utc(event.get("wall_time_utc")) or datetime.utcnow(), LOCATION),
            )
        status = state["status"]
        if status != status_logged:
            cursor.execute(
                "INSERT INTO devicelog (device_id, job_id, name, status, timestamp, location) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (self.device_id, job_id, f"status:{status}"[:64],
                 dumps({"event": "task_status", "status": status, "error": state.get("error")})[:2000],
                 naive_utc(state.get("updated_at")) or datetime.utcnow(), LOCATION),
            )
        cursor.execute(
            """INSERT INTO matterix_task (request_id, job_id, session_id, experiment_id, capability_id,
                   capability_version, config_version, parameters, status, status_logged, error, passed, acceptance,
                   elapsed_sim_s, elapsed_wall_s, result_path, result_sha256, samples_path, samples_sha256,
                   video_path, video_sha256, events_synced, created_at, updated_at, completed_at, synced_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, now())
               ON CONFLICT (request_id) DO UPDATE SET status = EXCLUDED.status,
                   status_logged = EXCLUDED.status_logged, error = EXCLUDED.error, passed = EXCLUDED.passed,
                   acceptance = EXCLUDED.acceptance, elapsed_sim_s = EXCLUDED.elapsed_sim_s,
                   elapsed_wall_s = EXCLUDED.elapsed_wall_s, result_path = EXCLUDED.result_path,
                   result_sha256 = EXCLUDED.result_sha256, samples_path = EXCLUDED.samples_path,
                   samples_sha256 = EXCLUDED.samples_sha256, video_path = EXCLUDED.video_path,
                   video_sha256 = EXCLUDED.video_sha256, events_synced = EXCLUDED.events_synced,
                   updated_at = EXCLUDED.updated_at, completed_at = EXCLUDED.completed_at, synced_at = now()""",
            (folder.name, job_id, state.get("session_id") or request.get("session_id"),
             state.get("experiment_id") or request.get("experiment_id"), state.get("capability_id"),
             request.get("capability_version"), request.get("config_version"),
             Json(request.get("parameters")) if request.get("parameters") is not None else None,
             status, status, state.get("error"),
             acceptance.get("passed") if acceptance else None, Json(acceptance) if acceptance else None,
             result.get("elapsed_sim_s"), result.get("elapsed_wall_s", state.get("elapsed_wall_s")),
             *files["result"], *files["samples"], *files["video"], events_synced + len(events),
             state.get("created_at"), state.get("updated_at"), state.get("completed_at")),
        )

    @staticmethod
    def _read_events(path, start):
        """Return complete JSON lines after the first `start`; a half-written last line waits for the next pass."""
        if not path.is_file():
            return []
        events = []
        with path.open(encoding="utf-8") as stream:
            for index, line in enumerate(stream):
                if index < start:
                    continue
                if not line.endswith("\n"):
                    break
                events.append(json.loads(line))
        return events

    # -- background loop ----------------------------------------------------------------------
    def _report(self, **fields):
        write_json(self.status_path, {"updated_at": now(), **fields})

    def run_forever(self, interval_s=1.0):
        last_ok = None
        while not self.stopping.is_set():
            try:
                written, errors = self.sync_once()
                last_ok = now()
                self._report(online=True, last_success_at=last_ok, last_written=written, error=None,
                             record_errors=errors)
            except (psycopg2.Error, OSError, ValueError) as exc:
                if self.connection is not None and not self.connection.closed:
                    self.connection.close()
                self.connection = None
                self._report(online=False, last_success_at=last_ok, error=str(exc).strip()[:500])
                self.stopping.wait(5)  # back off while the database is away; files keep accumulating
                continue
            self.stopping.wait(interval_s)

    def start(self):
        self.thread = threading.Thread(target=self.run_forever, name="chemos-db-sync", daemon=True)
        self.thread.start()
        return self


def counts(config):
    """Row counts for the web page; raises psycopg2.Error when the database is unreachable."""
    with connect(config) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('matterix_task') IS NOT NULL")
        if not cursor.fetchone()[0]:
            return None
        cursor.execute(
            """SELECT (SELECT count(*) FROM device), (SELECT count(*) FROM job),
                      (SELECT count(*) FROM devicelog), (SELECT count(*) FROM matterix_session),
                      (SELECT count(*) FROM matterix_task), (SELECT max(synced_at) FROM matterix_task)""")
        device, job, devicelog, session, task, synced = cursor.fetchone()
    connection.close()
    return {"device": device, "job": job, "devicelog": devicelog, "matterix_session": session,
            "matterix_task": task, "last_synced_at": synced.isoformat() if synced else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="copy all changed records once and exit (backfill)")
    args = parser.parse_args()
    config = load_config()
    if config is None:
        raise SystemExit(f"未配置数据库：缺少 {CONFIG_PATH}")
    sync = DatabaseSync(config)
    try:
        if args.once:
            written, errors = sync.sync_once()
            print(dumps({"written": written, "record_errors": errors, "counts": counts(config)}))
        else:
            sync.run_forever()
    finally:
        sync.close()


if __name__ == "__main__":
    main()

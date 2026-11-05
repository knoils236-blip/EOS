"""SiLA2 endpoint, using the same installed protocol stack as ChemOS."""

import argparse
from pathlib import Path
import signal
import time
import io
import zipfile

from sila2.framework import Feature
from sila2.server import FeatureImplementationBase, SilaServer

from .capabilities import catalog
from .chemos_db import DatabaseSync, load_config
from .common import TERMINAL, dumps, loads
from .manager import RunManager


def rejected(exc):
    return dumps({"status": "rejected", "error": str(exc)})


class MatterixImplementation(FeatureImplementationBase):
    def __init__(self, server, manager):
        super().__init__(server)
        self.manager = manager

    def RunWorkflow(self, Request, *, metadata, instance):
        instance.begin_execution()
        try:
            if len(Request.encode("utf-8")) > 4096:
                raise ValueError("Request exceeds 4096 bytes")
            state = self.manager.submit(loads(Request))
        except (ValueError, RuntimeError) as exc:
            response = rejected(exc)
            instance.send_intermediate_response(response)
            return response
        return self._observe(instance, state)

    def _observe(self, instance, state):
        identifier = state["request_id"]
        previous_event, previous_sample, last_sent = None, None, 0.0
        while True:
            state = self.manager.get(identifier)
            terminal = state["status"] in TERMINAL
            event = state.get("latest_event")
            sample = state.get("latest_sample", {}).get("sim_time_s")
            stamp = (state["status"], dumps(event))
            changed = stamp != previous_event
            sample_due = sample != previous_sample and time.monotonic() - last_sent >= 0.5
            if changed or terminal or sample_due:
                update = state.copy()
                if not (sample_due or terminal):
                    update.pop("latest_sample", None)
                instance.send_intermediate_response(dumps(update))
                previous_event = stamp
                if sample_due or terminal:
                    previous_sample = sample
                    last_sent = time.monotonic()
            if terminal:
                return dumps(state)
            time.sleep(0.05)

    def GetCapabilities(self, *, metadata):
        try:
            return dumps(catalog())
        except (OSError, ValueError) as exc:
            return rejected(exc)

    def CreateSession(self, Request, *, metadata):
        try:
            if len(Request.encode("utf-8")) > 4096:
                raise ValueError("Request exceeds 4096 bytes")
            return dumps(self.manager.sessions.create(loads(Request)))
        except (ValueError, RuntimeError, OSError) as exc:
            return rejected(exc)

    def GetSession(self, SessionId, *, metadata):
        try:
            return dumps(self.manager.sessions.get(SessionId))
        except (ValueError, RuntimeError, OSError) as exc:
            return rejected(exc)

    def ListSessions(self, *, metadata):
        try:
            return dumps(self.manager.sessions.list())
        except (ValueError, RuntimeError, OSError) as exc:
            return rejected(exc)

    def ResetSession(self, SessionId, *, metadata):
        try:
            return dumps(self.manager.sessions.reset(SessionId))
        except (ValueError, RuntimeError, OSError) as exc:
            return rejected(exc)

    def EndSession(self, SessionId, *, metadata):
        try:
            return dumps(self.manager.sessions.end(SessionId))
        except (ValueError, RuntimeError, OSError) as exc:
            return rejected(exc)

    def SubmitTask(self, Request, *, metadata, instance):
        instance.begin_execution()
        try:
            if len(Request.encode("utf-8")) > 8192:
                raise ValueError("Request exceeds 8192 bytes")
            state = self.manager.sessions.submit_task(loads(Request))
        except (ValueError, RuntimeError, OSError) as exc:
            response = rejected(exc)
            instance.send_intermediate_response(response)
            return response
        return self._observe(instance, state)

    def GetRunState(self, RequestId, *, metadata):
        try:
            return dumps(self.manager.get(RequestId))
        except (ValueError, RuntimeError, OSError) as exc:
            return rejected(exc)

    def CancelRun(self, RequestId, *, metadata):
        try:
            return dumps(self.manager.cancel(RequestId))
        except (ValueError, RuntimeError, OSError) as exc:
            return rejected(exc)

    def GetRunArchive(self, RequestId, *, metadata):
        state = self.manager.get(RequestId)
        if state["status"] not in TERMINAL:
            raise RuntimeError("Run must be stopped before its evidence can be archived")
        run_dir = Path(state["run_dir"])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in ("request.json", "config.json", "state.json", "result.json", "provenance.json",
                         "events.jsonl", "samples.jsonl", "latest.json", "cancel.json", "simulation.mp4"):
                path = run_dir / name
                if path.is_file():
                    archive.write(path, name)
            if state.get("session_id"):
                directory = self.manager.sessions._session_directory(state["session_id"])
                for name in ("request.json", "session.json", "runtime.json", "session_provenance.json",
                             "reset_acceptance.json", "simulator.log"):
                    path = directory / name
                    if path.is_file():
                        archive.write(path, "session/" + name)
        return buffer.getvalue()


def make_server(manager):
    server = SilaServer(server_name="Matterix simulation", server_type="MatterixBridge",
                        server_description="Independent ChemOS to Matterix simulation bridge",
                        server_version="0.1.0", server_vendor_url="https://github.com/ac-rad/Matterix")
    feature = Feature(str(Path(__file__).with_name("Matterix.sila.xml")))
    server.set_feature_implementation(feature, MatterixImplementation(server, manager))
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--port", type=int, default=65200)
    parser.add_argument("--no-db", action="store_true", help="do not mirror records into the ChemOS database")
    parser.add_argument("--state-dir", type=Path, help="directory for durable runs and sessions")
    args = parser.parse_args()
    manager = RunManager(args.config, run_root=args.state_dir / "runs" if args.state_dir else None)
    database = None if args.no_db else load_config()
    sync = None
    server = None
    ending = False

    def shutdown(*_):
        nonlocal ending
        ending = True

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server = make_server(manager)
        server.start_insecure("127.0.0.1", args.port, enable_discovery=False)
        print(f"Matterix SiLA2 ready at 127.0.0.1:{args.port}", flush=True)
        if database is not None:
            # Local JSON stays authoritative; the mirror retries on its own while the database is down.
            sync = DatabaseSync(database).start()
            print(f"ChemOS database mirror -> {database['host']}:{database['port']}/{database['dbname']}", flush=True)
        while not ending:
            time.sleep(0.2)
    finally:
        if sync is not None:
            sync.close()
        manager.close()
        if server is not None and server.running:
            server.stop()


if __name__ == "__main__":
    main()

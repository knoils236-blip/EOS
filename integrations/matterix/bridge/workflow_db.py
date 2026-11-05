"""Link workflow and device jobs using the existing ChemOS tables."""

import json

import psycopg2
from psycopg2.extras import Json


def connect():
    return psycopg2.connect(host="127.0.0.1", port=5432, dbname="chemos", user="chemos", connect_timeout=3)


def create_job(name, device, code):
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM device WHERE name=%s AND type=%s ORDER BY id LIMIT 1",
                           (device, "simulation"))
            row = cursor.fetchone()
            if row is None:
                cursor.execute("INSERT INTO device(name,type,descriptions,manifacturing,timestamp,location) "
                               "VALUES (%s,%s,%s,%s,CURRENT_TIMESTAMP,%s) RETURNING id",
                               (device, "simulation", "ChemOS 本机模拟工作流", "ChemOS / Matterix", "local simulation"))
                row = cursor.fetchone()
            cursor.execute("INSERT INTO job(name,devices_id,code,descriptions,timestamp,location) "
                           "VALUES (%s,%s,%s,%s,CURRENT_TIMESTAMP,%s) RETURNING id",
                           (name, row[0], Json(code), "source=simulation", "local simulation"))
            return cursor.fetchone()[0]


def save(state, message):
    if not state.get("job_id"):
        return
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE job SET code=%s WHERE id=%s", (Json(state), state["job_id"]))
            cursor.execute("INSERT INTO devicelog(device_id,job_id,status,timestamp,location) "
                           "SELECT devices_id,id,%s,CURRENT_TIMESTAMP,%s FROM job WHERE id=%s",
                           (message[:2000], "local simulation", state["job_id"]))


def link(job_id, state, stage):
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT code FROM job WHERE id=%s FOR UPDATE", (job_id,))
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"设备任务不存在：{job_id}")
            code = row[0]
            if isinstance(code, str):
                code = json.loads(code)
            if not isinstance(code, dict):
                code = {}
            code.update(workflow_id=state["request_id"], workflow_job_id=state["job_id"],
                        workflow_stage=stage, source="simulation")
            cursor.execute("UPDATE job SET code=%s WHERE id=%s", (Json(code), job_id))


def workflow_for_job(job_id):
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT code FROM job WHERE id=%s", (job_id,))
            row = cursor.fetchone()
    if row and isinstance(row[0], dict):
        code = row[0]
        if code.get("kind") == "integrated_workflow":
            return code.get("request_id")
        return code.get("workflow_id")
    return None

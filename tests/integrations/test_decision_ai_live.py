"""Opt-in real EOS/Ray campaign against a local OpenAI chat protocol stub; never calls a provider."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid

from dotenv import load_dotenv
import pytest
import ray
import yaml

from eos.campaigns.decision import load_decision
from eos.campaigns.entities.campaign import CampaignSubmission, CampaignStatus
from eos.configuration.eos_config import EosConfig
from eos.orchestration.orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("EOS_TEST_DECISION_LIVE") != "1", reason="EOS_TEST_DECISION_LIVE not enabled"
)


async def test_ai_sampling_provenance_through_real_campaign(tmp_path, monkeypatch):
    requests = []

    class ChatStub(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/v1/chat/completions"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            volume = 76 if len(requests) == 1 else 74
            answer = {
                "suggestions": [{"parameters": {"prepare.stock_volume_ul": volume}}],
                "journal_entry": f"本地协议桩验收：以 {volume} µL 检查选参、执行与观测回传；不代表真实模型推理。",
            }
            response = {
                "id": "local-validation",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "local-call",
                                    "type": "function",
                                    "function": {
                                        "name": body["tools"][0]["function"]["name"],
                                        "arguments": json.dumps(answer),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ChatStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("EOS_AI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("EOS_AI_API_KEY", "local-validation-key")
    monkeypatch.setenv("EOS_AI_MODEL", "local-validation-model")
    load_dotenv(ROOT / ".env")
    user_dir = tmp_path / "user"
    shutil.copytree(ROOT / "user/matterix_sim", user_dir / "matterix_sim", ignore=shutil.ignore_patterns("__pycache__"))
    lab_path = user_dir / "matterix_sim/labs/mixture_mock_lab/lab.yml"
    lab = yaml.safe_load(await asyncio.to_thread(lab_path.read_text))
    lab["devices"]["workstation"]["init_parameters"]["journal_dir"] = str(tmp_path / "journal")
    await asyncio.to_thread(lab_path.write_text, yaml.safe_dump(lab))
    token = "ai_protocol_" + uuid.uuid4().hex[:10]
    ray.init(address="auto", namespace=token, ignore_reinit_error=True)
    config = EosConfig(
        user_dir=user_dir,
        packages={"matterix_sim"},
        db={"type": "sqlite", "sqlite": {"db_dir": tmp_path, "db_name": "acceptance"}},
    )
    orchestrator = Orchestrator(config)
    evidence = Path(os.environ.get("EOS_DECISION_EVIDENCE_DIR", str(tmp_path / "evidence")))
    await asyncio.to_thread(evidence.mkdir, parents=True, exist_ok=True)
    try:
        await orchestrator.initialize()
        async with orchestrator.db_interface.get_async_session() as db:
            await orchestrator.loading.load_labs(db, {"mixture_mock_lab"})
            await orchestrator.loading.load_protocols(db, {"mixture_mock"})
            await orchestrator.campaigns.submit_campaign(
                db,
                CampaignSubmission(
                    name=token,
                    protocol="mixture_mock",
                    optimize=True,
                    max_protocol_runs=2,
                    meta={"optimizer_overrides": {"p_ai": 1.0, "ai_base_url": "http://127.0.0.1:1/blocked-override"}},
                ),
            )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            await orchestrator.spin_once()
            async with orchestrator.db_interface.get_async_session() as db:
                campaign = await orchestrator.campaigns.get_campaign(db, token)
            if campaign.status in {CampaignStatus.COMPLETED, CampaignStatus.FAILED}:
                break
            await asyncio.sleep(0.05)
        assert campaign.status == CampaignStatus.COMPLETED, campaign.error_message
        async with orchestrator.db_interface.get_async_session() as db:
            decision = await load_decision(db, token)
        assert len(requests) == 2
        assert all(row["selection"]["method"] == "ai" for row in decision["rounds"])
        assert all("本地协议桩验收" in row["selection"]["reason"] for row in decision["rounds"])
        assert all(row["eligible"] for row in decision["rounds"])
        assert "local-validation-key" not in json.dumps(campaign.model_dump(mode="json"))
        (evidence / "ai-protocol-acceptance.json").write_text(
            json.dumps({"passed": True, "decision": decision}, indent=2)
        )
        (evidence / "local-chat-requests.json").write_text(json.dumps(requests, indent=2))
    finally:
        await orchestrator.terminate()
        await orchestrator.db_interface._async_engine.dispose()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        with (
            sqlite3.connect(tmp_path / "acceptance.db") as source,
            sqlite3.connect(evidence / (token + ".sqlite3")) as target,
        ):
            source.backup(target)

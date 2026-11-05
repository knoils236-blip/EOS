import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd

from eos.campaigns.campaign_executor import CampaignExecutor, _PendingOptimizerSample


async def test_batch_provenance_is_attached_to_named_runs_before_execution():
    executor = object.__new__(CampaignExecutor)
    executor._campaign_name = "campaign"
    executor._save_optimizer_meta_dict = AsyncMock()
    saved = []

    async def save(db, name, decision):
        saved.append((name, decision))

    @asynccontextmanager
    async def db():
        yield None

    executor._db_interface = SimpleNamespace(get_async_session=db)
    executor._protocol_run_manager = SimpleNamespace(set_sampling_decision=save)
    future = asyncio.get_running_loop().create_future()
    reason = {"method": "ai", "reason": "验证已观察到的目标附近区域。", "model": "openai:local-stub-model"}
    future.set_result((pd.DataFrame({"prepare.stock_volume_ul": [74, 80]}), {"sampling_decision": reason}))
    pending = _PendingOptimizerSample(future, [("run_2", {}), ("run_1", {})])
    resolved = await executor._resolve_pending_sample(pending)
    assert saved == [("run_2", reason), ("run_1", reason)]
    assert resolved == [
        ("run_2", {"prepare": {"stock_volume_ul": 74}}),
        ("run_1", {"prepare": {"stock_volume_ul": 80}}),
    ]

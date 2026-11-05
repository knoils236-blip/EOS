"""Saving an agent-designed protocol into a package."""

from unittest.mock import Mock

import pytest
import yaml
from sqlalchemy import select

from eos.configuration.configuration_manager import ConfigurationManager
from eos.configuration.entities.definition import DefinitionModel
from eos.configuration.entities.protocol_def import ProtocolDef
from eos.configuration.eos_config import DatabaseType, DbConfig, SqliteDbConfig
from eos.configuration.exceptions import EosConfigurationError
from eos.database.sqlite_db_interface import SqliteDbInterface
from eos.orchestration.services.loading_service import LoadingService

YAML = "type: screen_a\ndesc: Screen\nlabs: [bench]\ntasks: []\n"


@pytest.fixture
def service(tmp_path):
    package = tmp_path / "user" / "pkg"
    (package / "labs" / "bench").mkdir(parents=True)
    (package / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "0.1.0"\n')
    (package / "labs" / "bench" / "lab.yml").write_text("name: bench\ndesc: Bench\ndevices: {}\n")
    cm = ConfigurationManager(str(tmp_path / "user"))
    return LoadingService(cm, Mock(), Mock(), Mock(), Mock())


@pytest.fixture
async def db():
    db_interface = SqliteDbInterface(DbConfig(type=DatabaseType.SQLITE, sqlite=SqliteDbConfig(in_memory=True)))
    await db_interface.initialize_database()
    async with db_interface.get_async_session() as session:
        yield session


def protocol(text: str) -> ProtocolDef:
    return ProtocolDef.model_validate(yaml.safe_load(text))


async def test_saved_protocol_is_written_and_registered_but_not_loaded_without_its_labs(service, db, tmp_path):
    loaded = await service.save_protocol(db, "pkg", protocol(YAML), YAML, overwrite=False)

    assert loaded is False
    assert (tmp_path / "user/pkg/protocols/screen_a/protocol.yml").read_text() == YAML
    row = (await db.execute(select(DefinitionModel).where(DefinitionModel.name == "screen_a"))).scalar_one()
    assert (row.type, row.package_name, row.data["labs"]) == ("protocol", "pkg", ["bench"])


async def test_existing_protocol_needs_overwrite_and_types_must_be_folder_safe(service, db, tmp_path):
    await service.save_protocol(db, "pkg", protocol(YAML), YAML, overwrite=False)
    with pytest.raises(EosConfigurationError, match="overwrite"):
        await service.save_protocol(db, "pkg", protocol(YAML), YAML, overwrite=False)
    changed = YAML.replace("desc: Screen", "desc: Screen v2")
    await service.save_protocol(db, "pkg", protocol(changed), changed, overwrite=True)
    assert "Screen v2" in (tmp_path / "user/pkg/protocols/screen_a/protocol.yml").read_text()

    unsafe = YAML.replace("screen_a", "../escape")
    with pytest.raises(EosConfigurationError, match="snake_case"):
        await service.save_protocol(db, "pkg", protocol(unsafe), unsafe, overwrite=False)
    with pytest.raises(EosConfigurationError, match="Unknown package"):
        await service.save_protocol(db, "nope", protocol(YAML), YAML, overwrite=False)

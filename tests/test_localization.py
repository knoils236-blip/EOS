"""Exercise CLI help without starting services or changing user configuration."""

import pytest
from typer.testing import CliRunner

from eos.eos import eos_app


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ([], "交互式配置 EOS"),
        (["start"], "启动 EOS 组件"),
        (["setup"], "仅写入配置文件"),
        (["sim"], "调度器类型"),
        (["pkg", "add"], "条目类型"),
        (["services", "down"], "永久删除数据"),
        (["auth", "create-user"], "首次登录时必须修改密码"),
        (["update"], "跳过数据库迁移"),
    ],
)
def test_cli_help_in_chinese(command, expected):
    result = CliRunner().invoke(eos_app, [*command, "--help"], env={"TERM": "dumb", "COLUMNS": "160"})
    assert result.exit_code == 0, result.output
    assert expected in result.output

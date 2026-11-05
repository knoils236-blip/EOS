import keyword
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
import yaml

from eos.cli._common import DEFAULT_CONFIG_PATH, ConfigOption, UserDirOption, run
from eos.configuration.exceptions import EosConfigurationError
from eos.configuration.packages import Package, discover_packages
from eos.logging.logger import log

pkg_app = typer.Typer(help="管理 EOS 软件包", no_args_is_help=True)

DEFAULT_USER_DIR = "./user"


class EntityType(StrEnum):
    LAB = "lab"
    DEVICE = "device"
    TASK = "task"
    PROTOCOL = "protocol"


_DEVICE_TEMPLATE = '''from eos import Device


class {class_name}(Device, type="{name}"):
    """Describe the device."""
'''

_TASK_TEMPLATE = '''from eos import task


@task("{name}")
async def {name}() -> None:
    """Describe the task."""
'''

# Starter files stamped out for each entity type, formatted with the entity name.
ENTITY_FILES: dict[EntityType, dict[str, str]] = {
    EntityType.LAB: {"lab.yml": ""},
    EntityType.DEVICE: {"device.py": _DEVICE_TEMPLATE},
    EntityType.TASK: {"task.py": _TASK_TEMPLATE},
    EntityType.PROTOCOL: {"protocol.yml": "", "optimizer.py": ""},
}


def _resolve_user_dir(user_dir: str | None, config: str) -> Path:
    """Resolve the user directory with precedence: --user-dir > config.yml's user_dir > ./user."""
    if user_dir is not None:
        return Path(user_dir)
    config_path = Path(config)
    if config_path.is_file():
        try:
            with config_path.open() as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:
            log.warning(f"Could not read user_dir from {config_path}: {e}")
        else:
            configured = data.get("user_dir")
            if configured:
                return Path(configured)
    return Path(DEFAULT_USER_DIR)


def _discover_or_exit(user_dir: Path) -> dict[str, Package]:
    """Run discover_packages, converting EosConfigurationError into a clean CLI exit."""
    try:
        return discover_packages(user_dir)
    except EosConfigurationError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e


def _run_uv_install(pyproject: Path, extra_args: list[str]) -> None:
    try:
        run(["uv", "pip", "install", "-r", str(pyproject), *extra_args])
    except subprocess.CalledProcessError as e:
        typer.echo(f"从 {pyproject} 安装依赖失败： {e}", err=True)
        raise typer.Exit(1) from e


_GITIGNORE = """\
# Byte-compiled / optimized / DLL files
__pycache__/
*.py[codz]
*$py.class

# C extensions
*.so

# Distribution / packaging
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
share/python-wheels/
*.egg-info/
.installed.cfg
*.egg
MANIFEST

# PyInstaller
*.manifest
*.spec

# Installer logs
pip-log.txt
pip-delete-this-directory.txt

# Unit test / coverage reports
htmlcov/
.tox/
.nox/
.coverage
.coverage.*
.cache
nosetests.xml
coverage.xml
*.cover
*.py.cover
.hypothesis/
.pytest_cache/
cover/

# Translations
*.mo
*.pot

# Django stuff:
*.log
local_settings.py
db.sqlite3
db.sqlite3-journal

# Flask stuff:
instance/
.webassets-cache

# Scrapy stuff:
.scrapy

# Sphinx documentation
docs/_build/

# PyBuilder
.pybuilder/
target/

# Jupyter Notebook
.ipynb_checkpoints

# IPython
profile_default/
ipython_config.py

# pyenv
# .python-version

# pipenv
# Pipfile.lock

# UV
# uv.lock

# poetry
# poetry.lock
# poetry.toml

# pdm
# pdm.lock
# pdm.toml
.pdm-python
.pdm-build/

# pixi
# pixi.lock
.pixi

# PEP 582
__pypackages__/

# Celery stuff
celerybeat-schedule
celerybeat.pid

# Redis
*.rdb
*.aof
*.pid

# RabbitMQ
mnesia/
rabbitmq/
rabbitmq-data/

# ActiveMQ
activemq-data/

# SageMath parsed files
*.sage.py

# Environments
.env
.envrc
.venv
env/
venv/
ENV/
env.bak/
venv.bak/

# Spyder project settings
.spyderproject
.spyproject

# Rope project settings
.ropeproject

# mkdocs documentation
/site

# mypy
.mypy_cache/
.dmypy.json
dmypy.json

# Pyre type checker
.pyre/

# pytype static type analyzer
.pytype/

# Cython debug symbols
cython_debug/

# PyCharm
# .idea/

# Abstra
.abstra/

# Visual Studio Code
# .vscode/

# Ruff stuff:
.ruff_cache/

# PyPI configuration file
.pypirc

# Marimo
marimo/_static/
marimo/_lsp/
__marimo__/

# Streamlit
.streamlit/secrets.toml
"""


def _validate_package_exists(package_dir: Path) -> None:
    """Validate that the package exists and has the expected structure."""
    if not package_dir.exists():
        raise typer.BadParameter(f"软件包目录 {package_dir} 不存在")
    if not package_dir.is_dir():
        raise typer.BadParameter(f"{package_dir} 不是目录")


def _add_entity(package_dir: Path, entity_type: str, name: str, files: dict[str, str]) -> None:
    """Add a new entity to the package, creating its starter files."""
    base_dir = package_dir / f"{entity_type}s" / name
    class_name = "".join(part.capitalize() for part in name.split("_"))

    try:
        base_dir.mkdir(parents=True, exist_ok=False)
        for filename, template in files.items():
            (base_dir / filename).write_text(template.format(name=name, class_name=class_name))
        typer.echo(f"已在 {base_dir} 创建 {entity_type} '{name}'")
    except FileExistsError:
        typer.echo(f"错误：{entity_type.title()} '{name}' 已存在", err=True)
    except Exception as e:
        typer.echo(f"创建 {entity_type} 时出错： {e!s}", err=True)


@pkg_app.command(name="create")
def create_package(
    name: Annotated[str, typer.Argument(help="要创建的软件包名称")],
    user_dir: UserDirOption = None,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
) -> None:
    """在用户目录中按指定名称创建软件包。"""
    resolved_user_dir = _resolve_user_dir(user_dir, config)
    package_dir = resolved_user_dir / name
    subdirs = ["devices", "tasks", "labs", "protocols"]

    try:
        package_dir.mkdir(parents=True, exist_ok=False)
        for subdir in subdirs:
            (package_dir / subdir).mkdir()

        readme_content = f"# {name}"
        readme_path = package_dir / "README.md"
        readme_path.write_text(readme_content)

        pyproject_content = f"""[project]
name = "{name}"
version = "0.1.0"
description = f"EOS package {name}"
readme = "README.md"
dependencies = [
  "eos",
]
"""
        pyproject_path = package_dir / "pyproject.toml"
        pyproject_path.write_text(pyproject_content)

        gitignore_path = package_dir / ".gitignore"
        gitignore_path.write_text(_GITIGNORE)

        typer.echo(f"已在 {package_dir} 创建软件包 '{name}'")
    except FileExistsError:
        typer.echo(f"错误：软件包 '{name}' 已存在于 {resolved_user_dir}", err=True)
    except Exception as e:
        typer.echo(f"创建软件包时出错： {e!s}", err=True)


@pkg_app.command(name="add")
def add_entity(
    entity: Annotated[EntityType, typer.Argument(help="要添加的条目类型")],
    package: Annotated[str, typer.Argument(help="目标软件包名称")],
    name: Annotated[str, typer.Argument(help="要创建的条目名称")],
    user_dir: UserDirOption = None,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
) -> None:
    """向现有软件包添加实验室、设备、任务或协议。"""
    package_dir = _resolve_user_dir(user_dir, config) / package
    _validate_package_exists(package_dir)
    if entity in (EntityType.DEVICE, EntityType.TASK) and (not name.isidentifier() or keyword.iskeyword(name)):
        typer.echo(f"错误：{entity.value.title()} 名称 '{name}' 必须为有效的 Python 标识符", err=True)
        raise typer.Exit(1)
    _add_entity(package_dir, entity.value, name, ENTITY_FILES[entity])


@pkg_app.command(
    name="install",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def install_package(
    ctx: typer.Context,
    all_packages: Annotated[bool, typer.Option("--all", "-a", help="安装所有软件包的依赖")] = False,
    user_dir: UserDirOption = None,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
) -> None:
    """通过 uv 安装一个或多个 EOS 用户软件包的 Python 依赖。

    指定一个或多个软件包名称，或使用 --all 安装所有已发现软件包的依赖。
    以 - 开头的参数及其后续参数将传给 uv pip install。
    示例：eos pkg install cahoon_lab color_lab --upgrade。
    """
    # Split raw args: everything up to the first flag is a package name, the rest goes to uv.
    raw = list(ctx.args)
    split = next((i for i, a in enumerate(raw) if a.startswith("-")), len(raw))
    names = raw[:split]
    extra = raw[split:]

    if not names and not all_packages:
        raise typer.BadParameter("请指定一个或多个软件包名称，或使用 --all。")
    if names and all_packages:
        raise typer.BadParameter("不能同时指定软件包名称和 --all。")

    resolved = _resolve_user_dir(user_dir, config)
    packages = _discover_or_exit(resolved)

    if all_packages:
        if not packages:
            typer.echo(f"在 {resolved} 中未找到软件包。", err=True)
            raise typer.Exit(1)
        selected = list(packages.values())
    else:
        missing = [n for n in names if n not in packages]
        if missing:
            available = ", ".join(sorted(packages)) or "<none>"
            raise typer.BadParameter(f"在 {resolved} 中未找到软件包：{', '.join(missing)}。可用软件包：{available}")
        selected = [packages[n] for n in names]

    for pkg in selected:
        typer.echo(f"正在安装 {pkg.name}（{pkg.path}）…")
        _run_uv_install(pkg.path / "pyproject.toml", extra)


if __name__ == "__main__":
    pkg_app()

import os
import secrets
import shutil
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt

from eos.auth.zitadel_bootstrap import ORG_USER_MANAGER_ROLE, ZitadelBootstrapClient, read_bootstrap_pat
from eos.auth.zitadel_client import generate_complexity_password

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"
CONFIG_PATH = REPO_ROOT / "config.yml"
WEB_UI_DIR = REPO_ROOT / "web_ui"
WEB_UI_ENV_PATH = WEB_UI_DIR / ".env"

DEFAULT_HTTP_PORT = 80
DEFAULT_HTTPS_PORT = 443
DEV_HTTPS_PORT = 8443
TLS_MODES = ["internal", "custom", "acme"]

COMPOSE_PROJECT = "eos"
ZITADEL_VOLUMES = [f"{COMPOSE_PROJECT}_zitadel_db_data", f"{COMPOSE_PROJECT}_zitadel_bootstrap"]
ZITADEL_SERVICES = ["zitadel", "zitadel-db", "zitadel-login", "zitadel-proxy"]

console = Console()


@dataclass
class SetupAnswers:
    """Everything the wizard collects, plus secrets it generates."""

    user_dir: str = "./user"
    log_level: str = "INFO"

    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_name: str = "eos"
    pg_user: str = "eos"
    pg_password: str = ""

    s3_endpoint: str = "http://localhost:8333"
    s3_bucket: str = "eos"
    s3_access_key: str = "eos"
    s3_secret_key: str = ""

    web_api_host: str = "localhost"
    web_api_port: int = 8070

    web_ui: bool = True
    ui_url: str = "http://localhost:3000"

    auth_enabled: bool = False
    # Public origin of the identity provider; every other Zitadel deployment value derives from it
    issuer: str = "http://localhost:8080"
    tls_mode: str = "none"
    zitadel_masterkey: str = ""
    zitadel_db_password: str = ""
    zitadel_admin_password: str = ""
    auth_secret: str = ""
    relax_password_policy: bool = False

    @property
    def _origin(self) -> SplitResult:
        return urlsplit(self.issuer)

    @property
    def zitadel_scheme(self) -> str:
        return self._origin.scheme or "http"

    @property
    def zitadel_domain(self) -> str:
        return self._origin.hostname or "localhost"

    @property
    def zitadel_external_port(self) -> int:
        return self._origin.port or (DEFAULT_HTTPS_PORT if self.zitadel_scheme == "https" else DEFAULT_HTTP_PORT)

    @property
    def zitadel_host_ports(self) -> tuple[int, int]:
        """Host ports the bundled proxy publishes, as (http, https). The two must never be equal."""
        if self.tls_mode == "none":
            return self.zitadel_external_port, DEV_HTTPS_PORT
        return DEFAULT_HTTP_PORT, self.zitadel_external_port

    @property
    def database_url(self) -> str:
        return f"postgresql://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_name}"


def _random_string(length: int, alphabet: str = string.ascii_letters + string.digits) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _section(title: str) -> None:
    console.print(f"\n[bold cyan]{title}[/bold cyan]")


def _ok(message: str) -> None:
    console.print(f"  [green]✓[/green] {message}")


def _warn(message: str) -> None:
    console.print(f"  [yellow]![/yellow] {message}")


def _parse_env_line(line: str) -> tuple[str, str] | None:
    """Split a 'KEY=value' dotenv line into (key, value), or None for blanks and comments."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    return key.strip(), value.strip()


def _update_env_file(path: Path, values: dict[str, str]) -> None:
    """Set KEY=value in a dotenv file, preserving comments and order; append any missing keys."""
    remaining = dict(values)
    lines = path.read_text().splitlines() if path.exists() else []
    out: list[str] = []
    for line in lines:
        parsed = _parse_env_line(line)
        if parsed and parsed[0] in remaining:
            out.append(f"{parsed[0]}={remaining.pop(parsed[0])}")
            continue
        out.append(line)
    out.extend(f"{key}={value}" for key, value in remaining.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")


def _run(cmd: list[str], cwd: Path | None = None) -> bool:
    """Run a command, streaming its output. Returns True on success."""
    try:
        return subprocess.call(cmd, cwd=cwd) == 0
    except FileNotFoundError:
        _warn(f"在 PATH 中未找到 {cmd[0]}")
        return False


def _preflight() -> None:
    _section("Preflight")
    _ok("使用当前 Python 环境（如缺少依赖，请先运行 'uv sync'）")
    if shutil.which("docker"):
        _ok("已找到 docker")
    else:
        _warn("未找到 docker，启动服务和初始化身份验证需要 docker")


def _defaults_from_env() -> SetupAnswers:
    """Seed answers from any existing .env / web_ui/.env so re-runs keep working values."""
    env = _read_existing_env(ENV_PATH)
    web = _read_existing_env(WEB_UI_ENV_PATH)
    a = SetupAnswers()
    a.pg_host = env.get("EOS_POSTGRES_HOST", a.pg_host)
    a.pg_port = int(env.get("EOS_POSTGRES_PORT", a.pg_port))
    a.pg_name = env.get("EOS_POSTGRES_DB", a.pg_name)
    a.pg_user = env.get("EOS_POSTGRES_USER", a.pg_user)
    a.pg_password = env.get("EOS_POSTGRES_PASSWORD", "")
    a.s3_endpoint = env.get("EOS_S3_ENDPOINT_URL", a.s3_endpoint)
    a.s3_bucket = env.get("EOS_S3_BUCKET", a.s3_bucket)
    a.s3_access_key = env.get("EOS_S3_ACCESS_KEY_ID", a.s3_access_key)
    a.s3_secret_key = env.get("EOS_S3_SECRET_ACCESS_KEY", "")
    a.web_api_host = env.get("EOS_WEB_API_HOST", a.web_api_host)
    a.web_api_port = int(env.get("EOS_WEB_API_PORT", a.web_api_port))
    a.auth_enabled = env.get("EOS_AUTH_ENABLED", "false").lower() == "true"
    a.ui_url = web.get("NEXT_PUBLIC_APP_URL", a.ui_url)
    a.auth_secret = web.get("AUTH_SECRET", "")
    a.issuer = env.get("EOS_AUTH_ISSUER", a.issuer)
    a.tls_mode = env.get("ZITADEL_TLS_MODE", a.tls_mode)
    a.zitadel_masterkey = env.get("ZITADEL_MASTERKEY", "")
    a.zitadel_db_password = env.get("ZITADEL_DB_PASSWORD", "")
    a.zitadel_admin_password = env.get("ZITADEL_ADMIN_PASSWORD", "")
    return a


def _collect_answers(a: SetupAnswers) -> SetupAnswers:
    _section("数据库（PostgreSQL）")
    a.pg_host = Prompt.ask("PostgreSQL 主机", default=a.pg_host)
    a.pg_port = IntPrompt.ask("PostgreSQL 端口", default=a.pg_port)
    a.pg_name = Prompt.ask("数据库名称", default=a.pg_name)
    a.pg_user = Prompt.ask("数据库用户", default=a.pg_user)
    entered = Prompt.ask("数据库密码（留空以保留现有值或自动生成）", password=True, default="")
    a.pg_password = entered or a.pg_password or _random_string(20)

    _section("对象存储（S3）")
    a.s3_endpoint = Prompt.ask("S3 端点 URL", default=a.s3_endpoint)
    a.s3_bucket = Prompt.ask("S3 存储桶", default=a.s3_bucket)
    a.s3_access_key = Prompt.ask("S3 访问密钥", default=a.s3_access_key)
    entered = Prompt.ask("S3 秘密密钥（留空以保留现有值或自动生成）", password=True, default="")
    a.s3_secret_key = entered or a.s3_secret_key or _random_string(24)

    _section("Web API")
    a.web_api_host = Prompt.ask("Web API 主机", default=a.web_api_host)
    a.web_api_port = IntPrompt.ask("Web API 端口", default=a.web_api_port)

    _section("功能")
    a.web_ui = Confirm.ask("是否配置 Web 界面？", default=True)
    if a.web_ui:
        a.ui_url = Prompt.ask("Web 界面 URL", default=a.ui_url)
    a.auth_enabled = Confirm.ask("是否启用身份验证（Zitadel）？", default=a.auth_enabled)
    if a.auth_enabled:
        _section("身份验证（Zitadel）")
        a.issuer = Prompt.ask("Zitadel 地址", default=a.issuer).rstrip("/")
        if a.zitadel_scheme == "https":
            tls_default = a.tls_mode if a.tls_mode in TLS_MODES else "internal"
            a.tls_mode = Prompt.ask("TLS 证书", choices=TLS_MODES, default=tls_default)
        else:
            a.tls_mode = "none"
        a.relax_password_policy = Confirm.ask("是否放宽 Zitadel 密码要求？", default=a.relax_password_policy)
        # Reuse existing secrets where present; the Zitadel masterkey in particular can never change.
        a.zitadel_masterkey = a.zitadel_masterkey or _random_string(32)
        a.zitadel_db_password = a.zitadel_db_password or _random_string(20)
        a.zitadel_admin_password = a.zitadel_admin_password or generate_complexity_password()
        a.auth_secret = a.auth_secret or secrets.token_urlsafe(32)
        _ok("已准备 Zitadel 主密钥、数据库密码、管理员密码和 Web 界面身份验证密钥")

    return a


def _write_config_files(a: SetupAnswers) -> None:
    _section("正在写入配置")

    config: dict = {
        "user_dir": a.user_dir,
        "labs": [],
        "protocols": [],
        "log_level": a.log_level,
        "scheduler": {"type": "greedy"},
    }
    CONFIG_PATH.write_text(yaml.safe_dump(config, sort_keys=False))
    _ok(f"已写入 {CONFIG_PATH.name}")

    env_values = {
        "COMPOSE_PROJECT_NAME": "eos",
        "EOS_POSTGRES_DB": a.pg_name,
        "EOS_POSTGRES_HOST": a.pg_host,
        "EOS_POSTGRES_PORT": str(a.pg_port),
        "EOS_POSTGRES_USER": a.pg_user,
        "EOS_POSTGRES_PASSWORD": a.pg_password,
        "EOS_S3_BUCKET": a.s3_bucket,
        "EOS_S3_ENDPOINT_URL": a.s3_endpoint,
        "EOS_S3_ACCESS_KEY_ID": a.s3_access_key,
        "EOS_S3_SECRET_ACCESS_KEY": a.s3_secret_key,
        "EOS_WEB_API_HOST": a.web_api_host,
        "EOS_WEB_API_PORT": str(a.web_api_port),
        "EOS_AUTH_ENABLED": str(a.auth_enabled).lower(),
    }
    if a.auth_enabled:
        http_port, https_port = a.zitadel_host_ports
        env_values |= {
            "EOS_AUTH_ISSUER": a.issuer,
            "ZITADEL_TLS_MODE": a.tls_mode,
            "ZITADEL_MASTERKEY": a.zitadel_masterkey,
            "ZITADEL_DB_PASSWORD": a.zitadel_db_password,
            "ZITADEL_ADMIN_PASSWORD": a.zitadel_admin_password,
            # Derived from EOS_AUTH_ISSUER for the compose stack
            "ZITADEL_PUBLIC_SCHEME": a.zitadel_scheme,
            "ZITADEL_DOMAIN": a.zitadel_domain,
            "ZITADEL_EXTERNAL_PORT": str(a.zitadel_external_port),
            "ZITADEL_EXTERNAL_SECURE": str(a.zitadel_scheme == "https").lower(),
            "ZITADEL_HTTP_PORT": str(http_port),
            "ZITADEL_HTTPS_PORT": str(https_port),
        }
    _update_env_file(ENV_PATH, env_values)
    _ok(f"已写入 {ENV_PATH.name}")

    if a.web_ui:
        web_values = {
            "DATABASE_URL": a.database_url,
            "ORCHESTRATOR_API_URL": f"http://{a.web_api_host}:{a.web_api_port}/api",
            "USER_DIR": str((REPO_ROOT / a.user_dir).resolve()),
            "EOS_S3_BUCKET": a.s3_bucket,
            "EOS_S3_ENDPOINT_URL": a.s3_endpoint,
            "EOS_S3_ACCESS_KEY_ID": a.s3_access_key,
            "EOS_S3_SECRET_ACCESS_KEY": a.s3_secret_key,
            "NEXT_PUBLIC_APP_URL": a.ui_url,
            "AUTH_ENABLED": str(a.auth_enabled).lower(),
        }
        if a.auth_enabled:
            web_values |= {"AUTH_SECRET": a.auth_secret, "AUTH_ISSUER": a.issuer}
        _update_env_file(WEB_UI_ENV_PATH, web_values)
        _ok(f"已写入 web_ui/{WEB_UI_ENV_PATH.name}")


def _start_zitadel() -> bool:
    return _run(["docker", "compose", "--profile", "auth", "up", "-d", *ZITADEL_SERVICES], cwd=REPO_ROOT)


def _reset_zitadel_volumes() -> None:
    """Recreate Zitadel's data volumes so it re-initializes with the configured credentials. Destroys accounts."""
    _run(["docker", "compose", "--profile", "auth", "rm", "-sf", *ZITADEL_SERVICES], cwd=REPO_ROOT)
    _run(["docker", "volume", "rm", *ZITADEL_VOLUMES])


def _bootstrap_auth(a: SetupAnswers) -> bool:
    """Deploy Zitadel and programmatically create the org, project, roles, apps, and service user."""
    if not _start_zitadel():
        _warn("Zitadel 启动失败，通常是之前遗留的数据卷导致")
        if not Confirm.ask("是否重置 Zitadel 数据卷并重试？这将删除所有 Zitadel 账户", default=True):
            _warn("跳过身份验证初始化")
            return False
        _reset_zitadel_volumes()
        if not _start_zitadel():
            _warn("Zitadel 仍无法启动，已跳过（请检查 'docker logs eos-zitadel'）")
            return False

    console.print("  正在等待 Zitadel 就绪…")
    admin_pat = read_bootstrap_pat()
    _ok("已读取初始化管理员 PAT")

    client = ZitadelBootstrapClient(a.issuer, admin_pat)
    try:
        client.wait_ready()
        _ok("Zitadel 已就绪")

        client.disable_registration()
        _ok("已禁用自助注册")

        org_id = client.ensure_org("EOS")
        project_id = client.ensure_project("EOS")
        _ok(f"已创建组织和项目 'EOS'（项目 ID：{project_id}）")

        if a.relax_password_policy:
            client.relax_password_complexity()
            _ok("已放宽密码要求")

        dev_mode = a.zitadel_scheme == "http"
        web_client_id = client.ensure_oidc_app(
            project_id, f"{a.ui_url}/api/auth/callback/zitadel", f"{a.ui_url}/signin", dev_mode
        )
        _ok("已创建应用 'EOS Web UI'")

        api_client_id, api_client_secret = client.ensure_api_app(project_id)
        _ok("已创建应用 'EOS API'")

        user_id = client.ensure_machine_user("eos-user-admin")
        client.ensure_org_member(user_id, [ORG_USER_MANAGER_ROLE])
        _ok("已创建服务用户 'eos-user-admin'")

        # Always mint a fresh PAT: a reused one from .env may belong to an old (reset) Zitadel instance.
        pat = client.create_pat(user_id)
        # The API client secret can only be read at creation, so reuse .env when the app already existed.
        existing = _read_existing_env(ENV_PATH)
        api_client_secret = api_client_secret or existing.get("EOS_AUTH_INTROSPECTION_CLIENT_SECRET", "")
        if not api_client_secret:
            _warn("无法获取 EOS API 客户端密钥，请清空该值并重新运行以生成")
    finally:
        client.close()

    _update_env_file(
        ENV_PATH,
        {
            "EOS_AUTH_ENABLED": "true",
            "EOS_AUTH_ISSUER": a.issuer,
            "EOS_AUTH_ORG_ID": org_id,
            "EOS_AUTH_PROJECT_ID": project_id,
            "EOS_AUTH_PAT": pat,
            "EOS_AUTH_INTROSPECTION_CLIENT_ID": api_client_id,
            "EOS_AUTH_INTROSPECTION_CLIENT_SECRET": api_client_secret,
        },
    )
    if a.web_ui:
        _update_env_file(
            WEB_UI_ENV_PATH,
            {
                "AUTH_ENABLED": "true",
                "AUTH_CLIENT_ID": web_client_id,
                "AUTH_ORG_ID": org_id,
                "AUTH_PROJECT_ID": project_id,
                "AUTH_PAT": pat,
                "AUTH_INTROSPECTION_CLIENT_ID": api_client_id,
                "AUTH_INTROSPECTION_CLIENT_SECRET": api_client_secret,
            },
        )
    _ok("已将身份验证凭据写入 .env 和 web_ui/.env")
    return True


def _read_existing_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        parsed = _parse_env_line(line)
        if parsed:
            values[parsed[0]] = parsed[1]
    return values


def _bootstrap_auth_step(a: SetupAnswers) -> None:
    """Deploy and bootstrap Zitadel, the one part of setup that touches services. Auth needs it pre-configured."""
    _section("身份验证（Zitadel）")
    # Make the freshly written .env available to docker compose interpolation regardless of the working directory.
    os.environ.update(_read_existing_env(ENV_PATH))
    if not _bootstrap_auth(a):
        _warn("身份验证初始化未完成，请重新运行 'eos setup' 重试")


def _print_summary(a: SetupAnswers) -> None:
    console.print("\n[bold green]配置完成。[/bold green]")
    written = f"{CONFIG_PATH.name}, {ENV_PATH.name}" + (f", web_ui/{WEB_UI_ENV_PATH.name}" if a.web_ui else "")
    console.print(f"  配置已写入 {written}。")

    services = "PostgreSQL, SeaweedFS" + (", Zitadel" if a.auth_enabled else "")
    console.print("\n[bold]后续步骤[/bold]")
    console.print(f"  eos services up    # 启动基础服务（{services}）")
    console.print("  eos start          # 启动编排器和 REST API（首次运行时初始化数据库）")
    if a.web_ui:
        console.print("  (cd web_ui && npm install)   # 安装 Web 界面依赖，仅首次需要")
        console.print("  eos start ui       # 构建并启动 Web 界面")
    if a.auth_enabled:
        console.print("  eos auth create-user <username> <email> --superuser   # 创建账户并授予超级用户角色")


def run_setup(
    skip_bootstrap: bool = typer.Option(False, "--skip-bootstrap", help="仅写入配置文件，不部署或初始化 Zitadel"),
) -> None:
    """通过交互式向导写入 EOS 配置，并在启用身份验证时初始化 Zitadel。"""
    console.print(Panel.fit("[bold]EOS 配置向导[/bold]", border_style="cyan"))
    try:
        _preflight()
        answers = _collect_answers(_defaults_from_env())
        _write_config_files(answers)
        if answers.auth_enabled and not skip_bootstrap:
            _bootstrap_auth_step(answers)
        _print_summary(answers)
    except KeyboardInterrupt:
        console.print("\n[yellow]配置已取消，请重新运行 'eos setup' 开始配置。[/yellow]")
        raise typer.Exit(1) from None
    except Exception as e:
        typer.secho(f"配置失败： {e}", fg="red", err=True)
        raise typer.Exit(1) from e

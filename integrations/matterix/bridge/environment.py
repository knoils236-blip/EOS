"""Shared local Matterix environment entry point."""

from pathlib import Path
from typing import Any

from .common import read_json
from .session_actions import sha256_file


INTEGRATION_ROOT = Path(__file__).resolve().parent.parent
GRID_ASSET_DIR = INTEGRATION_ROOT / "assets/Grid"
GRID_ASSET_USD = GRID_ASSET_DIR / "default_environment.usd"
GRID_ASSET_MANIFEST = GRID_ASSET_DIR / "manifest.json"


def grid_asset_provenance() -> dict[str, object]:
    """Return the cached Grid source metadata for worker provenance."""
    manifest = read_json(GRID_ASSET_MANIFEST)
    return {
        "usd": str(GRID_ASSET_USD),
        "manifest": str(GRID_ASSET_MANIFEST),
        "manifest_sha256": sha256_file(GRID_ASSET_MANIFEST),
        "source_root": manifest["source_root"],
        "files": manifest["files"],
    }


def create_local_environment(*args: Any, **kwargs: Any) -> Any:
    """Construct MatterixBaseEnv after Isaac starts, with only the ground USD redirected."""
    if not GRID_ASSET_USD.is_file():
        raise FileNotFoundError(f"Grid local asset cache is incomplete: {GRID_ASSET_USD}")
    from matterix.envs import MatterixBaseEnv  # noqa: PLC0415

    class LocalMatterixBaseEnv(MatterixBaseEnv):
        def setup_scene(self) -> None:
            super().setup_scene()
            self.cfg.scene.plane.spawn.usd_path = str(GRID_ASSET_USD)

    return LocalMatterixBaseEnv(*args, **kwargs)

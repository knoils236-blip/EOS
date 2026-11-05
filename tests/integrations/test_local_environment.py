import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

BRIDGE_ROOT = Path(__file__).resolve().parents[2] / "integrations/matterix"
sys.path.insert(0, str(BRIDGE_ROOT))

from bridge.environment import (  # noqa: E402
    GRID_ASSET_DIR,
    GRID_ASSET_MANIFEST,
    GRID_ASSET_USD,
    create_local_environment,
)


def test_local_environment_only_redirects_ground_spawn(monkeypatch):
    matterix = ModuleType("matterix")
    envs = ModuleType("matterix.envs")

    class FakeMatterixBaseEnv:
        def __init__(self, cfg, render_mode=None):
            self.cfg = cfg
            self.render_mode = render_mode
            self.setup_scene_called = False
            self.setup_scene()

        def setup_scene(self):
            self.setup_scene_called = True
            self.cfg.scene.plane = SimpleNamespace(
                prim_path="/World/GroundPlane",
                init_state=("unchanged-state",),
                spawn=SimpleNamespace(
                    usd_path="https://example.invalid/default_environment.usd",
                    color=(0.1, 0.2, 0.3),
                    size=(100.0, 100.0),
                    physics_material="unchanged-material",
                ),
            )

    envs.MatterixBaseEnv = FakeMatterixBaseEnv
    monkeypatch.setitem(sys.modules, "matterix", matterix)
    monkeypatch.setitem(sys.modules, "matterix.envs", envs)

    cfg = SimpleNamespace(scene=SimpleNamespace())
    env = create_local_environment(cfg, render_mode="rgb_array")
    plane = env.cfg.scene.plane

    assert env.setup_scene_called
    assert env.render_mode == "rgb_array"
    assert plane.spawn.usd_path == str(GRID_ASSET_USD)
    assert plane.prim_path == "/World/GroundPlane"
    assert plane.init_state == ("unchanged-state",)
    assert plane.spawn.color == (0.1, 0.2, 0.3)
    assert plane.spawn.size == (100.0, 100.0)
    assert plane.spawn.physics_material == "unchanged-material"


def test_grid_manifest_hashes_every_cached_file():
    manifest = json.loads(GRID_ASSET_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["files"]
    for relative, metadata in manifest["files"].items():
        path = GRID_ASSET_DIR / relative
        assert path.is_file()
        assert path.stat().st_size == metadata["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
        assert metadata["source_url"] == manifest["source_root"].rstrip("/") + "/" + relative

    assert manifest["files"][GRID_ASSET_USD.name]["size_bytes"] == GRID_ASSET_USD.stat().st_size
    assert json.loads(GRID_ASSET_MANIFEST.read_text(encoding="utf-8")) == manifest

import sys

import pytest

from eos.integrations.aiida import verdi_run

# Stands in for `verdi [-p PROFILE] run SCRIPT ARGS...`: runs SCRIPT with ARGS as sys.argv
FAKE_VERDI = f"""#!{sys.executable}
import json, os, runpy, sys
args = sys.argv[1:]
profile = args[1] if args[0] == "-p" else None
script, *rest = args[args.index("run") + 1:]
os.environ["FAKE_PROFILE"] = str(profile)
sys.argv = [script, *rest]
runpy.run_path(script, run_name="__main__")
"""

SCRIPT = """
import json, os, sys
inputs = json.load(open(sys.argv[1]))
print("workchain running")
json.dump({"gain": len(inputs["smiles"]), "profile": os.environ["FAKE_PROFILE"]}, open(sys.argv[2], "w"))
"""


@pytest.fixture
def fake_verdi(tmp_path):
    verdi = tmp_path / "verdi"
    verdi.write_text(FAKE_VERDI)
    verdi.chmod(0o755)
    return str(verdi)


class TestVerdiRun:
    async def test_passes_inputs_and_returns_outputs(self, tmp_path, fake_verdi):
        script = tmp_path / "run_laser.py"
        script.write_text(SCRIPT)

        outputs = await verdi_run(script, {"smiles": "c1ccccc1"}, verdi=fake_verdi, profile="lab")

        assert outputs == {"gain": 8, "profile": "lab"}

    async def test_failure_includes_script_log(self, tmp_path, fake_verdi):
        script = tmp_path / "broken.py"
        script.write_text("raise SystemExit('remote computer niagara not configured')")

        with pytest.raises(RuntimeError, match="niagara not configured"):
            await verdi_run(script, {}, verdi=fake_verdi)

    async def test_missing_outputs_is_an_error(self, tmp_path, fake_verdi):
        script = tmp_path / "silent.py"
        script.write_text("pass")

        with pytest.raises(RuntimeError, match=r"without writing outputs\.json"):
            await verdi_run(script, {}, verdi=fake_verdi)

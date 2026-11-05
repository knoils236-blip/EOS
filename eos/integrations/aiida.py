"""
Run AiiDA computations (e.g. DFT workchains, as in ChemOS 2.0) from EOS tasks.

AiiDA is not imported here: it runs in its own environment through ``verdi run``, so its dependency pins
never meet EOS's. The script receives two paths in ``sys.argv``: a JSON file with the task's inputs to read,
and a JSON file to write its outputs to.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any


async def verdi_run(
    script: str | Path,
    inputs: dict[str, Any],
    *,
    verdi: str = "verdi",
    profile: str | None = None,
) -> dict[str, Any]:
    """
    Run an AiiDA script with ``verdi run`` and return the JSON outputs it wrote.

    :param script: Script that loads ``sys.argv[1]``, runs the AiiDA process and dumps results to ``sys.argv[2]``.
    :param inputs: JSON-serializable inputs for the script.
    :param verdi: Path to the ``verdi`` executable of the AiiDA environment.
    :param profile: AiiDA profile to use, or None for the default profile.
    :return: The script's outputs.
    """
    with tempfile.TemporaryDirectory(prefix="eos-aiida-") as tmp:
        inputs_path = Path(tmp) / "inputs.json"
        outputs_path = Path(tmp) / "outputs.json"
        inputs_path.write_text(json.dumps(inputs))

        profile_args = ["-p", profile] if profile else []
        proc = await asyncio.create_subprocess_exec(
            verdi,
            *profile_args,
            "run",
            str(script),
            str(inputs_path),
            str(outputs_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        log, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"verdi run {script} failed (exit {proc.returncode}):\n{log.decode()[-2000:]}")
        if not outputs_path.exists():
            raise RuntimeError(f"verdi run {script} finished without writing {outputs_path.name}")
        return json.loads(outputs_path.read_text())

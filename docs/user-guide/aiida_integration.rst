AiiDA Integration
=================
Tasks can run `AiiDA <https://www.aiida.net>`_ computations, such as the DFT workchains of ChemOS 2.0, with
``eos.integrations.aiida.verdi_run``. Because a computation is an ordinary task, it can run in parallel with
lab tasks in the same protocol and its outputs can feed the optimizer.

AiiDA is not installed into EOS. ``verdi_run`` calls ``verdi run`` from AiiDA's own environment, so the two
dependency sets stay separate. AiiDA keeps its own provenance database; it may share the EOS PostgreSQL server
under a separate database.

AiiDA Script
------------
The script receives two paths: a JSON file with the task inputs, and a JSON file to write outputs to.
Return the process ``pk`` so the EOS result links to AiiDA provenance.

:bdg-primary:`run_laser.py`

.. code-block:: python

    import json
    import sys

    from aiida.engine import run_get_node
    from aiida.orm import Str
    from my_workchains import LaserWorkChain  # Your workchain; its output names will differ

    inputs = json.load(open(sys.argv[1]))
    results, node = run_get_node(LaserWorkChain, smiles=Str(inputs["smiles"]))
    json.dump({"pk": node.pk, "gain_factor": results["gain_factor"].value}, open(sys.argv[2], "w"))

Task
----

:bdg-primary:`task.py`

.. code-block:: python

    from pathlib import Path

    from pydantic import BaseModel

    from eos import Param, task
    from eos.integrations.aiida import verdi_run

    SCRIPT = Path(__file__).with_name("run_laser.py")


    class DftOutputs(BaseModel):
        gain_factor: float = Param(desc="Simulated maximum gain factor.")
        aiida_pk: int = Param(desc="AiiDA process pk.")


    @task("Laser DFT")
    async def laser_dft(smiles: str = Param(desc="Molecule SMILES.")) -> DftOutputs:
        out = await verdi_run(SCRIPT, {"smiles": smiles}, verdi="/opt/aiida-env/bin/verdi", profile="lab")
        return DftOutputs(gain_factor=out["gain_factor"], aiida_pk=out["pk"])

``verdi_run`` raises ``RuntimeError`` with the end of the ``verdi`` log when the script fails or writes no
outputs. The ``verdi`` path, the AiiDA profile, and any remote computers the workchain uses must be configured
on the computer that runs the task.

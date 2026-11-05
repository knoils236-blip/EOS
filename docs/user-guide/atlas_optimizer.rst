Atlas Optimizer
===============
``AtlasOptimizer`` runs `Atlas <https://github.com/aspuru-guzik-group/atlas>`_, the Bayesian planner of
ChemOS 2.0, as an EOS campaign optimizer.

Atlas pins old ``torch``, ``botorch`` and ``pandas`` versions that cannot share a Python environment with EOS.
EOS therefore keeps the observations itself and, for each recommendation, runs
``eos/optimization/atlas_bridge.py`` with the interpreter of a separate environment that has Atlas and
Olympus installed. Requests and results pass as JSON. The interpreter path must be valid on the computer that
hosts the optimizer actor.

Setup
-----
Create an environment with Atlas and Olympus, for example the one used to run ChemOS 2.0, and note its
interpreter path. EOS needs no extra packages.

optimizer.py
------------

.. code-block:: python

    from eos.optimization.abstract_sequential_optimizer import AbstractSequentialOptimizer
    from eos.optimization.atlas_optimizer import AtlasOptimizer

    MOLECULES = ["A001", "A002", "A003"]
    FINGERPRINTS = [[0, 1, 1, 0], [1, 1, 0, 0], [0, 0, 1, 1]]  # One descriptor vector per option


    def eos_create_campaign_optimizer() -> tuple[dict, type[AbstractSequentialOptimizer]]:
        constructor_args = {
            "inputs": [
                {"key": "synthesis.molecule", "type": "categorical",
                 "options": MOLECULES, "descriptors": FINGERPRINTS},
            ],
            "outputs": [
                {"key": "dft.gain_factor", "goal": "max"},
                {"key": "characterization.gain", "goal": "max"},
            ],
            "python": "/opt/atlas-env/bin/python",
            "planner": {
                "kind": "tanimoto",
                "use_descriptors": True,
                "num_init_design": 1,
                "acquisition_optimizer_kind": "genetic",
            },
        }
        return constructor_args, AtlasOptimizer

Inputs
    ``key`` is ``task_name.param_name``. ``type`` is ``continuous`` (with ``low`` and ``high``), ``discrete``
    (with ``options``) or ``categorical`` (with ``options`` and optional ``descriptors``).

Outputs
    ``goal`` is ``min`` or ``max``. With more than one output, Atlas scalarizes the objectives
    (Hypervolume by default) and optimizes the merit.

Planner
    Keyword arguments for the Atlas planner. ``kind`` selects ``botorch`` (default, ``BoTorchPlanner``) or
    ``tanimoto`` (``TanimotoPlanner``, a Tanimoto kernel over molecular fingerprints). ``batch_size`` and
    ``goal`` are set by EOS.

``timeout``
    Seconds to wait for one recommendation (default 600).

Tested Configurations
---------------------
Tested with the Atlas and Olympus revisions pinned by ChemOS 2.0. Other combinations fail inside Atlas itself:

.. list-table::
   :header-rows: 1

   * - Parameters
     - Planner settings
   * - One categorical with descriptors (the ChemOS 2.0 setup)
     - ``tanimoto``, ``genetic``, one or more objectives
   * - Continuous only
     - ``botorch``, ``gradient``
   * - Continuous + discrete
     - ``botorch``, ``gradient``
   * - Continuous + categorical
     - ``botorch``, ``genetic``
   * - Discrete + categorical
     - ``botorch``, ``gradient``

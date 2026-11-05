Campaigns
=========
A campaign runs a protocol repeatedly with supplied or optimizer-generated parameters.
``max_concurrent_protocol_runs`` controls how many runs can execute at once.

.. figure:: ../_static/img/dmta-loop.png
   :alt: The Design, Make, Test, Analyze loop
   :align: center

Choose How to Supply Parameters
-------------------------------
* **Optimization**: Set ``optimize: true`` and define an ``optimizer.py`` beside the protocol.
  EOS samples inputs, runs the protocol, and reports measured outputs to the optimizer.
* **Fixed parameters**: Use ``global_parameters`` for values shared by every run.
* **Parameter schedule**: Use ``protocol_run_parameters`` for values that vary by run.
  Without optimization, supply either global parameters or a complete schedule.

For example, the :doc:`color_mixing` campaign optimizes mixing parameters to minimize
``score_color.loss``. The desired ``score_color.target_color`` is fixed in ``global_parameters``.
See :doc:`optimizers` for the optimizer contract and :doc:`rest_api` for submission examples.

Campaign Settings
-----------------
.. list-table::
   :header-rows: 1

   * - Field
     - Purpose
   * - ``name`` / ``protocol``
     - Unique campaign name and loaded protocol type.
   * - ``max_protocol_runs``
     - Total number of runs, including completed runs when resuming.
   * - ``max_concurrent_protocol_runs``
     - Maximum simultaneous runs. Defaults to 1.
   * - ``max_failed_protocol_runs``
     - Failed runs to tolerate before the campaign fails. Defaults to 0 (the first failure fails the campaign).
       See `Failed Runs`_.
   * - ``optimizer_ip``
     - Ray worker for optimization. Defaults to ``127.0.0.1``.
   * - ``meta.optimizer_overrides``
     - Optimizer settings to override at submission or resume.
   * - ``resume``
     - Resume an existing campaign using the same name.

Failed Runs
-----------
By default, a failed protocol run fails the whole campaign. For unattended operation, set
``max_failed_protocol_runs`` so that the campaign continues past individual failures:

* A tolerated failure uses up one run of ``max_protocol_runs``, so a parameter sweep moves on to the next
  parameter set instead of repeating one.
* With optimization, the failed run's inputs are reported to the optimizer with NaN outputs. BoFire
  ignores them when fitting; Atlas uses them to learn infeasible regions. They never enter the Pareto front.
* The next failure beyond the limit fails the campaign.
* On resume, tolerated failures are kept and reported to the optimizer again.

For transient errors within a single task, use the task's ``retries`` and ``timeout`` fields instead
(see :doc:`protocols`).

Resuming
--------
EOS reconstructs the optimizer by reporting completed protocol results to it. Incomplete runs
are removed before execution continues. Beacon also restores its journal, queued insights,
and runtime settings. Explicit resume overrides take precedence over saved settings.

Keep the optimizer domain compatible with completed results. Domain overrides are not accepted
on resume. See :doc:`beacon_optimizer` for Beacon state and :doc:`custom_beacon` for custom optimizers.

Prepare a Protocol for Repeated Runs
------------------------------------
* Make each run independent and leave the lab ready for the next run.
* Declare every device a task interacts with. A robot transfer should request the robot,
  source device, and destination device.
* Add only necessary dependencies. Use :doc:`scheduling` holds when an allocation must survive
  between tasks.
* Use ``run_if`` for conditional branches. Protocol graphs cannot contain loops.
  See :doc:`protocols` for branching and fan-in.

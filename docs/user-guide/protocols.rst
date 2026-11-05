Protocols
=========
A protocol is a directed acyclic graph of tasks. Dependencies set execution order, and
:doc:`references` pass values, devices, resources, and files between tasks.

.. figure:: ../_static/img/protocol-graph.png
   :alt: Example protocol graph
   :align: center

Define a Protocol
-----------------
Place ``protocol.yml`` in ``protocols/<protocol_name>/`` inside an EOS package.
Add ``optimizer.py`` only when the protocol supports :doc:`campaigns` with optimization.

This example uses the multiplication tasks from the bundled example package:

.. code-block:: yaml

    type: optimize_multiplication
    desc: Find parameters whose multiplied value is 1024
    labs: [multiplication_lab]

    tasks:
      - name: mult_1
        type: Multiplication
        devices:
          multiplier:
            lab_name: multiplication_lab
            name: multiplier
        parameters:
          number: eos_dynamic
          factor: eos_dynamic

      - name: mult_2
        type: Multiplication
        dependencies: [mult_1]
        devices:
          multiplier: mult_1.multiplier
        parameters:
          number: mult_1.product
          factor: eos_dynamic

      - name: score_multiplication
        type: Score Multiplication
        dependencies: [mult_1, mult_2]
        devices:
          analyzer:
            lab_name: multiplication_lab
            name: analyzer
        parameters:
          number: mult_1.in_number
          product: mult_2.product

``type`` identifies the protocol definition. Each execution has a separate protocol run name.
``labs`` lists the laboratories available to its tasks.

Task Fields
-----------
* ``name`` identifies the task within this protocol. ``type`` selects a task specification.
* ``dependencies`` lists tasks that must finish first.
* ``parameters`` overrides task defaults. ``eos_dynamic`` requires a value from the submission
  or optimizer before the task can run.
* ``devices`` and ``resources`` assign specific instances, request dynamic allocation, or
  reference an earlier task's allocation. See :doc:`references` and :doc:`scheduling`.
* ``files`` references earlier task output files. See :doc:`tasks`.
* ``duration`` supplies the expected execution time in seconds for scheduling.
* ``timeout`` fails the task if it runs longer than this many seconds. EOS stops the task's process;
  a device command that was in progress may still finish on the device.
* ``retries`` re-runs the task this many times after a failure or timeout, keeping its devices and
  resources. Use it only for tasks that are safe to repeat.

.. code-block:: yaml

    - name: hplc_analysis
      type: HPLC Analysis
      timeout: 1800
      retries: 2

See :doc:`color_mixing` for a larger protocol with device allocation and resource movement.

可视化编辑器中的场景能力
~~~~~~~~~~~~~~~~~~~~~~~~
左侧“场景能力”按实验室和工作站展示已登记操作。选择移液工作站后，可直接添加开门、取瓶、移液等能力；
烧杯机器人与模拟配液展示各自的操作。能力目录可在设备未加载时用于设计，但执行前仍需加载实验室。

加载或重新加载协议时，EOS 会先加载协议所需但尚未加载的实验室，再校验协议。
初始化新的实验室需要超级用户权限；其他管理员仍可加载依赖已就绪的协议。
如果引用的实验室定义不存在，系统会明确列出缺失名称；设备初始化失败时不会把协议标为已加载。
当前 Matterix bridge 一次只能打开一个仿真场景。切换移液站与 Franka 烧杯场景时，
请先确认没有运行中的实验，再卸载原场景对应的实验室，随后加载目标协议；已有协议和历史结果文件会保留。

拖入能力会填写设备、能力编号和默认参数。移液站按添加顺序延续设备引用、先后依赖及工作站占用；
首次操作前添加“实验初始化”，完整七阶段流程以“结束检查（七阶段）”收尾。
这些连接不会验证物理前置条件，也不保证任意能力排列均可执行；具体操作仍由设备后端校验。
测量、质控和评分在模拟配液的“分析与反馈”中，需连接对应数据端口。

能力配置仍保存为标准 EOS 任务；重新打开时根据能力编号恢复中文名称。
“高级任务”保留按底层任务类型添加的入口，适用于尚未登记场景能力的插件。

Simulation Preflight
~~~~~~~~~~~~~~~~~~~~
A protocol run can rehearse on a digital twin before it touches real equipment. Load a twin lab that has devices
with the same names and types as the real lab (for example simulated or Matterix-backed devices), then submit the
run with a ``preflight`` mapping in its metadata. Mark each twin lab with ``simulated: true`` in its ``lab.yml``;
every lab used by the preflight must be simulated. Map every real lab used by the protocol, including protocols
that use more than one lab. Empty or malformed mappings are rejected before execution:

.. code-block:: bash

    curl -X POST http://localhost:8070/api/protocols/ -H 'Content-Type: application/json' -d '{
      "name": "assay_42", "type": "my_assay", "owner": "me",
      "parameters": {},
      "meta": {"preflight": {"real_lab": "real_lab_twin"}}
    }'

EOS first runs ``assay_42__preflight``: the same protocol and parameters, with every device of ``real_lab`` replaced by
the device of the same name in ``real_lab_twin`` (dynamic device requests are limited to the twin labs). Only if the
preflight completes does EOS start ``assay_42`` on the real lab. If the preflight fails or is cancelled, the real run
is not started; cancelling ``assay_42`` while it waits also cancels its preflight. The runs record each other in
``meta`` (``preflight_for`` and ``preflight_run``). The example package includes ``multiplication_lab_twin`` for
trying this with ``optimize_multiplication``.

Limitations: static resource assignments are not remapped, a run waiting for its preflight is lost if the orchestrator
restarts before the preflight finishes, and EOS does not treat a twin lab as in use by preflight runs when unloading
labs.

Conditionals (run_if)
~~~~~~~~~~~~~~~~~~~~~~~
Add a ``run_if`` field to a task to make it conditional.
The task runs only if the expression evaluates to ``True``, otherwise it is skipped.

.. code-block:: yaml

    - name: reanalyze
      type: Analyze Color
      run_if: score_color.loss > 0.2
      dependencies: [score_color]

Expressions reference earlier task outputs as ``task_name.output_name``.
Referenced tasks must be ancestors of the conditional task.

Supported syntax:

* Comparisons: ``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``
* Boolean operators: ``and``, ``or``, ``not``
* Literals: ints, floats, quoted strings, ``True`` / ``False``

.. code-block:: yaml

    run_if: prep.product >= 0 and prep.product <= 100
    run_if: not calibration.passed
    run_if: mode.setting == 'high' or sample.count >= 3

Expressions must return a boolean and are validated at load time.
Negative numbers are allowed. Binary arithmetic, function calls, indexing, and chained attribute access are not.

Skips propagate: a task is skipped if all of its dependencies were skipped, or (for a conditional task) if any task its
``run_if`` references was skipped.
A task with at least one non-skipped dependency still runs, enabling fan-in convergence across conditional branches.

**Branching and fan-in**: give branches complementary conditions so exactly one runs, then converge on a task depending
on both. To read the output of whichever branch ran, set a parameter to a **fan-in list** of references:

.. code-block:: yaml

    - name: measure
      type: Measure
      dependencies: []

    - name: heat
      type: Heat
      run_if: measure.temperature < 50
      dependencies: [measure]

    - name: cool
      type: Cool
      run_if: measure.temperature >= 50
      dependencies: [measure]

    - name: report
      type: Report
      dependencies: [heat, cool]
      parameters:
        reading: [heat.result, cool.result]  # fan-in: value of whichever branch ran

Each fan-in reference must point to an ancestor, and exactly one branch must run, so make the branch conditions mutually
exclusive.


Optimization
------------
Define ``eos_create_campaign_optimizer()`` in ``optimizer.py`` to return constructor arguments
and an optimizer class. See :doc:`optimizers` for the interface, :doc:`beacon_optimizer` for
hybrid optimization, and :doc:`custom_beacon` to replace Beacon's default optimizer.

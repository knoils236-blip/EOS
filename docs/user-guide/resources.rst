Resources
=========
Resources provide exclusive access to labware, tools, and locations such as beakers, tip racks,
and bench slots. Use a :doc:`device <devices>` when the object needs a persistent process with methods.

Defining resources in laboratories
----------------------------------
Resources are defined per laboratory in ``lab.yml`` using two sections:

- ``resource_types``: declares reusable types and their default metadata (e.g., capacity, geometry).
- ``resources``: declares concrete, globally unique resource instances of a given type.

:bdg-primary:`lab.yml`

.. code-block:: yaml

    name: small_lab
    desc: A small laboratory

    devices:
      magnetic_mixer:
        type: magnetic_mixer
        computer: eos_computer

    resource_types:
      beaker_500:
        meta:
          capacity_ml: 500
      p200_tips:
        meta:
          count: 96
      bench_slot:
        meta:
          footprint: 127x85

    resources:
      BEAKER_A:
        type: beaker_500
        meta:
          location: shelf_1
      BEAKER_B:
        type: beaker_500
      TIPS_RACK_1:
        type: p200_tips
      SLOT_1:
        type: bench_slot

Notes
"""""
- Resource names must be globally unique across all labs. EOS enforces this at load time.
- Instance ``meta`` overrides any defaults from the corresponding ``resource_types`` entry.

Declaring resources in tasks
----------------------------
Tasks declare the resource types they need with ``Resource`` subclasses. EOS validates that protocols supply
matching resource instances (by name) or a dynamic request for the required type.

:bdg-primary:`resources.py`

.. code-block:: python

    from eos import Resource


    class Beaker500(Resource, type="beaker_500"): ...

:bdg-primary:`task.py`

.. code-block:: python

    from eos import task

    from my_package.devices.magnetic_mixer.device import MagneticMixer
    from my_package.resources import Beaker500


    @task("Magnetic Mixing")
    async def magnetic_mixing(mixer: MagneticMixer, beaker: Beaker500) -> None:
        """Mix contents in a beaker."""
        beaker.meta["mixed"] = True

Input resources are also the task's output resources. Changes to their metadata are saved when the task
completes.

Assigning resources in protocols
----------------------------------
In protocol tasks, assign specific resource names or request resources dynamically by type. The scheduler (Greedy or CP-SAT) resolves dynamic requests to a concrete, non-conflicting instance.

:bdg-primary:`protocol.yml`

.. code-block:: yaml

    type: dynamic_resource_protocol
    desc: Demonstrate resource assignment
    labs: [small_lab]

    tasks:
      - name: prepare
        type: Magnetic Mixing
        duration: 60
        devices:
          mixer:
            lab_name: small_lab
            name: magnetic_mixer
        # Specific resource by name
        resources:
          beaker: BEAKER_A

      - name: process_batch
        type: Magnetic Mixing
        duration: 120
        devices:
          mixer: prepare.mixer
        # Dynamically allocate a beaker of the required type
        resources:
          beaker:
            allocation_type: dynamic
            resource_type: beaker_500
        dependencies: [prepare]

      - name: analyze
        type: Magnetic Mixing
        duration: 30
        devices:
          mixer: process_batch.mixer
        # Reuse the same instance selected for 'process_batch'
        # (when a task outputs a resource, it can be referenced by name)
        resources:
          beaker: process_batch.beaker
        dependencies: [process_batch]

.. tip::
   Dynamic resource requests select a single matching resource instance.

Protocol‑level resource metadata (optional)
--------------------------------------------
You may attach protocol-specific metadata to resources via the top-level ``resources`` block. This annotates existing resource instances. It does not define new ones.

:bdg-primary:`protocol.yml`

.. code-block:: yaml

    type: water_purification
    desc: Evaporate sample in a beaker
    labs: [small_lab]

    resources:
      BEAKER_A:
        meta:
          substance: salt_water

    tasks:
      - name: mixing
        type: Magnetic Mixing
        devices:
          mixer:
            lab_name: small_lab
            name: magnetic_mixer
        resources:
          beaker: BEAKER_A

Allocation and exclusivity
--------------------------
- EOS allocates resources exclusively to the task that holds them. Conflicting tasks wait until resources are free.
- Specific assignments must name an existing resource instance defined in one of the protocol’s labs.
- Dynamic assignments select from the pool of eligible instances by ``resource_type``.
- Allocation is handled automatically by the orchestrator and released when the task (or its request scope) finishes.

消耗品库存
----------
资源的 ``meta`` 设置 ``consumable: true``、非负整数 ``quantity``、``unit``、
``label``、``low_stock_threshold`` 和 ``source`` 即可显示库存。资源页面默认每 5 秒刷新，
可按实验室筛选；低于或等于阈值时显示低库存。提交者及以上权限可在详情中补充正整数数量。
耗材不能通过“重置资源”恢复数量，卸载及重新加载实验室也保留现有库存。

``matterix_pipetting_lab`` 提供 TIP 头 96 个、注射器 20 个、小瓶 24 个的示例初始库存。
这些数字是演示配置，不是场景中可用槽位数或真实设备测量。仿真会话每个场景对象首次出现
``grasp`` 事件后扣减一次；重复抓取和重复上报不会再次扣减。任务结束（包括失败和取消）
时处理已观察到的使用事件，不因提交协议直接扣减。场景重置产生新的一组演示耗材使用。

真实设备可在 ``finalize_task`` 返回 ``inventory_events``，或由具有提交权限的客户端调用
``POST /api/resources/inventory-events``，例如：

.. code-block:: json

    {"resource_name": "TIPS_RACK_1", "event_id": "device-operation-unique-id",
     "delta": -1, "source": "hardware", "note": "TIP 已装载"}

同一资源的事件编号必须在设备重启、重试后保持稳定且唯一。服务端在同一数据库事务中锁定库存、
更新数量并保存流水，拒绝负库存和同一编号的不同内容。真实设备必须报告实际使用事件，
不能按协议成功与否推测耗用。当前没有库存预留、任务执行前缺料拦截或硬件数量校准。

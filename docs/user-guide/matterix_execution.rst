Matterix 场景执行与验收
=======================

本文对应当前 checkout 中的 Matterix 场景升级接入。它说明如何在 EOS 中提交原子工作站能力、理解录像和观测数据，
以及哪些结果不能被当作化学测量或真机验收。设计依据是 Darvish 等人的 MATTERIX 论文，DOI
``10.1038/s43588-025-00924-4``：EOS 负责协议编排、设备占用和执行记录，Matterix 负责场景内技能、运动与语义状态。

.. note::

   下列 2026-11-03 证据来自旧运动学入口，不能作为本次物理路径修改的验收。
   当时已在本机 Isaac Sim 上通过下述 13 任务原子流程，并捕获运行中的真实观测。
   Franka 的六步重复升降完整循环也已通过。视频经逐帧解码和画面检查，浏览器完成五个页签、原生播放与文件预览验证。
   证据索引为 ``evidence/matterix-upgrade-20261103/README.md``。21 个新能力并未全部逐一完成连续仿真验收；
   不应把已测子集的通过结论扩展到所有组合或真实硬件。

场景和适用范围
--------------

``scene_id: pipetting_realtime`` 保留原有标识，当前入口改用 ``Matterix-PipettingStation-Twin-v1``
的 250 Hz GPU PhysX 路径。工作区碰撞阻挡应导致动作失败，不回退到运动学模式。原始资产保持不变；
新的连续物理工作流尚未全部通过验收，历史运动学视频不能证明碰撞或抓取正确，真实硬件也未标定。

为减少整站自碰撞开销，当前会话在内存中使用工作区碰撞配置：外壳和门板不参与碰撞；
保留已注册流程使用的 TIP 1、2（资产中的 85、73 号 TIP）及对应槽孔、TIP 盒承载结构、
台面、导轨、夹爪、瓶和针筒的碰撞。其余 94 个闲置 TIP 保持在 CAD 位置供显示，不参与动力学碰撞。
物理步长、工作区抓取和移液验收阈值不变；录像中的隐藏外壳选项仍只控制显示。
此配置不能用于验证外壳或安全门干涉，也不能证明闲置 TIP 槽位的碰撞正确性。
实际关闭的碰撞体数量与工作 TIP 名称记录在会话 ``collision_profile`` 和 provenance 中。

当前 ``station_catalog.json`` 为这个场景登记了 22 个原子能力（L2 技能）：

- ``station_skill_mount_tip``、``station_skill_eject_tip``
- ``station_skill_aspirate_from``、``station_skill_dispense_to``
- ``station_skill_small_vial_pick``、``station_skill_small_vial_place``、
  ``station_skill_small_vial_decap``、``station_skill_small_vial_recap``、
  ``station_skill_small_cap_home``
- ``station_skill_big_bottle_pick``、``station_skill_big_bottle_place``、
  ``station_skill_big_bottle_decap``、``station_skill_big_bottle_recap``
- ``station_skill_syringe_pick``、``station_skill_filter_head_mount``、
  ``station_skill_syringe_aspirate``、``station_skill_syringe_dispense``、
  ``station_skill_syringe_discard``
- ``station_skill_pipette_transfer``（源包 ``PipetteTransferCfg``：装 TIP、吸、吐、弃 TIP）、
  ``station_skill_syringe_filter_transfer``（源包 ``SyringeFilterTransferCfg``）、
  ``station_skill_front_door``（源包 ``FrontDoorCfg``，``open_m`` 为 0–0.39 m）
- ``station_skill_wait``

另有源包 ``PipettingStation_20261031`` 的 7 条注册工作流中此前未接入的 3 条，作为操作提供：
``station_take_drop_tip``、``station_small_cap_pick_place``、``station_big_cap_pick_place``。
它们的验证证据是源包 ``out/workflow_validation_20261031/registered.json`` 的逐条运动学运行
（每条前重置场景），不是物理验收。

这些是当前移液站的能力，不是 EOS 所有实验室的通用能力，也不能从 ``matterix_lab``、
``mixture_mock_lab`` 或 Franka 烧杯场景反推。``scene_id``、能力 ID 和参数必须同时匹配。

容器槽位和吸吐体积目前也是固定导出口径：

- 除 TIP 外，``index`` 只能为 ``1``；``skill_mount_tip`` 的 ``tip_index`` 只能为 ``1`` 或 ``2``。
- 吸液和吐液体积范围为 ``20–180 µL``，协议中必须显式写入有效体积。
- 区域来自真实导出名称：``中瓶区1``、``中瓶区2``、``大开盖区``、``大瓶区``、``小开盖区``、``小瓶区``。
  ``小瓶区`` 对应源瓶，``中瓶区1`` 对应目标瓶，``中瓶区2`` 对应反应器。
- ``volume_ul`` 是仿真语义账本中的指令值；它不证明真实移液器计量精度。

原子针筒吐液 ``station_skill_syringe_dispense`` 与源包程序 33 一致，固定向 ``小瓶区`` 10 mL 转移瓶吐液。
此前 EOS 自加的 ``recipient`` 参数（在运行时改写宏坐标指向其他容器）源包不支持，已下线。

吸吐量验收与源包 ``scripts/run_full_workflow.py`` 一致：实际转移量与请求体积相差不超过 5 µL，
吸入空气不超过 5 µL，不允许洒漏；账本总量守恒与工具残液仍按 1e-6 mL 检查。物理模式下活塞在控制器
5 µL 到位容差内停止，例如请求 50 µL 时实测转移约 49.66 µL。

原有七阶段中的小瓶移液、大瓶自吸自吐和注射器过滤转移同样支持 ``volume_ul``，不再写死体积。
默认分别为 100、100、50 µL，可在任务属性中分别修改；节点标题随当前体积更新。
2026-11-03 已实际运行七任务协议，以 75 µL 完成小瓶转移，并核对统一录像、完整协议结果与浏览器播放；
对应证据为 ``evidence/matterix-protocol-scope-20261103/README.md``。
执行构造器、源液量前置条件和液量守恒验收均使用本次请求值。其他能力只开放当前场景实际支持的参数，
如 TIP 槽位、目标区域、保持时间和等待时长；不向用户暴露未经标定的任意关节目标。

协议、设备所有权和结束条件
--------------------------

原子流程应让工作站从 ``begin`` 一直保持到 ``finish``。典型代表流程是：

``begin`` → 取小瓶 → 开盖 → 合盖 → 放回小瓶 → 小瓶盖头归位 →
装载 1 号 TIP → 从 ``小瓶区`` 吸 ``50 µL`` → 向 ``中瓶区1`` 吐 ``50 µL`` →
弃 TIP → ``Matterix End Experiment``。

每一步都应引用前一步的设备，并继续持有该设备。下面片段展示这种所有权链：

.. code-block:: yaml

   - name: mount_tip
     type: Matterix Capability
     timeout: 480
     retries: 0
     parameters:
       capability_id: station_skill_mount_tip
       parameters:
         tip_index: 1
     dependencies: [small_cap_home]
     devices:
       matterix:
         ref: small_cap_home.matterix
         hold: true

   - name: finish
     type: Matterix End Experiment
     timeout: 60
     parameters:
       expected_phase: 0
     dependencies: [eject_tip]
     devices:
       matterix:
         ref: eject_tip.matterix
         hold: false

``begin`` 第一次指定 ``matterix_pipetting_lab/matterix``；后续任务使用 ``ref``，并在 ``hold: true`` 下串行执行。
结束检查使用 ``hold: false``。不要在原子操作之间释放并重新选择同一个工作站，否则后续能力可能失去实验会话。

``expected_phase: 0`` 只适用于这种原子操作生命周期测试。它要求本协议至少执行过一个能力、每个能力均为 succeeded
且验收通过、会话没有活动任务，并且操作状态为 accepted 或 idle。它不要求七阶段实验完成，也不声称七个阶段通过。
结束还要求带盖容器关闭、TIP 与针筒从机械工具释放，并具有已用耗材的弃置事件。
弃置后的过滤头可以继续连接在已释放的针筒上。未满足这些条件时保留实验所有权，修正收尾后再结束。
七阶段完整流程才可检查 ``expected_phase: 7``；不要用 ``0`` 的结果替代 ``7``。

录像、观测结果和五个界面部分
----------------------------

在“提交协议运行”窗口统一选择“录制整个协议视频”和“隐藏移液站外壳”。新运行默认关闭，
该选择显式覆盖旧协议每个任务中保存的录像设置；编辑器的能力属性不再要求逐个开启录像。
程序调用可通过 Protocol Run metadata 提交相同设置：

.. code-block:: python

   ProtocolRunSubmission(
       name=run_name,
       type=protocol_type,
       meta={"matterix": {"record_video": True, "hide_shell": True}},
   )

EOS 会把这两个布尔值转发到每个原子任务，最终必须能够在实际 bridge ``request.json`` 的 ``parameters`` 中看到
``record_video: true`` 和 ``hide_shell: true``。提交参数与生效参数的快照都应保留下来。

Matterix 协议运行页默认进入“协议总览”，五个部分覆盖整个运行；流程图和单任务详情保留为下钻入口：

1. ``观测曲线``：运行期间汇总所有已执行任务的真实语义样本，完成后读取归档 ``samples.jsonl``；
   采用协议时间轴，保留任务、场景和会话标识，按单位区分平移与旋转。不同会话不会合成同一容器或关节系列。
   没有样本时不会补造曲线；长运行有界抽样，完整原始文件仍可下载。
2. ``参数快照``：逐任务并列显示实际提交参数与 bridge 生效请求，另显示协议 metadata；包含默认值，不限于手动覆盖值。
3. ``验收详情``：逐任务显示执行状态、真实检查项、实际值和期望值。初始化和结束检查保留各自验收，缺失记录不能视为通过。
4. ``视频``：协议结束后自动按实际执行顺序把全部已录制步骤合成为一个 ``protocol-simulation.mp4``，
   不包含任务间调度等待的空白时间。失败或取消只能生成已执行部分，明确标为不完整；合成失败显示具体录像错误。
5. ``下载与原始数据``：按来源任务区分 ``request.json``、``result.json``、``samples.jsonl``、``provenance.json``、
   证据 ZIP 和原始片段；另外提供协议视频及 ``recording.json`` 清单。单步骤 ZIP 不表示整个协议证据包。

录像会增加渲染时间。验收时应同时检查录像文件真实存在且非空、采样序列非空、单位字段存在、请求参数与 EOS
快照一致。只看到 ``simulation.mp4`` 文件名、只看到黑帧，或视频文件与本次 request/session 不对应，都不能算通过。

移液站录制采用整站总览与当前工具近景并排的双视角。近景按工具实测变换跟随，包括夹取耗材前的接近过程。隐藏外壳仅临时改变可视属性，结束时恢复；不会删除刚体或碰撞体。
当前提供“隐藏外壳”选项，未提供透明度滑条。任务视频输出为 25 fps、1920×540（左侧整站、右侧随工具移动的近景）；完整协议拼接保留相同尺寸；原始任务片段保留供复核。

``hide_shell`` 是移液站录像选项。Franka 烧杯场景当前没有对应的外壳隐藏实现，bridge 在非
``pipetting_realtime`` 场景会移除该参数；不要据提交值声称 Franka 画面已隐藏外壳。

Franka 烧杯和物理边界
--------------------

Franka 烧杯是独立的 ``franka_beaker`` 场景，控制链路包含机器人姿态、碰撞、抓取、接触和释放，不能把
``pipetting_realtime`` 的 kinematic 成功结果当作 Franka 物理验收。

烧杯工作流拆成抓取、抬升、放下和松开四个动作。同一抓取周期内，``lifted_m`` 是累计抬升量，下降不会返还抬升额度；
``lifted_m`` 不得超过 ``0.58 m``，``lowered_m`` 不得超过 ``lifted_m``。因此“抬 0.30 m、降 0.10 m、再抬 0.30 m”
会因累计抬升 ``0.60 m`` 被拒绝。``0.58 m`` 是当前固定场景、完整抓取/升降/放回/松开循环的已验证指令上限，
不是 Franka 的通用机械极限，也不能证明其他场景或更高载荷可用。

当前标定记录使用 ``0.02 m`` 到位容差和 ``0.0005 m`` 支撑接触容差；实际高度上界另行严格检查，不增加 2 cm 额度。
验收必须检查实际 beaker 位姿、抬升量、保持时间、
回位、支撑接触和末端运动；IK 有解或单向运动成功不能替代完整循环。放下后未回到基线时也不允许松开。

化学语义限制
------------

移液站的液量、瓶盖、夹持、TIP 和注射器状态来自 Isaac Sim 的语义账本，仿真成功不等于真实化学分析。
配液示例中的浓度测量使用明确标记的 ``synthetic_model`` / ``synthetic_concentration_v1``，即使物料准备经过
Matterix 场景，也不能把合成浓度曲线解释为仪器测量、产率、转化率或真实样品结果。

运行隔离测试
------------

live test 文件是 ``tests/integrations/test_matterix_scene_upgrade_live.py``。它只在
``EOS_TEST_MATTERIX_PORT`` 存在时运行，并实际创建私有的 ``matterix_sim`` 副本、私有 SQLite 数据库、唯一 Ray
namespace 和唯一 Protocol Run 名称。测试不复制自身到项目协议目录，也不启动或重启 bridge。

主验收建议使用独立桥接端口、已有 Ray 服务和指定证据目录：

.. code-block:: shell

   EOS_TEST_MATTERIX_PORT=65202 \
   EOS_MATTERIX_EVIDENCE_DIR="$PWD/evidence/matterix-scene-upgrade-20261103/eos-run" \
   .venv/bin/python -B -m pytest \
     tests/integrations/test_matterix_scene_upgrade_live.py -s -q

测试最多等待 1200 秒。它只读取一个代表任务的完整请求、结果、采样、来源和视频文件，其余能力只回读小型
``request.json``，避免重复下载全部录像。证据目录会保留真实数据库备份、13 个任务的 JSON 摘要、测试协议副本、
一个代表任务的独立 ``request.json``、``result.json``、``samples.jsonl``、``provenance.json``、
``simulation.mp4`` 和 ``_build_observation`` 输出。

测试必须检查观测曲线有实际样本和单位、五个界面部分有对应数据、请求 metadata 为规范布尔值、视频可由文件存储读取，
并确认来源为 ``simulation`` 且执行模式为 ``physics``。历史证据的 ``kinematic`` 标签保留。测试结束在 ``finally`` 中结束设备会话并释放私有工作站；
项目源码和固定场景资产不会被修改。

2026-11-04 物理验收
-------------------

真实 PhysX 初始化、2 秒等待与四个容器的后段静置稳定性检查已执行。前门在本次 8 秒仿真窗口内仅到约
2.835 mm，未达到 390 mm 目标，连续流程停在第一阶段。1920×540、25 fps 双画面已实际解码；
液体动作时的近景跟随、出口/瓶口可见性和新增接收器尚未运行到。原始资产未改动；完整证据见
``evidence/matterix-physics-acceptance-20261104-d12ffa/README.md``。

2026-11-08 前门复测
-------------------

独立 bridge 上复测：``station_skill_front_door`` 开到 0.389959 m、关回约 0 m，
``station_full_station_open_door`` 在 6.11 s 仿真内到位，均在 ±1.5 mm 内。8 秒窗口不是 11-04 失败的原因；
根因未定位。连续七阶段与液体动作未在复测中运行。证据见
``evidence/matterix-front-door-recheck-20261108/README.md``。

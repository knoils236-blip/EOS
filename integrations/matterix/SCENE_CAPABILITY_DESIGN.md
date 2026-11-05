# EOS 场景能力重新设计：按 MATTERIX 的操作与感知技能原理

状态：设计稿（2026-11-04）；第 1 步已完成，见第 6 节，其余未实现。依据 Darvish 等，*MATTERIX: toward a digital twin for robotics-assisted
chemistry laboratory automation*，Nat. Comput. Sci. 6, 67–82 (2026)，DOI `10.1038/s43588-025-00924-4`，
主要读的是 Results 的 “Manipulation and perception skills”、Methods 的 “Perception”、
“Hierarchical state machine task representation” 和 “Deployment to real setup”。

## 1. 论文原理（与本设计相关的部分）

1. **技能是可复用、以目标为条件的（goal-conditioned）。** 例子是 `Pick&Place(obj1, obj2)`：
   技能本身不绑定具体物体，化学家写工作流时才填入物体。
2. **层级状态机。** 工作流是一棵树 T = ⟨N, E⟩，深度代表抽象层次；叶子是原语动作，父节点由子节点组合而成。
   一个节点在其所有子节点处理完后才算执行完；工作流在所有节点执行完且目标达成时完成。
   执行进度按节点显示为“已完成 / 进行中 / 未执行”三种状态。
3. **角色分工。** 机器人工程师维护抽象技能库；化学家只用技能组装自己的工作流。
4. **原语集合。** 关节级控制器、带 IK 的运动规划、抓取/松开、移动机器人全身控制、笛卡尔相对运动、等待、
   带避障的 cuRobo 约束规划，以及一个用来部署 RL/IL 训练策略的 check-point 原语。
5. **感知。** 用 FoundationPose 做基于模型的 6D 位姿估计与跟踪，输入就是孪生里每个资产已有的 USD 模型。
   它的作用有两个：把物体位姿写回数字孪生，免去在机器人工作空间里手工指定位姿和繁琐标定；
   用户定义“拿起某物”这类动作时，抓取位置由估计出的位姿推导。
6. **部署顺序。** 先用真实机器人和设备状态初始化孪生，再用相机观测并估计物体位姿更新孪生，然后由工作流在孪生中
   生成动作、每个仿真步把状态传给实物；力反馈超过阈值即停止执行。
7. **语义状态。** 状态是 y = [x, s, l]：物理状态 x、连续语义状态 s、逻辑语义状态 l。事件和过程带前置条件
   （例如吸液要求 TIP 在液体内）。这正是技能前置条件和效果应该写在其上的词表。
8. **论文自己报告的局限。** 实机烧杯取放 12 次成功 9 次（75%），失败来自位姿估计误差、机器人动作误差、
   相机标定、物体坐标系定义和仿真与现实不匹配。也就是说，感知结果本身必须被当作需要校验的输入。

## 2. EOS 现状（事实，附位置）

**原生侧已经是层级的。** `PipettingStation_20261031/.../actions/skills.py:330` 的 `SkillCfg` 注释写明
L0 原语 / L1 PLC 程序 / L2 技能，`PipetteTransferCfg`（`skills.py:426`）注释直接对应论文 Fig. 4a，
`workflows.py:193` 的 `full_station()` 是由技能组成的工作流。

**到 EOS 边界被压平了。**

- `bridge/capabilities.json` 有 42 条平行条目（移液站 16 个操作 + 19 个原子技能，Franka 7 个）。`tier`（operation / skill / hidden）只是编辑器分组，不是父子关系。
  七阶段 `full_station_*` 和 `mixture_*` 这些“操作”与 19 个原子技能互不引用。
- 同一技能的知识在 bridge 里写了多份：
  - 构造：`station_skills.py:447` `build_workflow` 的 if/elif；
  - 执行前检查：`station_skills.py:1135` `precondition_checks`；
  - 执行后验收：`station_skills.py:1053` `evaluate_postconditions`；
  - 静态计划检查：`plan_check.py` 里的 `CAP_EFFECTS`、`TIP_BY_WORKFLOW`、`TIP_CHECKED`、`EMPTY_ZONES`
    和一套手写的符号状态推演；
  - 七阶段操作另有 `station_experiment.py:62` `preconditions` 与 `:162` `evaluate`。
  新增一个技能至少要改这几处，加上 `capabilities.json`、`station_catalog.json` 和配置哈希清单。
- **技能按物体命名，不是目标条件化的。** `small_vial_pick`、`big_bottle_pick`、`syringe_pick` 本质都是 pick(物体)；
  decap/recap、aspirate/dispense 同理。`index` 固定为 1，区域名直接是 PLC 程序名。
- **两个场景两套表示。** `franka_beaker` 用 `session_workflows.yaml` 的原语序列（pick / move_to_pose / open /
  relative / wait）加 `session_actions.py`；`pipetting_realtime` 用原生 Cfg 加 `station_skills.py`。场景集合在多处硬编码：
  `common.py:15` `SUPPORTED_SCENE_IDS`、`capabilities.py` 的 `_SCENE_WORKFLOW_FILES`、
  `user/matterix_sim/editor.py:52` 的场景分支，以及 `plan_check.check_plan` 对非移液站直接返回 `[]`
  （Franka 场景没有任何静态检查）。
- **没有感知。** 物体位姿来自固定导出坐标；`Begin Experiment` 只把快照和 `station_initial_state.json` 的固定初态比较
  （`station_experiment.py:115`）。`eos/protocols/preflight.py` 的“先孪生后真机”只是把同一协议映射到孪生实验室，
  孪生的初态并不来自真实现场。

## 3. 设计

### 3.1 一个场景 = 物体表 + 语义谓词 + 技能库

每个场景由一份 `scenes/<scene_id>.yaml` 描述，bridge 启动时加载，取代 `SUPPORTED_SCENE_IDS`、
`_SCENE_WORKFLOW_FILES` 和编辑器里的场景分支：

```yaml
scene_id: pipetting_realtime
task: Matterix-PipettingStation-Twin-v1
objects:                       # 化学家能引用的东西
  small_vial:   {kind: vial,  volume_ml: 2,   capped: true, slot: 出样区1/1, semantic_cap: SmallCapOpen}
  big_bottle:   {kind: bottle, volume_ml: 500, capped: true, slot: 大瓶区/1,  semantic_cap: BigCapOpen}
  source_vial:  {kind: vial,  volume_ml: 10,  zone: 小瓶区}
  target_vial:  {kind: vial,  volume_ml: 50,  zone: 中瓶区1}
  reactor:      {kind: reactor, zone: 中瓶区2, reachable_by: [syringe]}
  tip_1:        {kind: tip, consumable: true, slot: TIP/1}
  tip_2:        {kind: tip, consumable: true, slot: TIP/2}
  syringe:      {kind: syringe, consumable: true, slot: 过滤机构/1}
tools: [pipette, syringe_holder, small_cap_gripper, big_cap_gripper]
predicates:                    # 从快照派生，名字就是现有语义键
  tip_on: semantic.TipOn
  cap_open(c): semantic[c.semantic_cap] and not device.cap_closed[c]
  holding(o): attached(o)
  volume_ml(o): liquid[o]
  used(o): event_seen(grasp, o)
skills: [...]                  # 见 3.2
```

物体表就是论文里“化学家填对象”的那一半；谓词就是语义状态 l(t)、s(t) 的命名视图，值仍由现有
`station_semantics.py` 从 Isaac 快照计算，这一层不重写。

### 3.2 技能只定义一次：签名、前置条件、效果、验收、子节点

```yaml
- id: pick
  args: {object: {kind: [vial, bottle]}}
  pre:    [not holding(object), pose_known(object)]
  effect: [holding(object)]
  verify: [event(grasp, object), holding(object), jaw_closed(gripper_of(object))]
  bind:                                   # 物体 → 原生构造器，机器人工程师维护
    small_vial: SmallVialPickCfg
    big_bottle: {cfg: BigBottlePickCfg, fixed: {jaws: {big_cap_grip: 0.012, pg1: 0.018}}}

- id: aspirate
  args: {tool: [pipette, syringe], source: {kind: [vial, bottle, reactor]}, volume_ul: [20, 180]}
  pre:    [tool_loaded(tool), cap_open(source) if source.capped, volume_ml(source) >= volume_ul/1000]
  effect: [volume_ml(source) -= volume_ul/1000, tool_volume(tool) += volume_ul]
  verify: [command(aspirate), conserved(source, tool)]
  bind: {pipette: AspirateFromCfg, syringe: SyringeAspirateCfg}

- id: pipette_transfer                    # L3 操作：只有子节点，没有自己的执行代码
  args: {source: ..., target: ..., volume_ul: ..., tip: {kind: tip}}
  children:
    - mount(pipette, tip)
    - aspirate(pipette, source, volume_ul)
    - dispense(pipette, target, volume_ul)
    - discard(pipette)
```

规则：

- `pre` / `effect` / `verify` 是同一套谓词上的数据。运行时前置检查、运行后验收和静态计划检查都由它们求值，
  不再各写一份。`verify` 仍保留事件检查（“本次任务出现 grasp 事件”），这是当前验收里最有价值的部分。
- 有 `children` 的节点不需要 `bind`。它的前置条件按子节点顺序用 `effect` 推演得到，验收是子节点验收的合取，
  再加上节点自己声明的目标。七阶段整站和配液操作都改写成这种组合，`station_experiment.py` 的重复检查删掉。
- `bind` 指向 Matterix 原生 Cfg，原生代码不改。`index` 仍只允许 1，直到 3.4 的感知链路按槽位逐个验收。
- 原语层沿用论文第 4 点的分类；对 EOS 有意义的新增只有 `policy_checkpoint` 叶子（加载训练好的策略），
  其余原语已经在 Matterix 原生侧或 Franka 的 YAML 原语里存在。

层级如下，EOS 任务可以指向 L2 或 L3 节点：

| 层 | 内容 | 维护者 |
|---|---|---|
| L0 原语 | IK/关节控制、抓取/松开、相对运动、等待、策略 checkpoint | 机器人工程师（Matterix） |
| L1 设备程序 | PLC 程序（装小TIP头、去某区吸液…） | 机器人工程师（Matterix） |
| L2 技能 | pick / place / decap / recap / mount / aspirate / dispense / discard / observe / wait / door | 机器人工程师（scene.yaml） |
| L3 操作 | pipette_transfer、syringe_filter_transfer、prepare_vial、七阶段各阶段 | 机器人工程师或化学家 |
| L4 协议 | EOS protocol.yml，任务引用 L2/L3 并填物体 | 化学家 |

执行时 bridge 按节点路径回传进度（completed / running / pending），EOS 协议页可以像论文 Fig. 4a 那样显示
任务内部的树，而不只是一个任务一个状态。

### 3.3 目标条件化后的技能库

现有 19 个原子技能合并为约 10 个通用技能乘以物体绑定：

| 通用技能 | 取代的现有能力 |
|---|---|
| `pick(object)` | `station_skill_small_vial_pick`、`station_skill_big_bottle_pick`、`station_skill_syringe_pick`、Franka `grasp_beaker` |
| `place(object, slot)` | `station_skill_small_vial_place`、`station_skill_big_bottle_place`、Franka `release_beaker`/`return_beaker` |
| `decap(container)` / `recap(container)` | 小瓶、大瓶各两个 |
| `mount(tool, consumable)` | `station_skill_mount_tip`、`station_skill_filter_head_mount` |
| `aspirate(tool, source, volume_ul)` | `station_skill_aspirate_from`、`station_skill_syringe_aspirate` |
| `dispense(tool, target, volume_ul)` | `station_skill_dispense_to`、`station_skill_syringe_dispense`（`recipient` 变成 `target`） |
| `discard(tool)` | `station_skill_eject_tip`、`station_skill_syringe_discard` |
| `move(object, dz)` | Franka `lift_beaker`/`lower_beaker`（累计抬升 0.58 m 的约束写成 `pre`） |
| `home(axis)`、`door(open_m)`、`wait(s)` | `station_skill_small_cap_home`、`FrontDoorCfg`、`station_skill_wait` |
| `observe(objects)` | 新增，见 3.4 |

不支持的组合由 `args` 的 kind 约束和 `bind` 缺项直接拒绝，例如 `aspirate(pipette, reactor)`
（现有 `UNREACHABLE_PIPETTE_ZONES = ("中瓶区2",)` 的规则变成 `reactor.reachable_by: [syringe]`）。

### 3.4 感知技能

新增 L2 技能 `observe(objects)`，输出每个物体的位姿、置信度和来源，写回孪生，并提供谓词：

- `pose_known(o)`：本次实验内观测过，置信度高于阈值，位姿落在该物体槽位的已标定容差内；
- `at(o, slot)`：位姿与槽位的偏差小于容差；
- `present(o)`、`fresh(o)`：耗材存在且未用过。

来源分三种，按部署阶段启用：

| 来源 | 用途 | 说明 |
|---|---|---|
| `sim_truth` | 当前仿真 | 直接读 Isaac 中物体位姿，验证整条“观测 → 写回 → 前置条件”链路 |
| `sim_camera` | 仿真中测感知 | 渲染相机图像送 FoundationPose，用场景资产的 USD，和 `sim_truth` 比较得到误差分布 |
| `camera` | 真机 / 预检 | 真实相机 + FoundationPose，结果写进孪生 |

接入点：

1. **`Matterix Begin Experiment`** 从“快照等于固定初态”改为：同步设备状态 → `observe(全部物体)` →
   检查约束（每个物体在可达槽位、带盖容器闭合、耗材未用、液量在范围内）。固定初态文件降级为仿真重置用的种子。
2. **抓取类技能的前置条件**加 `pose_known(object)`；抓取位姿由观测位姿加资产里的抓取/操作帧推导
   （资产已由 matterix-asset-builder 定义操作帧）。在 PLC 治具场景里，前期只用它做校验，执行仍走固定程序坐标；
   位姿驱动的抓取先在 Franka 场景启用，那里本来就是 IK 控制。
3. **执行中复核**：`pick` 的验收加 `holding`，`place` 的验收加 `at(object, slot)`，用一次轻量 `observe` 完成，
   发现偏差就让任务失败，而不是让下一步在错误状态上执行。
4. **安全中止条件**：每个技能可声明 `abort`（如 `spilled_ml > 0`、接触力超阈值、意外碰撞），在仿真步中监测，
   命中即终止，对应论文的力反馈安全机制。
5. **`preflight.py`**：真机运行前先在真机执行 `observe`，把结果作为孪生预检的初态，预检通过后再跑真机。
   这才是论文意义上的数字孪生预检，现在的预检只是同一协议在孪生实验室上换设备跑一遍。

### 3.5 单一事实来源带来的直接收益

- `plan_check.py` 变成通用的符号推演：按顺序对每一步求 `pre`，再应用 `effect`。Franka 场景自动获得静态检查；
  `CAP_EFFECTS`、`TIP_BY_WORKFLOW`、`EMPTY_ZONES` 这些手写表删除。
- 编辑器预设由 scene.yaml 生成，删除 `editor.py:52` 的场景分支；“实验边界”预设也写进 scene.yaml。
- `eos/protocols/drafting.py` 给模型的能力描述直接用技能签名和 `pre`/`effect`。论文讨论部分提到用 LLM 基于技能库
  生成层级计划，这里的前提就是技能有机器可读的前置条件和效果。
- 新增一个技能只改 scene.yaml（必要时加一个原生 Cfg 绑定），配置哈希仍覆盖 scene.yaml。

### 3.6 职责边界不变

EOS 负责协议编排、设备占用、执行记录和证据归档；bridge 负责场景定义、技能求值与执行；Matterix 原生 Cfg 不改。
SiLA 接口仍是“提交能力 ID 加参数”，只是能力 ID 变成通用技能名，参数里多出物体引用。

## 4. 迁移顺序

每一步都以现有测试作回归（`tests/integrations/test_station_skills.py`、`test_plan_check.py`、
`test_editor_scenes.py`），行为不变才进入下一步。

1. **数据化，不改行为。** 把 `station_skills.py` 里 19 个技能的前置、验收写成 scene.yaml 的 `pre`/`verify`，
   用一个求值器替换三处 if/elif；`plan_check` 改用 `effect` 推演。能力 ID 暂时不变。
2. **操作改为组合。** 七阶段和配液操作写成 `children`，删除 `station_experiment.py` 的重复检查，回传节点进度。
3. **通用技能 + 物体绑定。** 能力 ID 改名。`user/matterix_sim/protocols` 下 `ai_pip_a/b/c`、`test_2` 引用了
   `station_skill_*`，需要一次性改写；历史 evidence 中的旧 ID 保持原样，不改。
4. **`observe` 用 `sim_truth`。** 改造 `Begin Experiment` 和抓取类前置条件、放置后复核。
5. **Franka 场景迁入同一格式。** `session_workflows.yaml` 的原语序列直接成为技能的 `children`。
6. **`sim_camera` / `camera` + FoundationPose。** 先在仿真中量误差，再按槽位逐个物理验收后开放 `index > 1`，最后接真机。

## 5. 风险和尚未确认的事

- 移液站新的连续 PhysX 流程尚未通过验收（前门 8 秒窗口内只到约 2.835 mm），第 1–3 步只能证明逻辑等价，
  不能证明物理执行。
- 感知的收益在这台 PLC 治具工作站上主要是校验，而不是改善抓取。这是根据物体都在固定治具中做的推断，没有数据支持；
  在 Franka 这类 IK 场景里收益更直接。
- 论文实机取放成功率 75%，失败原因里有位姿估计和相机标定。所以 `pose_known` 必须带置信度和容差，
  感知失败要让任务失败，不能退回到固定坐标继续执行。
- 物体绑定依赖固定导出 `PipettingStation_20261031` 里的 Cfg 类名，导出更新时要一起核对。
- 第 3 步改能力 ID 会让旧协议失效，是否保留旧 ID 作为别名需要你决定；本设计默认一次性迁移、不留别名。

## 6. 第 1 步实施记录（2026-11-04）

**做了什么。** 19 个原子技能的构造、执行前检查、计划检查效果和执行后验收集中到 `bridge/station_skills.py` 的
`RULES` 表。每个技能写有 `pre`（检查名、谓词、计划提示）、`effect`（`set` / `use` / `transfer`）和 `verify`。
原来的三处 if/elif 换成三个求值器：`build_workflow` 用 `CFG_FIELDS` 把请求参数映射到原生构造器字段，
`precondition_checks` 在 `SnapshotState` 上求值 `pre`，`evaluate_postconditions` 调用 `verify`。
`plan_check.py` 的原子技能部分改为在 `_PlanState` 上求值同一组 `pre`、再应用 `effect`，
计划时还不知道的值抛 `Unknown`，对应检查跳过。能力 ID、`capabilities.json`、`station_catalog.json` 没有改动。

**和设计稿的偏差。** 规则写在 Python 表里，没有写成 `scene.yaml`：前置条件含容差、单位和两种报告口径，
写成 YAML 需要一套表达式解释器，眼下只有一个场景用得上。等第 5 步 Franka 场景迁入时再决定是否抽成文件。
组合操作（七阶段、配液、`filter_take_syringe` 等）仍由 `plan_check.py` 里的表描述，留给第 2 步。

**行为验证。**

- 构造：用 Matterix 环境对 19 个技能的默认参数和边界参数（共 38 组）导出原生工作流树，新旧完全一致。
- 运行时前置检查与验收：在 15200 组随机快照和参数上（含缺字段、非数值等异常快照）新旧输出逐字一致。
- 受影响测试：`tests/integrations` 中引用 bridge 的测试 209 passed、3 skipped（跳过项需要 Isaac Lab 或实时 bridge）；
  另加 `test_container_skills_share_the_runtime_preconditions`。
- 计划检查（4000 组随机步骤序列，新旧对比）有意改变的地方：
  - 新增小瓶 / 大瓶的取、放、开盖、合盖前置检查（是否夹持、盖子开合），与运行时前置条件一致，以前计划检查不查；
  - 组合操作写明结束后的夹持状态（取自 `station_worker._build_workflow` 的实际子序列）：
    `full_station_open_bottle` 结束时仍夹着大瓶，结束检查会报 held tools，运行时的 `attached tools released`
    同样会拒绝；其余组合操作结束时都已放回；
  - 针筒吐液到 `中瓶区1` / `小开盖区` 时计入目标瓶液量（原来不计）；
  - 步骤报错后仍按其效果推演后续状态（原来针筒取用和过滤头两处例外），因此出错之后的连带提示可能和以前不同；
    超出范围的体积视为未知，不再拿它推演液量。

**需要注意。**

- `station_skills.py` 在配置哈希的受信文件里，哈希已经变了。运行中的 bridge 需要重启，EOS 需要重新加载实验室才会用上新代码。
- 顺带修复的已有缺陷：`station_mixture_stock`、`station_mixture_diluent` 的 `volume_ul` 规格缺少 `label` 和 `unit`，
  体积超出范围时 `plan_check` 会抛 `KeyError: 'label'`。已在 `capabilities.json` 补为 `移液体积` / `µL`（与其他移液操作一致），
  并加了回归测试。
- 没有做实机或 Isaac 物理运行，只验证了逻辑等价。

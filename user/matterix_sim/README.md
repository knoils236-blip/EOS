# matterix_sim

把 Matterix（Isaac Sim）数字孪生工作站接入 EOS。EOS 通过 SiLA 2 调用独立 bridge，bridge 再驱动 Isaac Sim。设计沿用 MATTERIX 论文 Fig. 1、Fig. 4 和 Methods 的分层技能、物理与语义协同；本包新增的是 EOS 编排和执行生命周期。

参数化配液、虚拟测量、质量检查和 Campaign 优化反馈见 [模拟配液与优化闭环](MIXTURE.md)。支持快速 Mock 和 Matterix 两种执行后端；测量均为明确标记的合成数据。

## 移液站完整实验

默认入口 `./start_eos.sh` 加载 `matterix_pipetting_lab` / `matterix_full_station`，同时加载 `mixture_mock_lab` / `mixture_mock` 供快速闭环实验使用。`./start_eos.sh --no-matterix` 仅加载配液 Mock。
在 EOS 中提交 `matterix_full_station`，协议包含初始化、七个操作阶段和结束检查，共九个任务：

1. 重置并核对初始状态。
2. 开门。
3. 小瓶取放与开盖合盖。
4. 小瓶向中瓶转移 100 µL（TIP 1）。
5. 大瓶取瓶与开盖。
6. 大瓶自吸自吐 100 µL、合盖归位（TIP 2）。
7. 注射器吸液、装过滤头、向小瓶转移 50 µL、弃置。
8. 关门。
9. 确认七阶段通过，归档后释放工作站。

每次 Protocol Run 从同一仿真初态开始。同一次实验保留液量、夹持与耗材状态，整段实验独占工作站。
初态来自 bridge 的 `station_initial_state.json`：TIP 源瓶 8 mL、反应器 70 mL，其他资产初值沿用导出。
这是仿真夹具配置；反应器的预装液用于让针尖浸没，不能解释为真实样品数据。

七个阶段复用导出 `full_station().sub_actions`，通过通用 `Matterix Capability` 任务调用。跨任务保留未改变的轴目标，
大瓶开盖后使用实时操作帧。首版体积、瓶位与 TIP 槽位固定，拒绝未登记参数。

任务输出包括验收明细与最终语义状态。EOS 文件中还可下载 `matterix-context.json` 和阶段的
`matterix-evidence.zip`（请求、配置、结果、事件、采样、版本与会话日志）；失败和取消同样归档。
实验室资源 `MX_SOURCE`、`MX_TARGET`、`MX_REACTOR`、`MX_BIG_BOTTLE`、`MX_TIP_1/2`、`MX_SYRINGE`、`MX_PISTON`
的 metadata 保存任务边界快照，执行期间的连续状态由 Matterix 维护。

录像默认关闭。需要录像时，在实验室设备的 `init_parameters` 中加入 `record_video: true`，
重新加载实验室后提交新实验。阶段 ZIP 会包含 Isaac Sim 实际渲染的 `simulation.mp4`（1920×540：左侧整站、右侧工具近景、25 fps），
录像启用时会增加渲染耗时；关闭时不会创建摄像机或视频文件。

## 失败、取消与恢复

EOS 取消和超时会先请求 bridge 停止，再确认远端终态、归档并处置设备占用。不能确认停止或归档失败时，
工作站停用；操作失败时也停止本次实验，不自动重试部分移液。

恢复通过 EOS 设备检查器的 `recover` 方法执行。确认重置验收通过后，在设备管理中重新激活设备，再提交新实验。
若会话已中断而无法重置，应先重新加载该实验室以创建新 worker。恢复不会续跑上一次实验。

原子针筒吐液与源包程序 33 一致，固定向 `小瓶区` 10 mL 转移瓶吐液；此前自加的 `recipient` 参数源包不支持，已下线。

## 执行范围

当前移液站接入改用 250 Hz PhysX 物理路径，记录仿真时间和实际耗时。旧运动学视频不能证明碰撞、支撑或抓取成功。液量是资产语义账本，
针筒体积依赖导出中的估算几何；这些结果不证明碰撞动力学、真实计量精度、化学产率或真机可用。
吸吐量按源包 `run_full_workflow.py` 的验收：与请求体积相差不超过 5 µL、吸入空气不超过 5 µL、不洒漏；账本总量守恒与工具残液仍按 1e-6 mL 检查。
弃置后的过滤头保留与针筒的装配连接，验收检查的是机械工具已释放针筒和活塞。
工作站同一时刻只有一个活动会话。EOS、bridge 和 Isaac Sim 使用独立环境，运行依赖见 bridge README。

## Franka 示例

- 设备 `matterix_workstation`（`MatterixWorkstation`，基于 `SilaDevice`）：初始化时新建一个仿真会话，清理时结束会话并等待 Isaac Sim 退出。
- 任务 `Matterix Capability`：在会话中执行一个已登记的能力（如 `pickup_beaker`），输出验收结果 `passed`、仿真耗时、最终语义状态（液量、设备状态）和 bridge 运行目录。能力未成功时任务失败。
- 实验室 `matterix_lab`、协议 `matterix_pick_and_return`：抓取烧杯再放回。

## 使用

如需原来的 Franka 烧杯示例，在项目根目录运行：

```shell
EOS_LABS=matterix_lab EOS_PROTOCOLS=matterix_pick_and_return ./start_eos.sh
```

bridge 的代码和运行环境见 `integrations/matterix/README.md`。bridge 只提供 SiLA 接口，Isaac Sim 在设备创建会话时才启动。

可用能力及参数范围由 bridge 的 `GetCapabilities` 给出；`scene_id` 决定场景，`franka_beaker` 场景的能力不能在 `pipetting_realtime` 会话中运行。

### 能力分层与类型化任务

`capabilities.json` 中每个能力带 `tier`：`operation` 是实验层操作，显示在编辑器"场景操作"中；`skill` 是单个 Matterix 原子技能，放在默认折叠的"原子技能"分组，用于调试和自定义序列；`hidden` 只供协议直接引用，不在编辑器中提供。

带 `volume_ul` 的 operation 使用类型化任务 `Matterix Volume Operation`，`capability_id`、`volume_ul`、`timeout_s` 都是顶层参数。协议加载时即校验体积范围和能力 ID，优化器可以直接引用 `<任务名>.volume_ul`，例如 `transfer_small.volume_ul`。可选能力和范围从目录读取，不需要手动维护。其余能力继续使用通用的 `Matterix Capability`。

原子技能的目录条目由 `bridge/station_skills.py` 的 `SKILLS` 生成。修改技能后运行 `python integrations/matterix/tools/sync_station_skills.py` 并重启 bridge（两份 JSON 都参与 bridge 配置版本校验）。测试会检查两者一致。

### 离线顺序检查与 AI 草稿

协议校验（编辑器校验、MCP 工具和 AI 草稿的修复回路都调用 `/protocols/validate`）通过静态校验后，还会调用设备类的 `check_plan`，按依赖顺序检查分配给同一工作站的步骤。移液站的规则在 `integrations/matterix/bridge/plan_check.py`，复用 bridge 的阶段表、TIP 映射和初态，只报告运行时一定会被 bridge 拒绝的情况：缺少实验初始化、阶段不连续、TIP 槽位重复使用、在空的中瓶区1 自吸自吐、结束检查的阶段数与实际不符、同一工作站的步骤之间没有依赖。体积使用 `eos_dynamic` 或引用、或者用原子技能移动过液体时，相关检查会跳过，不做猜测。

AI 草稿除了任务 spec，还会拿到所选实验室的场景操作（ID、含义、任务模板、参数范围）和场景说明（初态、阶段顺序、TIP 用法，由 `scene_notes` 从同一套规则生成）。检查不通过的错误会反馈给模型修正。

## 限制

- bridge 同一时间只允许一个活动会话，所以一个 bridge 只能对应一台 `matterix_workstation` 设备。
- 物理路径已接入。前门开合已在 2026-11-08 复测中通过 PhysX 验收（到位 0.389959 m，见 `evidence/matterix-front-door-recheck-20261108`），但整站七阶段物理流程尚未通过验收。液量来自仿真语义账本，不代表真实化学测量。
- `eos_dynamic` 和优化器只能用于顶层任务参数；`Matterix Capability` 的 `parameters` 字典内的参数需在协议中写定，需要优化体积时改用 `Matterix Volume Operation`。

## 测试

```shell
EOS_TEST_MATTERIX_PORT=65200 pytest tests/integrations/test_matterix_workstation.py
```

移液站的实际 EOS 调度与 Isaac Sim 验收（两次完整实验、一次取消、一次超时失败）使用独立数据库和 Ray namespace。
先在另一个终端启动专用 bridge，避免与正在使用的工作站冲突：

```shell
cd integrations/matterix
PYTHONNOUSERSITE=1 /home/ry/anaconda3/envs/chemos2/bin/python -B -m bridge.server --no-db --port 65201 --state-dir /tmp/matterix-eos-validation
# 在项目根目录，已有 EOS 的 Ray 与文件存储服务运行时：
EOS_TEST_MATTERIX_PORT=65201 EOS_MATTERIX_EVIDENCE_DIR=/path/to/evidence \
  .venv/bin/python -B -m pytest tests/integrations/test_matterix_full_station_live.py -s -q
```

静态与生命周期回归：

```shell
.venv/bin/python -B -m pytest tests/integrations/test_matterix_station_contract.py \
  tests/integrations/test_matterix_device_contract.py tests/tasks/test_external_device_lifecycle.py
```

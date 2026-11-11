# Matterix SiLA 2 bridge

从 `/home/ry/ChemOS2.0/matterix-integration` 复制而来（2026-11-02），随后增加 EOS 整站实验接入。它把 Matterix（Isaac Sim）包装成 SiLA 2 服务，EOS 通过 `user/matterix_sim` 中的 `matterix_workstation` 设备调用它。

## 目录

| 路径 | 内容 |
|---|---|
| `bridge/` | SiLA 2 服务（`python -m bridge.server`）、会话管理和 Isaac Sim worker |
| `config.json` | Matterix 源码目录、Matterix 环境的 Python 和单次任务的墙钟上限 |
| `assets/` | Isaac Sim 场景资产。只跟踪 `manifest.json`，其中有每个文件的下载地址和 sha256；二进制文件不进 git |
| `conda/` | ChemOS 2.0 的 conda 环境定义，bridge 需要其中的 `sila2==0.10.1` |
| `sessions/`、`runs/` | 运行时生成的会话与任务记录（不进 git） |

## 运行环境

bridge 运行在独立的 `chemos2` conda 环境中，不在 EOS 的 `.venv` 里（两者的 sila2、protobuf 版本不兼容）。默认使用 `/home/ry/anaconda3/envs/chemos2/bin/python`，可用 `MATTERIX_BRIDGE_PYTHON` 改为其他位置。

新建环境（版本取自 `conda/requirements-demo.txt`；新版 setuptools 已移除 `grpc_tools` 需要的 `pkg_resources`，`psycopg2` 在 `--no-db` 下也会被导入）：

```shell
conda create -n chemos2 python=3.10
conda run -n chemos2 pip install sila2==0.10.1 grpcio==1.46.5 grpcio-tools==1.46.5 protobuf==3.20.3 setuptools==68.2.2 psycopg2-binary==2.9.9
```

`conda/requirements-demo.txt` 是 ChemOS 2.0 demo 的完整依赖（含 Atlas 所需的 torch、botorch），安装时 `torch==1.13.1+cpu` 需要加 `--extra-index-url https://download.pytorch.org/whl/cpu`。装完再安装 Atlas 和 Olympus，这个环境也可以作为 `AtlasOptimizer` 的 `python`。

Isaac Sim worker 使用 `config.json` 中的 `matterix_python`（Matterix 环境）。`bridge/capabilities.py` 和 `bridge/station_worker.py` 还引用了 `/home/ry/projects/Matterix/PipettingStation_20261031` 下的移液站资产。

## 启动

通常由项目根目录的 `./start_eos.sh` 启动。单独启动：

```shell
cd integrations/matterix
PYTHONNOUSERSITE=1 /home/ry/anaconda3/envs/chemos2/bin/python -B -m bridge.server --no-db --port 65200
```

`--no-db` 表示不再同步到 ChemOS 的 PostgreSQL，记录由 EOS 保存。

`--state-dir <目录>` 可将运行记录与会话放到独立目录，适合隔离验证或独立部署。
默认 EOS 示例现为 `matterix_full_station`（初始化、七阶段、结束检查）；见 `user/matterix_sim/README.md`。
完整移液站的统一初态在 `bridge/station_initial_state.json`，配置和执行源码均参与版本校验。
`GetRunArchive` 提供终态任务的 ZIP 证据，EOS 同时归档成功、失败和取消结果。

不要在同一端口上同时运行两个 bridge：gRPC 允许端口复用，第二个进程不会报错，但请求会被随机分给两个进程。

## 移液站数字孪生（Web）

EOS 界面侧边栏有独立的“数字孪生”栏目（`/digital-twin`）：模型铺满整个页面，标题和透明度滑块叠在画布上，自动跟随最新一次协议运行的最新样本（运行中每秒刷新，否则每 5 秒查一次），没有时间轴。Matterix 执行视图里的同名标签页（任务级和协议级都有）用同一个 three.js 组件，多一条可回放的时间轴。

模型由 `tools/export_station_twin.py` 从 `PipettingStation_Operational.usd` 导出到 `web_ui/public/twin/`（GLB 约 28 MB，不进 git，新环境需要先生成一次；USD 或外壳判定规则变了也要重新生成）：

```shell
/home/ry/anaconda3/envs/matterix/bin/python -m pip install meshoptimizer
/home/ry/anaconda3/envs/matterix/bin/python integrations/matterix/tools/export_station_twin.py
```

导出只取可见网格，按连杆、外壳件、耗材和 USD 材质分节点，保留 USD 材质（颜色、金属度、粗糙度、透明度，含按面分配的 GeomSubset）并按 35° 折痕生成法线。减面有误差上限：只合并使表面偏移不超过 0.2 mm 的三角面（287 万 → 71 万）。31 个关节的轴和锚点、耗材初始位姿写进同目录的 JSON。USD 是操作中途的 CAD 快照，开盖位和工具头上挂着演示件（254 号大瓶、116/271/609 号小瓶、120 号吸头、094/095 号针筒、150 mL 样品瓶）；仿真启动前会移除它们，导出脚本读取 `matterix_assets/equipment/pipetting_station.py` 里的同一份清单并跳过，否则夹爪到位时会和这些不存在的瓶子穿模。外壳件沿用录像 `hide_shell` 的判定（`bridge/recording.py` 的 `shell_visual_paths`）。

驱动数据是样本里已有的两项，孪生不关心它们来自仿真还是真机：

| 字段 | 内容 |
|---|---|
| `semantics.jointsSI` | 关节名 → 位置（m / rad），关节名与 USD、PLC 轴映射一致 |
| `semantics.object_poses` | 复位后位移超过 2 mm 的耗材 → base 连杆坐标系下的 `[x, y, z, qx, qy, qz, qw]`；未列出的耗材画在 USD 初始位置 |

接真机时，让真机设备的样本按同样的关节名上报 `jointsSI` 即可驱动机构；真机没有耗材位姿测量时不填 `object_poses`，耗材会停在初始位置。当前只接了仿真。

显示上用 USD 原始材质加环境光照。外壳和门不隐藏，默认透明度 0.75 并描出面板轮廓线；机架和台面默认不透明。两者各有透明度滑块（0 到 0.95，外壳拉到 0 就是整机真实外观），运动机构按原材质显示；耗材的不透明度下限提到 0.65 并描轮廓线（USD 里玻璃和透明塑料的不透明度只有 0.03–0.35，在半透明外壳里几乎看不见）；两个样本之间有位移的关节，其连杆标成橙色并列在“正在运动”里。样本间隔约 0.5 s，中间线性插值，所以快于采样间隔的往复动作看不到。

## 2026-11-04 实验流程与物理边界修正

EOS 移液站已从仿真动作项和协议关节观测中移除 `service_door_left`、`service_door_right`。环境动作只包含其余 27 个工作流关节；上游 PLC 的原始 29 列在 bridge 内按名称映射，不再向环境传递检修门命令。原始 USD、manifest、门板及碰撞体保留，`hide_shell` 仍仅控制录制可见性。这与整站演示中显式使用的升降前门 `front_door` 无关。

移液站入口改用导出的 `PipettingStationTwinEnvCfg` 推进 PhysX；原有 `pipetting_realtime` 场景 ID 保留。不会回退到运动学执行。原始 USD、碰撞体和导出源码保持不变。前门已在 2026-11-08 复测中通过 PhysX 开合验收（见下文），但新整站七阶段物理流程尚未通过验收，旧运动学录像不能作为碰撞验收证据。

针筒原子吐液固定向小瓶区转移瓶吐液（源包程序 33）；此前的 `recipient` 扩展已下线。编辑器预检与实验结束检查覆盖盖子、过滤头、液量及一次性耗材弃置。录制为 1920×540、25 fps，左侧整站、右侧工具近景；协议拼接保留该尺寸。双画面已通过实际场景解码和首/中/末帧检查；动态工具跟随及液体出口/瓶口可见性尚未完成物理动作验收。

配置哈希包含这些源码，运行中的旧 bridge 不会自动热更新。启用前须结束空闲旧会话、重启 bridge，并在 EOS 重新加载实验室；不要中断正在执行的任务。

### 场景启动失败与加载恢复（2026-11-04）

两个场景的地面现在读取本地 `assets/Grid/default_environment.usd` 及其三个贴图，文件与 NVIDIA 原始资源一致，来源、大小和 SHA-256 见 `assets/Grid/manifest.json`。地面位置、碰撞、材质和尺寸沿用原配置；不再在初始化时请求在线地面 USD。与现有 SeattleLabTable 缓存一样，二进制资源受 Git 忽略规则管理，迁移时需一起复制，或按 manifest 的 `source_url` 下载至相同相对路径。

设备初始化会等待仿真进入 `idle` 后才成功；启动失败会返回底层错误并关闭会话。切换场景仍检查 EOS 运行/暂停实验、独立任务、实验所有权和锁定状态；无人占用且没有活动任务的 `ended` / `interrupted` 会话可以被替换。需要人工处理时，冲突信息包含具体状态与底层错误；已终止会话须重新加载设备，不能调用仅适用于现存会话的 ResetSession。预期的占用冲突返回 HTTP 409 并记录警告，不再输出服务器异常堆栈。

本次真实 API 验证：移液站启动及初态验收通过，加载 `matterix_pick_and_return` 返回 201，切回 `matterix_full_station` 返回 201，并恢复原有移液站和 Mock 协议。离线相关测试 103 passed；未运行整站实验或实机实验。记录见 [场景加载修复证据](../../evidence/matterix-scene-load-fix-20261104/README.md)。

前次离线验收：受影响范围 152 passed、1 skipped（原生 Isaac 配置构造测试缺少已初始化的 Isaac 环境）；本次主要修改文件通过 Ruff 检查，旧 worker / capability 文件相对任务基线没有新增 Ruff 诊断。该阶段未运行全仓测试或实机实验，也未重启当时的旧会话。

随后已执行真实 PhysX 验收：初始化和 2 秒等待通过；四个容器后段静置稳定；前门目标 390 mm、实测约 2.835 mm，本次 8 秒仿真窗口超时，连续流程停止在第一阶段。验收中修复了纯 Wait 初始动作为空的 bridge 接入错误，相关回归 50 passed、1 skipped。原始资产/导出源码的 8 个受保护文件哈希未变，独立验收服务与 Isaac worker 已退出。完整报告和视频见 [物理验收证据](../../evidence/matterix-physics-acceptance-20261104-d12ffa/README.md)。

2026-11-08 在独立 bridge 上复测前门（HEAD `0dff2fa`，配置版本 `d2138389…`）：`station_skill_front_door` 开到 0.389959 m、关回约 0 m，11-04 失败的 `station_full_station_open_door` 在 6.11 s 仿真内到位 0.389959 m，三项均在 ±1.5 mm 内且无拒绝。8 秒窗口不是 11-04 失败的原因；两次配置版本不同，具体是哪处改动消除了 `Tracking lag at front_door` 未定位。移液、旋盖、过滤头、弃置和连续七阶段未在复测中运行。见 [前门复测证据](../../evidence/matterix-front-door-recheck-20261108/README.md)。

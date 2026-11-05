# EOS 上手演示

跟着做一遍大约 15 分钟。前两步只用浏览器；第三步换成脚本，看看同样的事情怎么用命令完成。

## 0. 启动

```shell
./start_eos.sh        # 在项目根目录运行；按 Ctrl+C 停止
demo/run.sh setup     # 另开一个终端：加载演示用的两个实验室和两个协议
```

打开 <http://127.0.0.1:3000>。

## 先认识五个词

| 概念 | 是什么 | 演示里的例子 |
|---|---|---|
| 设备（Device） | 一台仪器的控制程序 | `multiplier`（会做乘法的“仪器”）、`matterix`（Isaac Sim 中的机械臂工作站） |
| 实验室（Lab） | 一组设备及其所在电脑 | `multiplication_lab`、`matterix_lab` |
| 任务（Task） | 用设备完成的一步操作 | `Multiplication`、`Matterix Capability` |
| 协议（Protocol） | 任务按依赖关系串成的流程 | `optimize_multiplication`：乘两次再打分 |
| 实验计划（Campaign） | 把一个协议跑很多遍，可以让优化器挑参数 | 自动寻找乘积最接近 1024 的三个数 |

它们都放在“软件包”里：`user/example`（乘法）和 `user/matterix_sim`（仿真）。

## 1. 看懂一个协议（编辑器）

1. 左侧点 **编辑器**，展开 `example` → 协议 → `optimize_multiplication`，再点画布上方的 **自动布局** 看到全图。
2. 画布上有三个任务：`mult_1` 把 number 乘以 factor，`mult_2` 再乘以另一个 factor，`score_multiplication` 计算结果与 1024 的差（loss）。每个节点左边是输入、右边是输出，连线表示数据和先后顺序，例如 `mult_1` 的 `product` 连到 `mult_2` 的 `number`。
3. 点右上角 **代码** 看协议的 YAML 原文。值为 `eos_dynamic` 的参数在协议里不写死，由提交者或优化器在运行时给出；**优化器** 按钮里是优化器的设置。

## 2. 手动跑一次，再让优化器跑（Web 界面）

**手动跑一次协议**

1. 左侧点 **协议运行** → 右上角 **提交协议运行**。
2. 名称填 `my_first_run`，协议选 `optimize_multiplication`，负责人随便填。
3. 往下滚到“任务参数”，按任务分组填值：`mult_1` 的 number 填 `4`、factor 填 `16`，`mult_2` 的 factor 填 `16`（4×16×16=1024）。`mult_2` 的 number 默认是 Reference，表示直接取 `mult_1` 的输出，不用填。
4. 提交后点开这次运行，看三个任务依次变成“已完成”；点 `score_multiplication` 看输出 `loss`，应为 0。

**让优化器去找参数**

1. 左侧点 **实验计划** → **提交实验计划**。
2. 名称 `my_first_campaign`，协议 `optimize_multiplication`，最大协议运行次数 `15`，最大并发 `2`，勾选“启用优化”。
3. 提交后点开它：能看到每一轮优化器给出的参数、loss 的变化曲线，以及最终的最优解。整个过程十几秒。

自己改改试试：把轮数加到 30，看最优解是否更接近 1024。

**在仿真里动一动机械臂**

1. **协议运行** → **提交协议运行**，协议选 `matterix_pick_and_return`，不需要填参数。
2. 两个任务依次完成：`pickup` 抓起烧杯并抬高 10 cm，`return_beaker` 放回原位。点开任务，看输出 `passed`（验收是否通过）、`sim_time_s`（仿真用时）和 `final_semantics`（仿真结束时的状态）。
3. 左侧 **设备检查器** 选 `matterix_lab` / `matterix`，可以看到设备状态和它连接的仿真会话。

**在编辑器里直接编排和提交**

1. 打开任意协议的可视化画布，左侧的 **任务** 面板列出所有可用任务。把任务卡片拖到画布上（或悬停后点 **+**）即可添加。
2. 从一个任务右侧的输出点拖到另一个任务左侧的输入点即可连线，缺少的先后依赖会自动补上。保存后生效。
3. 编辑器顶部的 **提交协议运行**（打开协议时）或 **提交任务**（打开任务时）会弹出与对应页面相同的表单，类型已预选。提交后按钮旁出现 **查看** 链接，跳到这次运行的详情。

## 3. 用脚本做同样的事（REST API）

```shell
demo/run.sh mult 15     # 提交 15 轮优化实验计划，等待完成并打印最优解
demo/run.sh matterix    # 提交一次机械臂协议运行，打印两个任务的验收结果
```

脚本只是调用 EOS 的 REST API（<http://127.0.0.1:8070/api>），提交的运行同样会出现在 Web 界面里。接口说明见 `docs/user-guide/rest_api.rst`。

## 4. 新能力试一试

- **多保真度优化**：提交实验计划，协议选 `optimize_yield_multi_fidelity`，最大协议运行次数 14、启用优化。优化器会在快速但有偏差的 `simulation` 和慢但准确的 `experiment` 之间自动选择；最优解只来自 `experiment`。
- **仿真先行预检**：先在“系统管理”加载 `multiplication_lab_twin`，再提交 `optimize_multiplication` 的协议运行，在“元数据（JSON）”里填 `{"preflight": {"multiplication_lab": "multiplication_lab_twin"}}`。EOS 会先跑 `<名称>__preflight`（设备全部换成孪生实验室），通过后才在真实实验室上运行。
- **AI 草稿**：在协议画布工具栏点 **AI 草稿**，用一句话描述实验目标，生成的协议会先经过编排器校验，确认后再应用到画布。需要在 `.env` 配置 `EOS_AI_*`，或可用的 Claude 登录。
- **学习任务耗时**：在 `config.yml` 的 `scheduler` 下加 `learn_durations: true`，CP-SAT 和启发式调度器会用最近实测的耗时排程。

## 下一步

- 改协议：在编辑器里给 `matterix_pick_and_return` 的 `pickup` 改 `lift_height_m`（0.06–0.2），再跑一次，对比 `final_semantics`。
- 写自己的包：`eos pkg create my_lab`，再用 `eos pkg add device|task|lab|protocol` 添加内容，可参考 `user/matterix_sim`。

# 模拟配液与优化闭环

这个例子将 EOS 的 Campaign、可调整的配液条件、样品记录、虚拟测量、质量检查和目标函数串起来。它用于验收软件闭环，没有接入真实实验设备。

实验计划详情页提供“实验决策”面板。OpenAI 兼容连接、按需分析和 Beacon 选参的配置见 [实验决策与 AI](../../docs/user-guide/decision_ai.rst)。默认仍为纯贝叶斯优化，不会自动调用 AI。

## 实验定义

每份样品总量固定为 200 µL。优化器选择 20–180 µL 的母液量，稀释液量为其余部分。仿真配方假设母液浓度为 1 mM，稀释液为 0 mM，目标浓度为 0.37 mM，因此已知模型的理论母液量为 74 µL。

这是明确标记的配方模型。Matterix 只提供实际执行后的语义液量，不模拟真实化学组成或真实仪器计量误差。虚拟测量在模型浓度上加入标准差 0.003 mM 的独立高斯噪声，记录三次读数、空白、参考样、模型版本和随机种子；同一样品与种子可重复生成相同数据。

协议有四个任务：

1. `prepare`：准备样品并记录来源容器、组成、执行后端、命令历史，输出 `sample.json`。
2. `measure`：生成虚拟观测及原始读数，输出 `observation.json`。
3. `quality`：检查空白、参考样、读数范围与重复性，输出 `quality.json`。
4. `score`：核对观测与质控记录的身份和结论，仅对通过检查的数据计算浓度误差平方。

母液量是 EOS 的顶层动态任务参数 `prepare.stock_volume_ul`；优化目标为 `score.loss`。现有贝叶斯优化器负责提出条件和接收反馈，不需要 AI 服务。

## 两种执行后端

`mixture_mock_lab` / `mixture_mock` 使用快速物料平衡 Mock，适合协议、数据质量、恢复和多轮反馈检查。

`mixture_matterix_lab` / `mixture_matterix` 使用同样的任务接口，调用常驻 Matterix 会话，依次执行开门、小瓶准备、可调整母液转移、大瓶开盖、向目标瓶加入稀释液并归位、关门。两次液体转移分别使用 TIP 1、TIP 2，液量取实际语义状态差值，六个阶段都通过验收后才生成样品。每份样品重置到相同初态。

每份配液在一个 EOS 任务内独占工作站；测量与分析使用已经保存的样品记录。原 `matterix_full_station` 七阶段协议及其固定体积保持不变，用作整站回归。

## 使用

在没有另一个 EOS 编排器运行时，从项目根目录启动 Mock：

```sh
./start_eos.sh --no-matterix
```

默认 `./start_eos.sh` 同时加载整站仿真和配液 Mock。仍可用 `EOS_LABS` / `EOS_PROTOCOLS` 显式选择配置。若服务已经运行，在管理页先加载实验室 `mixture_mock_lab`，再加载协议 `mixture_mock`；协议的 `labs` 必须包含 `mixture_mock_lab`。

向正在运行且已加载上述实验室和协议的 EOS 提交八轮优化：

```sh
curl -X POST http://127.0.0.1:8070/api/campaigns/ \
  -H 'Content-Type: application/json' \
  -d '{"name":"mixture_demo_01","protocol":"mixture_mock","optimize":true,"max_protocol_runs":8,"max_concurrent_protocol_runs":1,"max_failed_protocol_runs":0}'
```

用新名称提交新的 Campaign。每轮的样品、观测、质控和目标值都保存在 EOS 的任务输出与文件存储中。

使用 Isaac 后端时启动配置改为：

```sh
EOS_LABS=mixture_matterix_lab EOS_PROTOCOLS=mixture_matterix ./start_eos.sh
```

同一个 bridge 只能承载一个活动工作站会话。`mixture_matterix_lab` 与 `matterix_pipetting_lab` 不能同时加载；切换前等待当前实验结束，再卸载旧实验室。Mock 可以与任一 Isaac 实验室同时加载。

## 数据可信度与失败处理

样品的 `backend` 明确区分 `mock` / `simulation`，组成来源区分 Mock 物料平衡和 Isaac 语义账本。观测的 `source` 一律为 `synthetic_model`，并保留模型、处理版本、单位、不确定度与原始读数。`hardware_calibrated` 始终为 false。

质控不合格会保留原始观测和明确原因，打分任务失败。默认 Campaign 立即停止；即使使用失败预算，配液示例优化器也会排除没有目标值的失败运行，只清除其待返回标记，不将其当作化学不可行性训练数据。

执行取消或失败时先停止远端并归档已完成与活动阶段的证据，工作站停用，需要显式 `recover`、重新激活，再使用新的实验运行名称。配液命令与样品保存在 `.state/mixture/` 的持久记录中：重复提交相同已完成请求返回原样品，参数不同则拒绝；进程重启发现未确定的执行状态时冻结，不自动重做部分移液。该日志不提供真实设备的事务回滚或断电后恰好执行一次的保证。

## 验收

普通契约检查：

```sh
.venv/bin/python -B -m pytest tests/integrations/test_mixture_contract.py tests/integrations/test_mixture_station.py
```

实际 EOS 的 Mock 优化验收依赖已运行的 Ray 和文件存储：

```sh
EOS_TEST_MIXTURE_LIVE=1 .venv/bin/python -B -m pytest tests/integrations/test_mixture_live.py -k mock -s
```

实际 Isaac 验收需先停止其他 Isaac 工作站，并在独立端口运行专用 bridge，再指定 `EOS_TEST_MATTERIX_PORT`。验收覆盖 20、74、180 µL 三个母液量以及对应稀释液量。测试使用独立数据库和 Ray namespace；通过 `EOS_MIXTURE_EVIDENCE_DIR` 指定保留证据的位置。

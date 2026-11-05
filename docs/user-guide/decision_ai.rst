实验决策与 AI 服务
===================

实验计划详情页的“实验决策”面板把最近 50 轮的输入条件、测量、数据来源、质控、目标值与选择依据放在一起。
最优值只使用已完成且质控通过的记录，按执行后端、测量来源和模型版本分别计算。
历史记录没有逐轮选择依据时显示“未记录”，不会根据结果补写理由。

AI 设置
-------

超级用户可以在“系统管理”的“AI”标签中配置服务。配置同时供实验决策分析、协议起草和
Beacon 的默认模型使用。支持：

- ``openai``：OpenAI Chat Completions 兼容服务。
- ``anthropic``：Anthropic Messages 服务。

界面会填写 API 基地址、模型名称和 API Key。基地址不能包含 ``/chat/completions``、
``/messages``、账号、查询参数或片段。浏览器只能看到是否已配置密钥，接口不会返回密钥内容；
保存空 API Key 会保留已有密钥。

Anthropic 的 Base URL 填写不带版本路径的根地址，例如 ``https://api.anthropic.com``；
SDK 会自动追加 ``/v1/messages``。不要把 ``/v1`` 填入设置，否则会形成 ``/v1/v1/messages``。

设置默认保存在 EOS 项目根目录的 ``.eos/ai-settings.json``，目录权限为 700、文件权限为 600，
并忽略 Git。可用绝对路径覆盖：

.. code-block:: dotenv

   EOS_AI_SETTINGS_FILE=/var/lib/eos/ai-settings.json

相对覆盖路径以 EOS 项目根目录为准。服务端和 Ray worker 使用解析后的同一个绝对路径；
Beacon worker 同时接收当前连接的 base URL 和密钥，避免工作目录不同导致读取不同设置。

环境变量后备
------------

未启用界面设置时，仍可使用 EOS 项目根目录 ``.env`` 中的旧配置：

.. code-block:: dotenv

   EOS_AI_PROTOCOL=openai
   EOS_AI_BASE_URL=https://your-service.example/v1
   EOS_AI_API_KEY=your-api-key
   EOS_AI_MODEL=your-model-name
   EOS_AI_TIMEOUT_S=60

优先级从高到低为：显式调用参数、界面保存的 ``.eos/ai-settings.json``、进程环境变量和
``.env``。``EOS_AI_MODEL`` 填实际模型名称，不加 ``openai:`` 或 ``anthropic:`` 前缀；
模型前缀只用于 Beacon 的显式模型字段。新的决策分析、协议起草及新建 Beacon 计划读取当前设置；
运行中的 Beacon 计划保留创建时的模型和连接。
密钥只由后端读取，不填写到浏览器、协议文件或实验计划的参数覆盖中。

协议采用 OpenAI `Chat Completions
<https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create>`_
或 Anthropic Messages。按需分析只需要文本回复；Beacon 选参还需要兼容工具调用及其结构化结果。
显式 ``claude-agent-sdk:`` 和 ``ollama:`` 模型继续使用原有配置路径，不受界面设置的模型名称影响。

两种用途
--------

“生成 AI 分析”由用户按需点击。它发送最近 50 轮的条件、测量、质控、选择依据与目标信息，
提供事实、推断和下一步实验建议，不修改实验、调度或设备。原始文件、API Key 和实验负责人信息不进入该上下文。
分析对应的数据版本会显示在面板中，实验数据变化后会提示刷新或重新生成。
分析结果保留在当前页面会话中；选参记录则随协议运行持久保存。

Beacon 可以在新实验计划中参与选参。配液例子默认仍为 100% 贝叶斯优化，配置 AI 服务不会自动触发 AI 请求。
界面设置了有效服务时，Beacon 未指定 ``ai_model`` 的默认值使用该服务的协议和模型。
也可通过 ``meta.optimizer_overrides`` 显式设置 ``p_ai`` 和 ``ai_model``，不在其中传递密钥。
运行中的计划可以调整 AI 比例及补充上下文；模型与连接变更应在新计划中使用。

AI 的建议仍通过现有参数域与约束验证。超出范围会请求修正；服务不可用时，由原优化器接替，
该轮会明确记录接替原因。每轮的选择依据在提出条件时保存，不由事后分析推断。
提供给配液 AI 的上下文包含合成测量与质控结论，缺失结果不会作为化学不可行性训练数据。

当前配液例子没有真实仪器。``mock``、``simulation``、``synthetic_model`` 的标记保留，
账本守恒与优化结果不能证明真实计量精度或化学性能。预算用尽也不代表科学目标已经达到。

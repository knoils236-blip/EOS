<p align="center">
    <img src="docs/_static/img/eos-logo.png" alt="EOS logo" width="400">
</p>

<h1 align="center">实验编排系统（EOS）</h1>
<h3 align="center">实验室自动化基础平台</h3>

![python](https://img.shields.io/badge/Python-3.11+-darkgreen)
[![Docs](https://img.shields.io/badge/Docs-Available-brightgreen)](https://unc-robotics.github.io/eos/)
![license](https://img.shields.io/badge/License-BSD_3--Clause-blue)

EOS 是面向实验室自动化的软件框架与运行时，可为一个或多个自动化实验室或自驱动实验室（SDL）提供基础支持。

**核心功能**

* 通过插件定义实验室、设备、任务、协议和优化器
* 通过软件包共享和复用自动化代码
* 在加载和运行时验证协议、参数及配置

**执行与调度**

* 中央编排器协调多台计算机上的设备和协议
* 智能任务调度，动态分配设备与资源
* 无需硬件即可离线仿真和测试调度策略

**优化**

* 内置贝叶斯优化，支持单目标和多目标实验计划
* Beacon 优化器将[可插拔算法](https://unc-robotics.github.io/eos/user-guide/custom_beacon.html)与 AI 推理结合

**交互接口**

* 中文 Web 界面，提供可视化协议编辑器、实时监控、设备检查器和文件浏览器
* 提供 OpenAPI 文档的 REST API
* 用于连接 AI 助手的 MCP 服务
* 可选的身份验证，支持角色权限和个人 API 令牌
* SiLA 2 仪器协议集成

完整的上游英文文档见 [EOS 文档](https://unc-robotics.github.io/eos/)。

## 安装与启动

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)、Node.js、npm 和 Docker Compose，然后克隆 EOS 并运行配置向导：

```shell
git clone https://github.com/UNC-Robotics/eos
cd eos
uv sync --all-groups
source .venv/bin/activate
eos setup
eos services up
eos start
```

在另一个终端中安装并启动 Web 界面：

```shell
cd eos/web_ui
source ../.venv/bin/activate
npm install
eos start ui
```

默认访问地址为 `http://127.0.0.1:3000`。

完成上面的安装后，也可以用一条命令启动全部组件：基础服务、Matterix（Isaac Sim）仿真 bridge、Web 界面，以及加载了 `matterix_pipetting_lab` / `matterix_full_station` 和 `mixture_mock_lab` / `mixture_mock` 的编排器。`--no-matterix` 仅加载配液 Mock，适合软件闭环实验。按 Ctrl+C 停止脚本启动的所有进程：

```shell
./start_eos.sh                 # --no-matterix 不启动仿真；--no-ui 不启动界面；--rebuild-ui 重新构建界面
```

Matterix 的接入方式见 [user/matterix_sim](user/matterix_sim/README.md)，bridge 的代码与运行环境见 [integrations/matterix](integrations/matterix/README.md)。

手动配置和平台说明见[安装指南](https://unc-robotics.github.io/eos/user-guide/installation.html)，共享身份服务和角色说明见[身份验证指南](https://unc-robotics.github.io/eos/user-guide/authentication.html)。

## 中文界面

Web 界面、CLI 帮助和交互提示使用简体中文。协议（Protocol）表示任务流程，协议运行（Protocol Run）表示一次流程执行，实验计划（Campaign）用于组织多次协议运行及优化。

命令名、选项、API 字段、配置键、状态码和用户自定义名称保持原样；界面中的状态和角色使用中文显示。第三方组件、插件提供的说明和后端原始诊断信息可能仍为英文。

修改源码后，生产模式需要重新构建并启动界面：

```shell
eos start ui --build
```

启用 Zitadel 时，中文登录页需要重新构建镜像并重建容器；配置自定义 `ZITADEL_LOGIN_IMAGE` 时，请使用相同的镜像标签：

```shell
docker build -t eos-zitadel-login:v4.15.1-eos docker/zitadel/login
docker compose --profile auth up -d --force-recreate zitadel-login
```

本地化检查（需 Node.js 24 或更新版本）：

```shell
cd web_ui
node --test tests/localization.test.mjs
```

## 引用

如果在工作中使用 EOS，请引用以下论文：

```bibtex
@inproceedings{Angelopoulos2025_EOS,
  title = {The Experiment Orchestration System ({EOS}): Comprehensive Foundation for Laboratory Automation},
  booktitle = {2025 IEEE International Conference on Robotics and Automation (ICRA)},
  author = {Angelopoulos, Angelos and Baykal, Cem and Kandel, Jade and Verber, Matthew and Cahoon, James F. and Alterovitz, Ron},
  year = {2025},
  month = may,
  pages = {15900--15906},
  doi = {10.1109/ICRA55743.2025.11128578},
}
```

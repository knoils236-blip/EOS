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

## 部署流程

### 组件与端口

| 组件 | 说明 | 默认端口 |
|---|---|---|
| PostgreSQL | EOS 数据库（Docker） | 5432 |
| SeaweedFS | S3 兼容对象存储，保存任务文件（Docker） | 8333 |
| Filestash | 文件浏览（Docker） | 8334 |
| EOS 编排器 | `eos start`，同时提供 REST API | 8070 |
| Web 界面 | Next.js，`eos start ui` | 3000 |
| Matterix bridge | SiLA 2 服务，可选 | 65200 |
| Zitadel | 身份验证，可选（`--profile auth`） | 由 `eos setup` 决定 |

只做软件闭环（调度、协议编辑、配液 Mock）时，前四项加 Web 界面即可；要接 Isaac Sim 仿真，再部署 Matterix bridge。

### 1. 前置条件

- Linux，Python 3.11 或更新版本
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Docker 与 Docker Compose
- Node.js 24 或更新版本及 npm（Web 界面和本地化检查）
- 仅 Matterix 仿真需要：NVIDIA GPU、Isaac Sim / Isaac Lab、conda（见第 7 步）

### 2. 获取代码并安装 Python 依赖

```shell
git clone git@github.com:knoils236-blip/EOS.git
cd EOS
uv sync --all-groups
source .venv/bin/activate
```

### 3. 生成配置

```shell
eos setup
```

向导会依次询问数据库、S3、Web API 地址，以及是否启用身份验证，并写入仓库根目录的 `.env`、`config.yml` 和 `web_ui/.env`。三者含密钥，已在 `.gitignore` 中，不要提交。密码留空会自动生成。重新运行向导会保留已有的值。

只想写文件、暂不部署 Zitadel 时加 `--skip-bootstrap`。不启用身份验证时，所有人都以管理员身份访问，仅适合本机或内网试用。

### 4. 启动基础服务

```shell
eos services up        # 启用身份验证时加 --auth
```

这会在后台启动 PostgreSQL、SeaweedFS 和 Filestash。若本机 5432 已被占用，可在仓库根目录建 `docker-compose.override.yml` 改发布端口（已被 `.gitignore` 忽略），并同步 `eos setup` 中填的数据库端口：

```yaml
services:
  eos-postgresql:
    ports: !override
      - "5433:5432"
```

常用命令：`eos services down`（停止并移除容器，加 `-v` 会删除数据卷）、`eos services restart [服务]`。

### 5. 启动编排器

```shell
eos start --labs mixture_mock_lab --protocols mixture_mock
```

`--labs` 和 `--protocols` 是逗号分隔的列表，指向 `user/` 下软件包里的实验室和协议；不带参数时使用 `config.yml` 中的列表。编排器启动后 REST API 在 `http://127.0.0.1:8070`。同一数据库不能同时运行两个编排器。

### 6. 启动 Web 界面

在另一个终端中：

```shell
cd EOS/web_ui
source ../.venv/bin/activate
npm install
eos start ui --build     # 首次或源码更新后加 --build；之后直接 eos start ui
```

访问 `http://127.0.0.1:3000`。首次构建可能需要几分钟。

### 7. 可选：部署 Matterix（Isaac Sim）仿真

Matterix 部分依赖本机环境，**迁移到新机器前要检查下面这些路径**：

| 内容 | 当前位置 | 修改处 |
|---|---|---|
| bridge 的 conda 环境 `chemos2` | `/home/ry/anaconda3/envs/chemos2/bin/python` | 环境变量 `MATTERIX_BRIDGE_PYTHON` |
| Matterix 源码与 `matterix` 环境 | `/home/ry/projects/Matterix` | [integrations/matterix/config.json](integrations/matterix/config.json) |
| 移液站资产目录 | `/home/ry/projects/Matterix/PipettingStation_20261031` | `bridge/capabilities.py`、`bridge/station_worker.py`、`bridge/station_skills.py` 中的常量 |
| 地面和台面等二进制资产 | `integrations/matterix/assets/`（不进 git） | 按 `assets/*/manifest.json` 的 `source_url` 下载到同一相对路径 |

步骤：

1. 准备 Matterix 环境和移液站资产，使上表路径有效。
2. 创建 bridge 的 conda 环境（`sila2` 与 EOS 的 `.venv` 版本冲突，必须独立）：

   ```shell
   conda create -n chemos2 python=3.10
   conda run -n chemos2 pip install sila2==0.10.1 grpcio==1.46.5 grpcio-tools==1.46.5 protobuf==3.20.3 setuptools==68.2.2 psycopg2-binary==2.9.9
   ```

3. 生成 Web 数字孪生模型（约 28 MB，不进 git，新环境需生成一次）：

   ```shell
   conda run -n matterix pip install meshoptimizer
   conda run -n matterix python integrations/matterix/tools/export_station_twin.py
   ```

4. 启动 bridge（端口 65200，`--no-db` 表示记录由 EOS 保存）：

   ```shell
   cd integrations/matterix
   PYTHONNOUSERSITE=1 $MATTERIX_BRIDGE_PYTHON -B -m bridge.server --no-db --port 65200
   ```

   不要在同一端口同时运行两个 bridge。

5. 编排器加载 Matterix 实验室和协议：

   ```shell
   eos start --labs matterix_pipetting_lab,mixture_mock_lab --protocols matterix_full_station,mixture_mock
   ```

更多细节见 [user/matterix_sim](user/matterix_sim/README.md) 和 [integrations/matterix](integrations/matterix/README.md)。

### 8. 一键启动

完成第 2、3 步后，`start_eos.sh` 依次启动基础服务、Matterix bridge（已有服务会复用）、Web 界面和编排器。按 Ctrl+C 会停止脚本启动的所有进程：

```shell
./start_eos.sh                 # 完整栈
./start_eos.sh --no-matterix   # 只加载配液 Mock，不需要 Isaac Sim
./start_eos.sh --no-ui         # 不启动界面
./start_eos.sh --rebuild-ui    # 重新构建界面
```

可用环境变量 `EOS_LABS`、`EOS_PROTOCOLS`、`MATTERIX_BRIDGE_PYTHON`、`MATTERIX_BRIDGE_PORT`、`PORT`（界面端口）覆盖默认值。日志在 `.state/logs/`。

### 9. 启用身份验证（可选）

在 `eos setup` 中选择启用身份验证，向导会部署 Zitadel 并写入凭据；之后用 `eos services up --auth` 启动。创建用户和角色见 `eos auth --help`，共享身份服务和角色说明见[身份验证指南](https://unc-robotics.github.io/eos/user-guide/authentication.html)。

### 10. 验证部署

- `docker ps` 能看到 `eos-postgres`、`eos-seaweedfs`
- `http://127.0.0.1:8070/docs` 能打开 API 文档
- `http://127.0.0.1:3000` 能打开界面，实验室页面里有已加载的实验室
- 在界面中提交 `mixture_mock` 协议，状态变为完成

### 11. 更新与常见问题

更新代码后：`git pull`、`uv sync --all-groups`、`eos start ui --build`，再重启编排器。若更新带有数据库迁移，运行 `eos db upgrade`（`eos db current` 查看当前版本）。首次部署如果表未创建，运行 `eos db init`。

- **`eos: command not found`**：先 `source .venv/bin/activate`。
- **数据库连接失败**：检查 `docker ps`，以及 `.env` 中的端口是否与 `docker-compose.override.yml` 一致。
- **Matterix bridge 启动失败**：看 `.state/logs/matterix-bridge.log`；多半是 conda 环境路径或 Matterix 资产路径不对。
- **界面显示旧内容**：生产模式需要 `eos start ui --build`。
- **孪生页面没有模型**：未生成 `web_ui/public/twin/`，见第 7 步。

手动配置和平台说明见[安装指南](https://unc-robotics.github.io/eos/user-guide/installation.html)。

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

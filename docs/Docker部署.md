# Docker 部署

本方案面向 Alibaba Cloud Linux 3，使用三个容器运行网页、Java 后端和 Python 智能体。服务器只暴露网页端口，后端和智能体只在 Docker 内部网络通信。SQLite、上传资料、模型配置和报告输出均保存在 Docker 数据卷中。

## 服务器准备

当前服务器应能正常执行：

```bash
docker --version
docker compose version
docker ps
```

建议至少准备 4 核 CPU、8 GB 内存和 40 GB 可用磁盘。低于该规格仍可能运行，但处理大型 Excel、绘图和生成完整 Word 时更容易变慢或内存不足。

## 上传项目

将完整项目目录上传到服务器，例如：

```text
/opt/assess-report
```

项目至少应包含 `compose.yaml`、`frontend/`、`backend/` 和 `agent-service/`。不要把本机的 `node_modules`、`.venv`、构建产物或真实模型密钥上传到代码仓库。

进入项目目录：

```bash
cd /opt/assess-report
```

复制部署配置：

```bash
cp .env.docker.example .env.docker
```

生成随机内部密钥：

```bash
openssl rand -hex 32
```

编辑 `.env.docker`，把生成的值填入 `ASSESS_SERVICE_TOKEN`。

系统已支持邀请码注册、账号密码登录和独立用户空间。默认邀请码为 `appmath`，可在 `.env.docker` 中设置 `ASSESS_INVITE_CODE`。首次部署也可只允许本机访问：

```dotenv
APP_BIND_ADDRESS=127.0.0.1
APP_PORT=8088
```

此时可通过 SSH 端口转发安全访问：

```bash
ssh -L 8088:127.0.0.1:8088 root@服务器公网IP
```

保持 SSH 连接后，在本机浏览器打开 `http://127.0.0.1:8088`。

通过服务器公网 IP 访问时，将 `APP_BIND_ADDRESS` 改为 `0.0.0.0`，并配置阿里云安全组。登录后的资料库、报告记录和模型设置只属于当前用户。使用 HTTPS 域名部署时，在 `.env.docker` 设置 `ASSESS_COOKIE_SECURE=true`，并将 `ASSESS_ALLOWED_ORIGINS` 设置为网页完整来源（例如 `https://report.example.com`）；外层代理应转发原始 Host。

## 构建并启动

首次构建会下载 Node、Java、Python 镜像及依赖，耗时取决于服务器网络：

```bash
docker compose --env-file .env.docker build
docker compose --env-file .env.docker up -d
```

检查状态：

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs --tail=100
```

三个服务都应显示为运行中，网页和两个内部服务随后会变为 `healthy`。健康检查地址为：

```bash
curl http://127.0.0.1:8088/api/health
```

正常响应包含 `"status":"UP"`。登录后可在页面检查智能体连接状态。

打开网页后注册并登录，再在“模型设置”中填写模型名称、Base URL 和 API Key，按需执行“测试连接”。模型密钥会加密保存到 `agent-data/users/{用户ID}/`，用户资料保存在 `backend-data/users/{用户ID}/`。账号和会话位于 `backend-data/auth/`。升级前的全局数据保留在原位置，不自动分配给新账号。

## 更新、停止与查看日志

上传新版代码后重新构建：

```bash
docker compose --env-file .env.docker up -d --build
```

查看单个服务日志：

```bash
docker compose --env-file .env.docker logs -f frontend
docker compose --env-file .env.docker logs -f backend
docker compose --env-file .env.docker logs -f agent
```

停止项目但保留数据：

```bash
docker compose --env-file .env.docker down
```

不要执行带 `-v` 的 `down`，它会删除上传资料、任务记录、报告、模型配置和模型密钥。

## 数据与备份

数据保存在以下 Docker 卷中：

- `assess-report_backend-data`：上传文件和工作区记录。
- `assess-report_agent-data`：任务数据库、模型配置、加密密钥和报告输出。

成功任务自动删除中间文件，只保留最终 Word 和数据库记录；失败任务保留压缩恢复缓存。每个容器日志采用轮转，最多约10 MB。系统不会自动创建整份数据备份。

如管理员另外需要备份，可按下面步骤操作；备份前先停止服务，避免复制 SQLite 时数据正被修改：

```bash
docker compose --env-file .env.docker stop
mkdir -p backup
docker run --rm -v assess-report_backend-data:/source:ro -v "$PWD/backup":/backup alpine tar czf /backup/backend-data.tar.gz -C /source .
docker run --rm -v assess-report_agent-data:/source:ro -v "$PWD/backup":/backup alpine tar czf /backup/agent-data.tar.gz -C /source .
docker compose --env-file .env.docker start
```

必须同时备份两个卷。`agent-data` 中的 `model-config.key` 和模型配置数据库缺一不可。

## Linux 环境差异

容器已安装 Noto CJK 中文字体，图表可以显示中文。Linux 服务器没有 Microsoft Word，因此生成的 DOCX 会保留可更新的目录字段，但无法在服务器上自动刷新最终目录页码。下载报告后，在 Microsoft Word 中选中目录并执行“更新整个目录”即可。

## Portainer（可选）

Portainer 可以查看容器状态、日志和资源占用。安装后可在 Portainer 的 Stacks 页面粘贴 `compose.yaml`，但首次部署仍建议按本文命令执行，便于明确使用 `.env.docker`。Portainer 端口也应在阿里云安全组中限制来源 IP。

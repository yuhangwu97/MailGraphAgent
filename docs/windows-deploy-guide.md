# Windows 部署指南

适用于全新 Windows 机器（AMD64, 64GB RAM），从头配置 MailGraphAgent。

**架构**: 基础设施（Redis/Neo4j/Milvus）跑在 Docker，API/Worker/前端直接在 Windows 上跑。

---

## 0. 前置条件

- Windows 10/11 专业版或企业版
- 至少 16GB 内存（64GB 推荐）
- BIOS 已开启虚拟化（Intel VT-x / AMD-V，新机器默认开启）

---

## 1. 安装 Docker Desktop

Docker 只跑基础设施（Redis、Neo4j、Milvus 等），API/Worker/前端不用 Docker。

1. 下载 Docker Desktop for Windows：
   https://www.docker.com/products/docker-desktop/

2. 安装时确认勾选 **"Use WSL 2 instead of Hyper-V"**

3. 安装完成后打开 Docker Desktop，进入 Settings：
   - **General** → 勾选 "Start Docker Desktop when you sign in to your computer"
   - **Resources** → 内存建议给到 16GB+（机器有 64GB 的话给 24GB）

4. 验证（打开 PowerShell）：
   ```powershell
   docker --version
   docker compose version
   ```

## 2. 配置国内镜像/代理（重要）

如果你在国内，Docker Hub、PyPI、npm 直连都很慢甚至超时。这里有三种包管理器各自的最佳配置方式。

### 2.1 Docker：配置 Registry 镜像

打开 Docker Desktop → **设置**（右上角齿轮）→ **Docker Engine**，把 `registry-mirrors` 加到 JSON 配置里：

```json
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me",
    "https://hub.rat.dev",
    "https://docker.m.daocloud.io",
    "https://registry.cn-hangzhou.aliyuncs.com"
  ]
}
```

多配几个，Docker 会逐个尝试，哪个通就用哪个。然后点 **Apply & restart**，等 Docker Desktop 重启完再继续。

验证镜像是否生效：

```powershell
docker info | Select-String "Registry Mirrors"
```

> **小镜像够用、大镜像慢？** Registry 镜像对 Redis（~30MB）这类小镜像够快，但 Neo4j（~500MB）、Milvus（~1GB）这种大镜像在镜像源上可能同步不及时或被限速。此时代理更可靠，见下一节。

### 2.1-alt 推荐：Docker 走代理（大镜像更快）

如果你本地有代理软件（Clash Verge / v2rayN 等），**大镜像走代理通常比 Registry 镜像更快更稳**。安装 Clash Verge：https://github.com/clash-verge-rev/clash-verge-rev/releases（下载 `-setup.exe` 安装即可）。

打开 Docker Desktop → **设置** → **Resources** → **Proxies**，勾选 **Manual proxy configuration**：

```
HTTP Proxy:  http://127.0.0.1:7890    （改成你的代理端口）
HTTPS Proxy: http://127.0.0.1:7890
No Proxy:    localhost,127.0.0.1,.internal
```

Clash Verge 默认 7890，v2rayN 默认 10809，对照你用的软件填写。填完点 **Apply & restart**。

> **镜像 + 代理可以共存**：两者不冲突，镜像和代理可以同时配置。Docker 优先走镜像，镜像不可用时走代理。

### 2.2 pip：配置国内镜像

在 PowerShell 中执行：

```powershell
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

这会把镜像地址写入 `%APPDATA%\pip\pip.ini`，之后 `pip install` 自动走清华源。

也可以手动创建/编辑该文件，内容为：

```ini
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
```

其他可用源：`https://mirrors.aliyun.com/pypi/simple/`、`https://pypi.mirrors.ustc.edu.cn/simple/`

### 2.3 npm：配置国内镜像

```powershell
npm config set registry https://registry.npmmirror.com
```

验证：

```powershell
npm config get registry
```

> **注意**：`npm config set` 配置是全局的，对后续 `scripts\setup.bat` 里的 `npm install` 生效。

---

## 3. 安装 Python 3.12

1. 下载：https://www.python.org/downloads/
2. 安装时务必勾选 **"Add Python to PATH"**
3. 验证：
   ```powershell
   python --version
   ```

## 4. 安装 Node.js

1. 下载 LTS 版：https://nodejs.org/
2. 一路默认安装即可
3. 验证：
   ```powershell
   node --version
   npm --version
   ```

## 5. （跳过，不用装 Git）

## 6. 传输项目

**发送端（你当前的机器）**：打包项目（排除 `.venv`、`node_modules`、`data` 和 `.git` 以减小体积），通过 ToDesk 文件传输发过去。

**接收端（Windows 机器）**：把压缩包解压到桌面（或任意目录），进入项目文件夹：

```powershell
cd ~/Desktop/MailGraphAgent
```

## 7. 配置环境变量

```powershell
# 从模板创建 .env
copy .env.example .env

# 用记事本编辑 .env
notepad .env
```

**必须修改的项：**

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | LLM API Key（阿里云百炼或其他 OpenAI 兼容接口） |
| `OPENAI_BASE_URL` | API 地址 |
| `OPENAI_MODEL` | 模型名称，如 `qwen-plus` |
| `IMAP_SERVER` | IMAP 邮件服务器地址 |
| `IMAP_PORT` | 邮件服务器端口（通常 993） |
| `EMAIL_USER` | 邮箱账号 |
| `EMAIL_PASS` | 邮箱密码或应用专用密码 |

**不需要改的项**（已默认 localhost，Docker 端口已映射到宿主机）：
- `REDIS_HOST=localhost` / `REDIS_PORT=6379`
- `NEO4J_URI=bolt://localhost:7687`
- `MILVUS_URI=http://localhost:19530`

## 8. 启动 Docker 基础设施（Neo4j / Redis / MinIO）

API 和 Worker 启动前需要先跑起这些基础服务。

### 8.1 推荐方式：docker compose 一键启动

项目根目录已提供 `docker-compose.yml`，一条命令启动全部 5 个容器（Redis、etcd、MinIO、Milvus、Neo4j）：

```powershell
cd ~/Desktop/MailGraphAgent
docker compose up -d
```

等待所有容器健康检查通过（首次拉取镜像可能需要几分钟）：

```powershell
docker compose ps
```

看到所有容器 STATUS 都是 `healthy` 即就绪。

### 8.2 逐个 docker run（备选）

如果不方便用 compose，也可以用以下命令逐个启动。

**建议先一次性拉取所有镜像**（避免启动时等待）：

```powershell
docker pull redis:7-alpine
docker pull neo4j:5.26-community
docker pull minio/minio:latest
docker pull quay.io/coreos/etcd:v3.5.5
docker pull milvusdb/milvus:v2.5.4
```

> **拉取慢/进度条不动？** Neo4j 镜像约 500MB，Milvus 约 1GB，国内网络下可能很慢。进度条长时间卡住（例如 `Downloading [====> ] 16.78MB/167.8MB` 好几分钟不动）是正常现象 — Docker Desktop on Windows 的进度显示有时不实时刷新，实际仍在下载。耐心等待即可，不要 Ctrl+C 中断，否则会损坏已下载的层导致重头再来。如果确实超时失败，检查第 2 节 Registry 镜像是否配置正确。

**注意**：MinIO 必须先于 Milvus 启动，etcd 必须先于 Milvus 启动。

#### Redis

```powershell
# 先拉镜像
docker pull redis:7-alpine

# 再启动容器
docker run -d `
  --name mailgraph-redis `
  --restart unless-stopped `
  -p 6379:6379 `
  -v "$env:USERPROFILE\MailGraphAgent\data\redis:/data" `
  redis:7-alpine `
  redis-server --appendonly yes --requirepass mailgraph2024
```

对应 `.env` 配置：

| 变量 | 值 |
|------|-----|
| `REDIS_HOST` | `localhost` |
| `REDIS_PORT` | `6379` |
| `REDIS_DB` | `0` |
| `REDIS_PASSWORD` | `mailgraph2024` |

#### Neo4j

```powershell
# 先拉镜像
docker pull neo4j:5.26-community

# 再启动容器
docker run -d `
  --name mailgraph-neo4j `
  --restart unless-stopped `
  -p 7474:7474 -p 7687:7687 `
  -e NEO4J_AUTH=neo4j/mailgraph2024 `
  -e NEO4J_PLUGINS='["apoc"]' `
  -v "$env:USERPROFILE\MailGraphAgent\data\neo4j:/data" `
  neo4j:5.26-community
```

对应 `.env` 配置：

| 变量 | 值 |
|------|-----|
| `NEO4J_URI` | `bolt://localhost:7687` |
| `NEO4J_USER` | `neo4j` |
| `NEO4J_PASSWORD` | `mailgraph2024` |

首次启动需 30 秒左右初始化，可以用以下命令检查是否就绪：

```powershell
docker logs mailgraph-neo4j 2>&1 | Select-String "Started"
```

看到 `Started.` 即就绪。也可以浏览器打开 [http://localhost:7474](http://localhost:7474) 验证。

#### MinIO

```powershell
# 先拉镜像
docker pull minio/minio:latest

# 再启动容器
docker run -d `
  --name mailgraph-minio `
  --restart unless-stopped `
  -p 9000:9000 -p 9001:9001 `
  -e MINIO_ROOT_USER=admin `
  -e MINIO_ROOT_PASSWORD=mailgraph2024 `
  -v "$env:USERPROFILE\MailGraphAgent\data\minio:/data" `
  minio/minio:latest `
  server /data --console-address ":9001"
```

MinIO 控制台：[http://localhost:9001](http://localhost:9001)，用户名 `admin`，密码 `mailgraph2024`。

#### etcd（Milvus 依赖）

```powershell
# 先拉镜像
docker pull quay.io/coreos/etcd:v3.5.5

# 再启动容器
docker run -d `
  --name mailgraph-etcd `
  --restart unless-stopped `
  -p 2379:2379 `
  -e ETCD_AUTO_COMPACTION_MODE=revision `
  -e ETCD_AUTO_COMPACTION_RETENTION=1000 `
  -e ETCD_QUOTA_BACKEND_BYTES=4294967296 `
  -e ETCD_SNAPSHOT_COUNT=50000 `
  -v "$env:USERPROFILE\MailGraphAgent\data\etcd:/etcd" `
  quay.io/coreos/etcd:v3.5.5 `
  etcd -advertise-client-urls=http://127.0.0.1:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd
```

#### Milvus

```powershell
# 先拉镜像
docker pull milvusdb/milvus:v2.5.4

# 再启动容器
docker run -d `
  --name mailgraph-milvus `
  --restart unless-stopped `
  -p 19530:19530 `
  -e ETCD_ENDPOINTS=mailgraph-etcd:2379 `
  -e MINIO_ADDRESS=mailgraph-minio:9000 `
  -e MINIO_ACCESS_KEY_ID=admin `
  -e MINIO_SECRET_ACCESS_KEY=mailgraph2024 `
  -v "$env:USERPROFILE\MailGraphAgent\data\milvus:/var/lib/milvus" `
  milvusdb/milvus:v2.5.4 `
  milvus run standalone
```

> **注意**：Milvus 依赖 etcd 和 MinIO，需要等前两者健康后再启动，首次启动需 1-2 分钟。Milvus 不需要在 `.env` 中额外配置凭据，只需 `MILVUS_URI=http://localhost:19530`。

### 8.3 验证所有服务

```powershell
# 检查容器运行状态
docker ps --filter "name=mailgraph-" --format "table {{.Names}}\t{{.Status}}"

# Redis 连通性
docker exec mailgraph-redis redis-cli -a mailgraph2024 ping
# 应返回 PONG

# Neo4j 连通性
curl http://localhost:7474
# 应返回 JSON 包含 "neo4j_version"

# MinIO 连通性
curl http://localhost:9000/minio/health/live
# 应返回 200 OK
```

---

## 9. 一键安装依赖

双击 `scripts\setup.bat`，或者在项目目录下 PowerShell 执行：

```powershell
.\scripts\setup.bat
```

这一步会：
- 创建 Python 虚拟环境 `.venv`
- 安装 Python 依赖（`requirements.txt`）
- 安装前端依赖（`npm install`）

只需执行一次，之后不用再跑。

## 10. 启动服务

双击 `scripts\start.bat`，或者在项目目录下 PowerShell 执行：

```powershell
.\scripts\start.bat
```

这一步会：
- 启动 Docker 基础设施（如果还没启动）
- 等待所有服务就绪
- 弹出三个命令行窗口分别跑 API、Worker、前端

> 如果你已按第 8 节手动启动了 Docker 容器，`start.bat` 会跳过 Docker 启动步骤，直接拉起 API/Worker/前端。

## 11. 访问

- 前端工作台：**http://localhost:5173**
- API 文档：**http://localhost:8000/docs**
- Neo4j Browser：**http://localhost:7474**
- MinIO 控制台：**http://localhost:9001**

## 12. 停止服务

关闭弹出的三个命令行窗口，然后在项目目录下：

```powershell
docker compose down
```

如果是逐个 docker run 启动的，则：

```powershell
docker stop mailgraph-redis mailgraph-neo4j mailgraph-minio mailgraph-milvus mailgraph-etcd
docker rm mailgraph-redis mailgraph-neo4j mailgraph-minio mailgraph-milvus mailgraph-etcd
```

## 13. 开机自启

**Docker 基础设施**：Docker Desktop 已设开机自启 + 容器 `restart: unless-stopped`。

**API / Worker / 前端**：新建一个计划任务，在用户登录时自动执行 `start.bat`：

以**管理员**身份打开 PowerShell：

```powershell
$projectDir = "$env:USERPROFILE\MailGraphAgent"

$action = New-ScheduledTaskAction `
    -Execute "$projectDir\scripts\start.bat" `
    -WorkingDirectory $projectDir

$trigger = New-ScheduledTaskTrigger -AtLogon

Register-ScheduledTask `
    -TaskName "MailGraphAgent" `
    -Action $action `
    -Trigger $trigger `
    -Description "启动 MailGraphAgent 全部服务"
```

---

## 常见问题

### Docker 镜像拉取失败（"dial tcp ... connectex" 或 timeout）

这是 Docker 直连 Docker Hub 被墙/超时。按本文 **第 2 节** 配置 Registry 镜像或代理即可解决。

```powershell
# 验证镜像是否生效
docker info | Select-String "Registry Mirrors"
```

### Docker 容器启动失败

```powershell
docker compose down
docker compose up -d
```

### 端口被占用

如果 8000/5173/6379 等被其他程序占用，修改 `.env` 中对应端口，以及 `docker-compose.yml` 中宿主机侧端口。

### Milvus 启动慢

Milvus 需要 etcd + MinIO 先就绪，首次启动需要 1-2 分钟，`start.bat` 会自动等。

### 虚拟环境问题

如果 `start.bat` 报找不到 Python 包，先确定 `setup.bat` 跑过一次：

```powershell
.\scripts\setup.bat
```

### .env 配置验证

```powershell
python -c "from config.settings import get_settings; s=get_settings(); print('OK - Neo4j:', s.neo4j_uri, 'Milvus:', s.milvus_uri)"
```

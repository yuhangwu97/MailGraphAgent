# Windows Docker 生产部署适配 — 设计文档

**日期**: 2026-07-14
**状态**: 已确认
**目标**: 将 MailGraphAgent 以 Docker Compose 方式部署到 Windows（Docker Desktop WSL2, AMD64, 64GB RAM）生产环境，处理几十 GB 邮件数据。

---

## 背景

项目已有完整的 Docker 化配置（`Dockerfile` + `docker-compose.yml`，7 个服务），但现有配置基于 macOS ARM64 开发环境编写，部署到 Windows/AMD64 生产环境存在 3 个关键问题需解决。

## 评估矩阵

| 维度 | 当前状态 | 目标状态 |
|------|----------|----------|
| 架构兼容性 | ARM64 优化（apt mirror, torch CPU-only） | AMD64，镜像源可选 |
| 文件 I/O | macOS 原生文件系统 | WSL2 原生 ext4（禁跨文件系统） |
| 端口安全 | 8 个端口全部对外暴露 | 仅 API(8000) + Web(8080) 对外 |
| 启动顺序 | `service_started`（进程起即启动） | `service_healthy`（健康检查通过才启动） |
| 开机自启 | 无 | Docker Desktop 自启 + `restart: unless-stopped` |
| 数据备份 | 无 | 计划任务 + 备份脚本 |
| 内存管控 | 无 | `.wslconfig` 限制 24GB |

---

## 改动清单

### 改动 1：Dockerfile — apt 镜像 ARG 化

**文件**: `Dockerfile` 第 14-18 行

**现状**: 写死阿里云 apt 镜像替换，`|| true` 兜底。
**改为**: `ARG USE_ALIYUN_MIRROR=true` 控制，默认值 `true` 保持现有行为。

```dockerfile
ARG USE_ALIYUN_MIRROR=true
RUN if [ "$USE_ALIYUN_MIRROR" = "true" ]; then \
        sed -i 's|^URIs: http://deb\.debian\.org/debian$|URIs: http://mirrors.aliyun.com/debian|' \
            /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
        sed -i 's|^URIs: http://deb\.debian\.org/debian-security$|URIs: http://mirrors.aliyun.com/debian-security|' \
            /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    fi
```

**影响**: 默认行为不变。海外构建传 `--build-arg USE_ALIYUN_MIRROR=false` 即可。

### 改动 2：WSL2 环境配置（运维操作，不涉及代码）

**2a. `.wslconfig`**

在 Windows `%USERPROFILE%\.wslconfig` 创建：

```ini
[wsl2]
memory=24GB
processors=8
swap=4GB
```

**2b. 项目路径**

项目必须放在 WSL2 原生文件系统（`/home/<user>/MailGraphAgent`），**禁止**放 `/mnt/c/...`（9p 协议 I/O 性能差 10-50x）。

**2c. 启动**

```bash
cd ~/MailGraphAgent
docker compose up -d
```

### 改动 3：docker-compose — 端口收紧

**文件**: `docker-compose.yml`

**现状**: redis(6379)、neo4j(7474,7687)、minio(9000,9001)、milvus(19530) 全部对外暴露。
**改为**: 仅 api(8000) 和 web(8080) 保留 `ports`，其余服务 `ports` 段删除（容器间通过 `mailgraph` bridge 网络通信）。

需要调试 Neo4j Browser 时通过临时 compose override 文件暴露端口，不写死在生产配置中。

### 改动 4：docker-compose — 启动顺序强化

**文件**: `docker-compose.yml` api / worker 的 `depends_on`

**现状**: `condition: service_started`
**改为**: `condition: service_healthy`

```yaml
api:
  depends_on:
    redis:    { condition: service_healthy }
    neo4j:    { condition: service_healthy }
    milvus:   { condition: service_healthy }

worker:
  depends_on:
    redis:    { condition: service_healthy }
    neo4j:    { condition: service_healthy }
    milvus:   { condition: service_healthy }
```

注意：`condition: service_healthy` 要求被依赖的服务已定义 `healthcheck`。当前所有被依赖服务（redis, neo4j, milvus）均有 healthcheck，无需新增。

### 改动 5：数据备份（运维操作，提供脚本）

**备份对象**:

| 数据 | 路径 | 方式 | 停服要求 |
|------|------|------|----------|
| Neo4j 图谱 | `data/neo4j/` | `tar czf`（或 `neo4j-admin dump`） | 建议停服 |
| Milvus 向量 | `data/milvus/` | `tar czf` | 必须停服 |
| Redis AOF | `data/redis/` | 直接复制 `appendonly.aof` | 无需停服 |
| LightRAG KV | `data/lightrag/` | `tar czf` | 无需停服 |

**自动化**: Windows 计划任务 → 调用 WSL2 内 `~/MailGraphAgent/scripts/backup.sh` → 输出到 `/mnt/d/backups/mailgraph/YYYY-MM-DD/`。

---

## 不改动的部分

- `docker-compose.yml` 的服务拓扑（7 服务、bridge 网络、卷挂载）保持不变
- `Dockerfile` 的 torch CPU-only 预装保留（AMD64 上无害且减小镜像体积）
- 前端 `src/web/Dockerfile` 不变
- `.dockerignore` 不变
- `.env` / `environment` 变量映射不变（compose 中已正确覆盖 localhost）

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| Milvus 在 Windows/WSL2 偶发权限问题 | `data/milvus/` 目录 `chmod 777`（WSL2 挂载卷已知问题） |
| Docker Desktop 更新后自动重启失败 | 设置 Docker Desktop → "Start when you log in"，定期检查 |
| 几十 GB 数据首次导入时 Worker OOM | Worker 的 LightRAG 分批复用；必要时 `docker compose run` 单独跑 worker 限制 `--memory=16g` |
| 端口已占用（8000/8080） | 修改 compose 中宿主机侧端口映射，如 `"18000:8000"` |

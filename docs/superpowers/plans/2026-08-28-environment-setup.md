# 实施环境准备文档

> 本文档配套：`docs/superpowers/plans/2026-08-28-mvp-phase-1-infra-auth.md`

## TL;DR — 必须使用 Docker

**结论：所有基础设施（Postgres / Dify / Nginx / MinIO）都用 Docker 容器运行。**

理由：

1. **环境一致性**：开发、测试、生产用同一套镜像，避免"在我机器上能跑"
2. **Dify 自带 docker-compose**：官方推荐部署方式
3. **本地零污染**：不需要在本机装 Postgres / Redis
4. **重建成本低**：`docker compose down -v && up` 5 分钟干净环境
5. **网络隔离容易**：开发时 Dify 不暴露给同事，仅容器间通信

唯一例外：**后端 / 前端开发模式**用本机 `uvicorn --reload` / `vite dev`，因为热重载 + IDE 调试体验远好于容器内开发。

---

## 1. 系统要求

| 组件 | 最低 | 推荐 |
|---|---|---|
| CPU | 4 核 | 8 核 |
| 内存 | 8 GB | 16 GB |
| 磁盘 | 30 GB 可用 | 50 GB SSD |
| OS | macOS 12+ / Ubuntu 22.04+ / Windows 11 WSL2 | macOS 14 / Ubuntu 24.04 |
| 网络 | 可访问 docker hub | 同左 |

**Windows 用户**：必须用 WSL2（Docker Desktop 已默认开启）。文件存放建议放 WSL2 文件系统（`\\wsl$\...`），性能比 `/mnt/c/...` 高 5–10 倍。

---

## 2. 必装工具

### 2.1 Docker Desktop / Docker Engine

| OS | 安装方式 |
|---|---|
| macOS | [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) |
| Windows | [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)（含 WSL2） |
| Ubuntu | `sudo apt install docker.io docker-compose-plugin` |

**版本要求**：Docker Engine ≥ 24.0，Compose ≥ v2.20。

验证：

```bash
docker --version        # Docker version 24.x+
docker compose version  # Docker Compose version v2.x+
docker info             # 确认 Server 段不报错
```

### 2.2 Python 3.11+

| OS | 安装 |
|---|---|
| macOS | `brew install python@3.11` |
| Ubuntu | `sudo apt install python3.11 python3.11-venv` |
| Windows WSL2 | 同 Ubuntu |

**强烈建议**：用 `pyenv` 或 `uv` 管理多版本（项目要求 ≥3.11）。

如果用 `uv`（推荐，更快）：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# 进入项目后
cd backend
uv sync                  # 创建 .venv + 装依赖
uv run uvicorn app.main:app --reload
```

### 2.3 Node.js 20+

| OS | 安装 |
|---|---|
| macOS | `brew install node@20` |
| Ubuntu | `sudo apt install nodejs npm`（默认源可能版本旧，建议用 nvm） |
| Windows WSL2 | 同 Ubuntu |

**强烈建议**：用 `nvm` 装：

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
nvm install 20
nvm use 20
```

### 2.4 Git

任何 ≥ 2.30 版本都行。

### 2.5 验证整体环境

```bash
docker --version
docker compose version
python3.11 --version   # 或 python --version
node --version
git --version
```

---

## 3. Docker 镜像清单

> **所有镜像都用具体版本号，不要用 `latest`**——避免不可重现的构建。

### 3.1 官方镜像（公开 Docker Hub）

| 镜像 | 版本标签 | 用途 | 大小 |
|---|---|---|---|
| `postgres` | `16-alpine` | 主数据库 | ~80 MB |
| `redis` | `7-alpine` | 仅 Dify 用 | ~40 MB |
| `nginx` | `1.25-alpine` | 反向代理 + 前端静态服务 | ~45 MB |
| `minio/minio` | `RELEASE.2024-08-29T01-40-52Z` | 文件存储（二期） | ~150 MB |
| `python` | `3.11-slim-bookworm` | 后端容器构建基础 | ~120 MB |

**为什么用 `-alpine` 或 `-slim`？** 体积小、攻击面小、启动快。

### 3.2 Dify 镜像（来自 Dify 官方仓库）

Dify 的所有镜像都在 Docker Hub 的 `langgenius/` 命名空间下，**不要自己 build**：

| 镜像 | 版本标签 | 用途 |
|---|---|---|
| `langgenius/dify-api` | `1.1.0` | Dify API 服务 |
| `langgenius/dify-web` | `1.1.0` | Dify Web UI |
| `langgenius/dify-sandbox` | `0.2.1` | 代码执行沙箱 |
| `langgenius/dify-plugin-daemon` | `0.0.2-local` | 插件运行时 |
| `langgenius/dify-plugin-runtime` | `0.0.2-local` | 插件运行时 |
| `nginx` | `latest`（Dify 自带） | Dify 自带的反向代理 |

**镜像版本锁定方法**：

```bash
cd deploy/dify
# 拉取后查看镜像 digest
docker pull langgenius/dify-api:1.1.0
docker inspect --format='{{index .RepoDigests 0}}' langgenius/dify-api:1.1.0
# → langgenius/dify-api@sha256:abc123...

# 在 docker-compose.yaml 中改用 digest（最严格的版本锁定）
# image: langgenius/dify-api@sha256:abc123...
```

### 3.3 本项目自建镜像（后端 / 前端）

后端 `backend/Dockerfile`：

```dockerfile
# 构建阶段
FROM python:3.11-slim-bookworm AS builder

WORKDIR /app

# 系统依赖（编译 cryptography 等需要的）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 装依赖到独立目录
COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install ".[dev]"

# 运行阶段
FROM python:3.11-slim-bookworm

WORKDIR /app

# 运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 appuser

# 拷贝构建好的依赖
COPY --from=builder /install /usr/local

# 拷贝应用代码
COPY --chown=appuser:appuser app /app/app
COPY --chown=appuser:appuser alembic /app/alembic
COPY --chown=appuser:appuser alembic.ini /app/

USER appuser

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# 启动：先跑迁移再启 uvicorn
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]
```

前端 `frontend/Dockerfile`（多阶段构建）：

```dockerfile
# 构建阶段：装依赖 + 编译 TS
FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# 运行阶段：nginx 提供静态文件
FROM nginx:1.25-alpine

# 删掉默认配置
RUN rm /etc/nginx/conf.d/default.conf

# 拷贝构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx 配置（SSE 必需）
COPY nginx.conf /etc/nginx/conf.d/portal.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
```

### 3.4 镜像版本核对清单

启动前必查的 6 个版本（避免日后"为什么我这跑不通"的争议）：

```bash
# 1. Postgres
docker run --rm postgres:16-alpine postgres --version
# → PostgreSQL 16.x

# 2. Redis（Dify 用）
docker run --rm redis:7-alpine redis-server --version
# → Redis server v=7.x

# 3. Python 基础镜像
docker run --rm python:3.11-slim-bookworm python --version
# → Python 3.11.x

# 4. Node 基础镜像
docker run --rm node:20-alpine node --version
# → v20.x

# 5. Nginx
docker run --rm nginx:1.25-alpine nginx -v
# → nginx version: nginx/1.25.x

# 6. Dify API
docker run --rm --entrypoint echo langgenius/dify-api:1.1.0 "Dify API 1.1.0"
```

### 3.5 镜像加速（中国大陆用户）

默认 Docker Hub 国内访问慢，加镜像源：

`~/.docker/daemon.json`：

```json
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com"
  ],
  "max-concurrent-downloads": 5
}
```

```bash
sudo systemctl restart docker
docker info | grep -A 5 "Registry Mirrors"
```

---

## 4. 端口规划

避免端口冲突，先确认本地没占用：

| 端口 | 用途 | 容器/进程 |
|---|---|---|
| 5432 | Postgres | docker: ap-postgres |
| 5001 | Dify API | docker: dify-api |
| 3000 | Dify Web UI | docker: dify-web |
| 6379 | Dify 内置 Redis | docker: dify-redis |
| 8000 | FastAPI 后端（dev） | 本机 uvicorn |
| 5173 | Vite 前端（dev） | 本机 vite dev |
| 80/443 | Nginx（生产） | docker: ap-nginx |

**冲突检查**：

```bash
# Linux/macOS
for port in 5432 5001 3000 6379 8000 5173; do
  lsof -i :$port || echo "$port: free"
done
```

---

## 5. 容器编排（deploy/）

### 5.1 文件结构

```
deploy/
├── docker-compose.yml          # 主编排（Postgres + Dify + Nginx + 后端 + 前端）
├── docker-compose.override.yml # 本地开发覆盖（dev 配置、挂载代码）
├── .env.example
├── nginx/
│   └── conf.d/portal.conf
├── dify/                       # Dify 官方 compose（vendored 或子模块）
│   ├── docker-compose.yaml
│   └── .env.example
└── scripts/
    ├── init-dify.sh            # 初始化 Dify 管理员
    └── seed-admin.sh           # 创建 PLATFORM_ADMIN
```

### 5.2 docker-compose.yml（开发模式）

> **注**：Dify 推荐用其官方 compose，本项目 `deploy/docker-compose.yml` 只声明本项目自己的服务，**Dify 单独部署**。

**为什么不把 Dify 放进本项目 compose？**
- Dify 官方 compose 自带 Postgres + Redis + 5 个微服务（api/worker/web/nginx/SSRF proxy），耦合度高
- 升级 Dify 时只想替换它的目录，不希望牵连本项目
- 本项目开发者经常需要单独重启 FastAPI，不希望连带重启 Dify

**本项目 compose 启动的服务**：

```yaml
# deploy/docker-compose.yml
services:
  postgres:
    image: postgres:16-alpine
    container_name: ap-postgres
    environment:
      POSTGRES_DB: agent_platform
      POSTGRES_USER: agent
      POSTGRES_PASSWORD: agent_dev_only
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init-postgres.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agent"]
      interval: 5s
      timeout: 5s
      retries: 10

  # 后端（生产用，开发用本机 uvicorn --reload）
  backend:
    build: ../backend
    container_name: ap-backend
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+asyncpg://agent:agent_dev_only@postgres:5432/agent_platform
      DIFY_BASE_URL: http://host.docker.internal:5001
      JWT_SECRET: ${JWT_SECRET}
      ENCRYPTION_KEY: ${ENCRYPTION_KEY}
      # ...
    ports:
      - "8000:8000"
    volumes:
      - ../backend/app:/app/app  # 容器内热重载

volumes:
  postgres_data:
```

**Dify 单独部署**：

```bash
cd deploy/dify
cp .env.example .env  # 编辑 POSTGRES/REDIS 指向外部
docker compose -f docker-compose.yaml up -d
# 等 1–2 分钟
curl http://localhost:5001/v1/setup/initialize-status  # → {"finished": false}（首次）
```

### 5.3 docker-compose.override.yml（dev 覆盖）

```yaml
# deploy/docker-compose.override.yml
# 用途：本地开发时挂载代码、暴露调试端口
services:
  backend:
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    volumes:
      - ../backend/app:/app/app
      - ../backend/tests:/app/tests
    environment:
      DEBUG: "true"
      LOG_LEVEL: "DEBUG"

  postgres:
    ports:
      - "5432:5432"
```

### 5.4 启动顺序

```bash
# 1. 启动 Postgres（最先）
cd deploy
docker compose up -d postgres
# 验证
docker exec ap-postgres psql -U agent -c "\l"  # 看到 agent_platform 库

# 2. 启动 Dify（独立目录）
cd dify
docker compose -f docker-compose.yaml up -d
# 等待健康
docker compose -f docker-compose.yaml ps  # 全部 healthy

# 3. 初始化 Dify 管理员
curl -X POST http://localhost/v1/setup/init-admin \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@dify.local","password":"DifyAdmin123!","name":"Dify Admin"}'

# 4. 启动后端（生产容器 OR 本机 dev）
# 容器：
cd deploy && docker compose up -d backend
# OR 本机：
cd backend && uv run uvicorn app.main:app --reload --port 8000

# 5. 启动前端
cd frontend && npm install && npm run dev  # 默认 http://localhost:5173
```

---

## 6. 后端本地开发（推荐方式）

**为什么后端用本机 `uvicorn --reload` 而不是容器？**

- `pip install -e .` 后改代码即时生效（容器内 -v 挂载也行，但 watch 经常失效）
- IDE 调试（VSCode / PyCharm）直接 attach 进程
- pytest 跑得快（无容器网络延迟）

### 6.1 一次性初始化

```bash
cd backend
python -m venv .venv          # 或 uv venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"       # 或 uv sync
cp .env.example .env
# 编辑 .env：
#   JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
#   ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

### 6.2 数据库初始化

```bash
# Postgres 已经在 docker 跑，本机连 localhost:5432
alembic upgrade head
python -m app.scripts.seed_admin
# 输入 ADMIN_EMAIL/ADMIN_PASSWORD（或在 .env 预置）
```

### 6.3 启动 dev server

```bash
uvicorn app.main:app --reload --port 8000
# 验证
curl http://localhost:8000/api/health
# → {"status":"ok","version":"0.1.0"}
```

### 6.4 运行测试

```bash
# 必须先有测试库
docker exec -it ap-postgres psql -U agent -c "CREATE DATABASE agent_platform_test;"

pytest -v
pytest -v --cov=app --cov-report=term-missing  # 带覆盖率
```

---

## 7. 前端本地开发

```bash
cd frontend
npm install            # 或 pnpm install / yarn
npm run dev            # 默认 http://localhost:5173

# Vite 已配置 proxy：/api/* → http://localhost:8000
# 所以前端无需关心后端地址
```

---

## 8. 推荐的 IDE 配置

### 8.1 VSCode（推荐）

`.vscode/settings.json`：

```json
{
  "python.defaultInterpreterPath": "backend/.venv/bin/python",
  "[python]": {
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "ms-python.black-formatter"
  },
  "[typescript]": {
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "files.associations": {
    "*.css": "tailwindcss"
  }
}
```

`.vscode/launch.json`（调试 FastAPI）：

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "FastAPI",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["app.main:app", "--reload", "--port", "8000"],
      "cwd": "${workspaceFolder}/backend",
      "envFile": "${workspaceFolder}/backend/.env"
    }
  ]
}
```

### 8.2 PyCharm / WebStorm

- 选 backend/.venv/bin/python 为 Project SDK
- Run/Debug Configurations → Python → Module name = `uvicorn`，Parameters = `app.main:app --reload --port 8000`，Environment file = `backend/.env`

---

## 9. 关键环境变量

`backend/.env` 必备：

```bash
# 必须：32+ 字节随机字符串
JWT_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(32))">

# 必须：Fernet 格式 base64
ENCRYPTION_KEY=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Postgres（与 docker-compose 一致）
DATABASE_URL=postgresql+asyncpg://agent:agent_dev_only@localhost:5432/agent_platform

# Dify（注意：宿主机访问 Docker 容器内的 Dify 用 host.docker.internal 或容器 IP）
DIFY_BASE_URL=http://localhost:5001

# Dify 管理员（用于拉取应用列表的运维脚本）
DIFY_ADMIN_EMAIL=admin@dify.local
DIFY_ADMIN_PASSWORD=DifyAdmin123!

# 可选：种子管理员（首次启动时创建）
ADMIN_EMAIL=admin@company.local
ADMIN_PASSWORD=AdminPass123
ADMIN_NAME=平台管理员

# 开发模式
DEBUG=true
LOG_LEVEL=DEBUG
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

**⚠️ 安全警告**：上述密码仅用于本地开发。生产环境必须：
- JWT_SECRET ≥ 64 字节随机
- ENCRYPTION_KEY 用 KMS 管理
- 所有密码不进 git，用 secrets 管理平台

---

## 10. 常见问题排查

### Q1: `docker compose` 找不到命令

**症状**：`command not found: docker compose` 或 `docker-compose`

**原因**：旧版 Docker 用 `docker-compose`（带横杠），新版用 `docker compose`（空格）

**解决**：
```bash
# 检查 docker compose plugin
docker compose version
# 如未装（Ubuntu）：
sudo apt install docker-compose-plugin
```

### Q2: Postgres 容器起不来，`port 5432 already in use`

**原因**：本机已装 Postgres 占用了 5432

**解决（选项）**：
- A. 停掉本机 Postgres：`sudo systemctl stop postgresql`
- B. 改 compose 端口为 5433：`"5433:5432"`，并相应修改 `DATABASE_URL`

### Q3: 后端连不上 Dify `connection refused`

**原因**：宿主机无法直接访问容器内的 Dify（除非端口映射）

**解决**：
- macOS/Windows：默认 `http://localhost:5001` 可达（Docker Desktop 自动转发）
- Linux：要么把 Dify 端口映射出来（修改 dify/docker-compose.yaml 加 `ports: - "5001:5001"`），要么用 `http://host.docker.internal:5001`

### Q4: alembic 报错 `connection refused`

**症状**：`asyncpg.exceptions.CannotConnectNowError`

**原因**：Postgres 还没完全启动

**解决**：compose 已配 healthcheck，本机连 `localhost:5432` 健康前可手动等几秒：

```bash
until docker exec ap-postgres pg_isready -U agent; do sleep 1; done
alembic upgrade head
```

### Q5: 改了 .env 但 uvicorn --reload 没生效

**原因**：uvicorn 默认不监听 .env 变化（除非用 `uvicorn[standard]` 的 watchgod）

**解决**：手动 Ctrl+C 重新启动。或：

```bash
uvicorn app.main:app --reload --reload-include="*.env" --port 8000
```

### Q6: Docker 磁盘爆了

```bash
# 清理未用镜像/容器/卷
docker system prune -a --volumes
# 仅清理本项目
cd deploy && docker compose down -v
```

### Q7: 前端 Vite proxy 报 502

**原因**：后端没起，或后端在别的端口

**验证**：
```bash
curl http://localhost:8000/api/health
```

### Q8: pytest 报 `event loop` 错误

**症状**：`RuntimeError: Event loop is closed`

**原因**：pytest-asyncio 配置与 fixture scope 不匹配

**解决**：本项目 `pyproject.toml` 已配 `asyncio_mode = "auto"` + session-scope event_loop fixture，如仍报错，检查是否有 sync fixture 误用了 async session。

### Q9: Dify Web UI 进不去

```bash
# 检查所有 Dify 服务是否 healthy
cd deploy/dify
docker compose -f docker-compose.yaml ps
# 若 unhealthy，看日志
docker compose -f docker-compose.yaml logs --tail=100 api
```

常见原因：Postgres 没外部化配置正确 / 端口冲突。

---

## 11. 验证清单：环境就绪

跑完上述步骤后，逐项验证：

- [ ] `docker --version` ≥ 24.0
- [ ] `docker compose up -d postgres` 成功，`pg_isready` 通过
- [ ] `docker compose -f docker-compose.yaml up -d` (Dify) 全部 healthy
- [ ] 浏览器访问 http://localhost/install 能进 Dify 初始化页
- [ ] `alembic upgrade head` 无报错
- [ ] `python -m app.scripts.seed_admin` 创建 PLATFORM_ADMIN 成功
- [ ] `uvicorn app.main:app --reload` 启动成功，`curl http://localhost:8000/api/health` 返回 200
- [ ] `pytest -v` 全部通过
- [ ] `npm run dev` 启动成功，浏览器访问 http://localhost:5173 看到登录页
- [ ] 登录后能调通 Dify API（用 Dify 创建的测试 Agent）

环境就绪后，按 `docs/superpowers/plans/2026-08-28-mvp-phase-1-infra-auth.md` 的 Task 0.1 开始实施。

---

## 12. 进阶：性能调优（可选）

### 12.1 Postgres 调优（容器）

`deploy/docker-compose.yml` 加：

```yaml
postgres:
  command:
    - "postgres"
    - "-c"
    - "shared_buffers=256MB"
    - "-c"
    - "max_connections=200"
    - "-c"
    - "work_mem=4MB"
```

### 12.2 Docker Desktop 资源限制

`Docker Desktop → Settings → Resources`：
- CPUs: 4+
- Memory: 8 GB+
- Swap: 2 GB
- Disk image size: 60 GB+

### 12.3 WSL2 性能（Windows）

`.wslconfig`（Windows 用户主目录）：

```ini
[wsl2]
memory=8GB
processors=4
swap=2GB
```

项目代码务必放 `\\wsl$\...\home\<user>\projects\`（WSL2 内部文件系统），不要放 `/mnt/c/...`（Windows 文件系统，性能差 10 倍）。

---

## 13. 快速参考

| 操作 | 命令 |
|---|---|
| 启动所有服务 | `cd deploy && docker compose up -d` |
| 停止所有服务 | `cd deploy && docker compose down` |
| 重置 Postgres 数据 | `cd deploy && docker compose down -v && docker compose up -d postgres` |
| 进入 Postgres shell | `docker exec -it ap-postgres psql -U agent -d agent_platform` |
| 看后端日志（容器） | `docker logs -f ap-backend` |
| 重启后端（容器） | `docker compose restart backend` |
| 重跑迁移 | `cd backend && alembic upgrade head` |
| 回退一次迁移 | `cd backend && alembic downgrade -1` |
| 看 Dify 日志 | `cd deploy/dify && docker compose -f docker-compose.yaml logs -f` |
| 完整环境清理 | `docker system prune -a --volumes` |
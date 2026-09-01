# 企业内部 Agent 平台 MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建企业内部 Agent 智能体平台的 MVP——面向员工的对话门户，复用 Dify 社区版作为编排/RAG 引擎，由 FastAPI 作为鉴权 + 代理 + 审计层。

**Architecture:** 三层架构 —— React SPA（员工入口） ↔ FastAPI 后端（鉴权 / 授权 / 代理 / 审计） ↔ Dify 社区版（编排 / RAG / 工作流）。SSE 流式透传，对话镜像落库，离职联动禁用。

**Tech Stack:**
- 前端：React 18 + TypeScript + Vite + Ant Design 5 + Zustand
- 后端：Python 3.11+ / FastAPI / SQLAlchemy 2.0 async / Pydantic v2 / httpx / python-jose / passlib[bcrypt]
- 数据库：PostgreSQL 16
- 引擎：Dify 社区版（官方 Docker Compose）
- 部署：Docker Compose + Nginx

**关联文档：**
- 设计文档：`docs/superpowers/specs/2026-08-28-agent-platform-design.md`

---

## 文件结构总览

实施前必须先创建这个仓库骨架（Task 0）。后续每个 Task 都在这个骨架下增量。

```
/mnt/e/program/agent_platform/
├── backend/                        # FastAPI 后端
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI 入口、lifespan、CORS
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py           # Pydantic Settings
│   │   │   ├── security.py         # JWT 签发/校验、密码哈希
│   │   │   ├── lifespan.py         # httpx AsyncClient 单例
│   │   │   └── middleware.py       # CSRF 中间件
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── base.py             # SQLAlchemy declarative base
│   │   │   ├── session.py          # async engine、sessionmaker
│   │   │   └── deps.py             # get_db 依赖
│   │   ├── models/                 # SQLAlchemy ORM 模型
│   │   │   ├── __init__.py
│   │   │   ├── user.py
│   │   │   ├── department.py
│   │   │   ├── role.py
│   │   │   ├── app.py              # Agent（Dify 应用镜像）
│   │   │   ├── app_authorization.py
│   │   │   ├── conversation.py
│   │   │   ├── message.py
│   │   │   ├── refresh_token.py
│   │   │   ├── dify_api_key.py
│   │   │   └── audit_log.py
│   │   ├── schemas/                # Pydantic DTO
│   │   ├── auth/
│   │   ├── users/
│   │   ├── depts/
│   │   ├── roles/
│   │   ├── apps/
│   │   ├── chat/
│   │   ├── conversations/
│   │   ├── dify/                   # Dify 适配层
│   │   └── admin/
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_auth.py
│   │   ├── test_apps.py
│   │   ├── test_chat_sse.py
│   │   └── ...
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── .env.example
│   ├── Dockerfile
│   └── README.md
│
├── frontend/                       # React 前端
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/                    # axios 封装、SSE 消费工具
│   │   ├── stores/                 # Zustand stores
│   │   ├── pages/
│   │   │   ├── Login.tsx
│   │   │   ├── Chat.tsx
│   │   │   ├── History.tsx
│   │   │   └── Admin/
│   │   ├── components/
│   │   └── types/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   ├── Dockerfile
│   └── README.md
│
├── deploy/
│   ├── docker-compose.yml
│   ├── nginx/
│   │   └── conf.d/portal.conf
│   └── .env.example
│
└── docs/
    └── superpowers/
        ├── specs/
        │   └── 2026-08-28-agent-platform-design.md
        └── plans/
            └── 2026-08-28-mvp-phase-1-infra-auth.md (本文件)
```

---

## 阶段划分

| 阶段 | 内容 | 交付物（可独立测试） | 估时 |
|---|---|---|---|
| 0 | 仓库骨架与基础设施 | 仓库结构 + Docker Compose 起 Dify + Postgres | 1 周 |
| 1 | 后端鉴权与权限模型 | 用户注册/登录/JWT/CSRF 可用，E2E 通过 | 1.5 周 |
| 2 | Dify 适配层 + Agent 同步 + 授权 | 可列出已授权 Agent，权限校验生效 | 1 周 |
| 3 | 对话代理 + SSE + 对话镜像 | API 调通可流式对话，本地落库 | 2 周 |
| 4 | 文件上传（带安全约束） | 文件上传全链路可用 | 0.5 周 |
| 5 | 前端骨架 + 登录 + 对话 UI | 用户端 MVP 可用 | 2 周 |
| 6 | 管理员后台 | 管理员可管用户/部门/Agent 授权 | 1 周 |
| 7 | 生产部署 + 可观测性 | 生产环境可用，监控告警就位 | 1 周 |
| **合计** | | | **10 周** |

> **本文档详细描述阶段 0、1、2、3、4。** 阶段 5–7 建议作为后续独立 plan 文件实施。

---

# 阶段 0：仓库骨架与基础设施（1 周）

目标：搭建可运行的仓库骨架，部署 Dify 社区版 + Postgres，能用 curl 调通 Dify 管理 API。

## Task 0.1: 初始化仓库骨架

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.env.example`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `frontend/package.json`
- Create: `frontend/index.html`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `deploy/docker-compose.yml`
- Create: `deploy/nginx/conf.d/portal.conf`
- Create: `.gitignore`
- Create: `README.md`

- [ ] **Step 1: 初始化 git 仓库**

```bash
cd /mnt/e/program/agent_platform
git init
git config user.email "agent-platform@company.internal"
git config user.name "Agent Platform Team"
```

- [ ] **Step 2: 创建 `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
.venv/
venv/
*.egg-info/
.pytest_cache/
.coverage
htmlcov/

# Node
node_modules/
dist/
.vite/

# Env
.env
.env.local

# IDE
.vscode/
.idea/
*.swp
.DS_Store

# Logs
*.log

# Uploaded files (dev)
backend/uploads/
backend/storage/
```

- [ ] **Step 3: 创建 `backend/pyproject.toml`**

```toml
[project]
name = "agent-platform-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "sqlalchemy[asyncio]>=2.0.27",
    "asyncpg>=0.29.0",
    "alembic>=1.13.0",
    "pydantic>=2.6.0",
    "pydantic-settings>=2.2.0",
    "python-jose[cryptography]>=3.3.0",
    "passlib[bcrypt]>=1.7.4",
    "bcrypt==4.0.1",  # passlib 兼容版本
    "httpx>=0.27.0",
    "python-multipart>=0.0.9",
    "cryptography>=42.0.0",
    "sse-starlette>=2.0.0",
    "loguru>=0.7.0",
    "email-validator>=2.1.0",
    "werkzeug>=3.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=4.1.0",
    "httpx>=0.27.0",  # TestClient
    "ruff>=0.3.0",
]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 4: 创建 `backend/.env.example`**

```bash
# 必须：JWT 签名密钥（32+ 字节随机字符串）
# 生成：python -c "import secrets; print(secrets.token_urlsafe(32))"
JWT_SECRET=change-me-in-production-32-bytes-minimum-random-string
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# 必须：Dify API Key 加密密钥（Fernet 格式）
# 生成：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=change-me-fernet-base64-key==

# 必须：Postgres 连接
DATABASE_URL=postgresql+asyncpg://agent:agent@localhost:5432/agent_platform

# 必须：Dify 接入
DIFY_BASE_URL=http://localhost:5001
DIFY_ADMIN_EMAIL=admin@dify.local
DIFY_ADMIN_PASSWORD=change-me-dify-admin-password

# 可选
APP_NAME=Agent Platform
DEBUG=true
LOG_LEVEL=INFO
ALLOWED_ORIGINS=http://localhost:5173
```

- [ ] **Step 5: 创建最小 `backend/app/__init__.py`**

```python
"""Agent Platform 后端。"""
__version__ = "0.1.0"
```

- [ ] **Step 6: 创建最小 `backend/app/main.py`**

```python
"""FastAPI 应用入口。"""
from fastapi import FastAPI

app = FastAPI(title="Agent Platform", version="0.1.0")

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
```

- [ ] **Step 7: 创建 `frontend/package.json`**

```json
{
  "name": "agent-platform-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "test": "vitest"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.22.0",
    "antd": "^5.15.0",
    "axios": "^1.6.7",
    "zustand": "^4.5.0",
    "dayjs": "^1.11.10"
  },
  "devDependencies": {
    "@types/react": "^18.2.0",
    "@types/react-dom": "^18.2.0",
    "@vitejs/plugin-react": "^4.2.0",
    "typescript": "^5.4.0",
    "vite": "^5.1.0",
    "vitest": "^1.4.0",
    "@testing-library/react": "^14.2.0"
  }
}
```

- [ ] **Step 8: 创建 `frontend/vite.config.ts`**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

- [ ] **Step 9: 创建 `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "jsx": "react-jsx",
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true
  },
  "include": ["src"]
}
```

- [ ] **Step 10: 创建 `frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Agent Platform</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 11: 创建 `deploy/docker-compose.yml`**

```yaml
version: '3.8'

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
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agent"]
      interval: 5s
      timeout: 5s
      retries: 10

  # Dify 社区版（独立子目录部署，本仓库仅声明引用）
  # 实际部署需克隆 https://github.com/langgenius/dify 仓库的 docker 目录
  # 这里仅作占位说明：
  # cd /opt/dify && docker compose -f docker-compose.yaml up -d
  # 然后 Dify 监听 http://localhost:5001

volumes:
  postgres_data:
```

- [ ] **Step 11.5: 创建 `deploy/Makefile`**（封装 compose 操作）

```makefile
# Agent Platform 部署 Makefile
# 用法：make up / down / logs / ps / pull / restart

PROJECT_ROOT := $(shell pwd)
DIFY_DIR := $(DIFY_DIR)

# 颜色输出
GREEN := \033[0;32m
YELLOW := \033[1;33m
NC := \033[0m

.PHONY: help up down restart logs ps pull pull-dify status clean reset

help: ## 显示帮助
	@echo "$(GREEN)Agent Platform 部署命令$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$' $(MAKEFILE_LIST) | sort | awk | 'BEGIN' {FSFS = ":.*?## "}; {printf "  $(YELLOW)%-15s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "环境变量："
	@echo "  DIFY_DIR    Dify compose 目录（默认：../deploy/dify）"

up: ## 启动本项目所有服务
	@echo "$(GREEN)启动 Postgres + Backend + Nginx...$(NC)"
	docker compose up -d
	@echo "$(GREEN)启动 Dify（独立目录）...$(NC)"
	cd $(DIFY_DIR) && docker compose -f docker-compose.yaml up -d
	@echo "$(GREEN)✅ 全部启动完成$(NC)"
	@echo "Portal:        http://localhost:5173"
	@echo "Dify API:      http://localhost:5001  (本项目直接调用，不暴露 Web UI)"

down: ## 停止所有服务（保留数据卷）
	@echo "$(YELLOW)停止 Dify...$(NC)"
	cd $(DIFY_DIR) && docker compose -f docker-compose.yaml down
	@echo "$(YELLOW)停止本项目...$(NC)"
	docker compose down
	@echo "$(GREEN)✅ 全部停止$(NC)"

restart: ## 重启所有服务
	make down && make up

logs: ## 查看所有服务日志（最近 100 行 + 跟踪）
	docker compose logs -f --tail=100
	cd $(DIFY_DIR) && docker compose -f docker-compose.yaml logs -f --tail=100

ps: ## 列出所有运行中的容器
	docker compose ps
	cd $(DIFY_DIR) && docker compose -f docker-compose.yaml ps

status: ps

pull: ## 拉取本项目所有镜像
	docker compose pull

pull-dify: ## 拉取 Dify 所有镜像
	cd $(DIFY_DIR) && docker compose -f docker-compose.yaml pull

clean: ## 清理停止的容器 + 未用镜像（不删数据卷）
	docker system prune -f

reset: ## ⚠️ 危险：删除所有数据卷并重置（不可逆）
	@echo "$(YELLOW)⚠️  这将删除所有数据库数据，确认请输入 YES:$(NC)"
	@read -p "" confirm && [ "$$confirm" = "YES" ] || (echo "取消" && exit 1)
	docker compose down -v
	cd $(DIFY_DIR) && docker compose -f docker-compose.yaml down -v
	@echo "$(GREEN)✅ 重置完成$(NC)"

DIFY_DIR ?= $(PROJECT_ROOT)/../dify
```
```

- [ ] **Step 12: 创建 `deploy/nginx/conf.d/portal.conf`**

```nginx
upstream backend {
    server backend:8000;
}

server {
    listen 80;
    server_name _;

    # SSE 必需：禁用缓冲
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;

    location /api/ {
        proxy_pass http://backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 特殊 header
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
    }

    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 13: 创建根 `README.md`**

```markdown
# Agent Platform

企业内部 Agent 智能体平台。

- 设计文档：`docs/superpowers/specs/2026-08-28-agent-platform-design.md`
- 实施计划：`docs/superpowers/plans/2026-08-28-mvp-phase-1-infra-auth.md`

## 快速开始

```bash
# 1. 启动 Postgres
cd deploy && docker compose up -d postgres

# 2. 启动 Dify（独立部署，按官方文档）
cd /opt/dify && docker compose -f docker-compose.yaml up -d

# 3. 启动后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # 编辑密钥
uvicorn app.main:app --reload --port 8000

# 4. 启动前端
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173
```

- [ ] **Step 14: 提交骨架**

```bash
git add .
git commit -m "chore: initial repo scaffold (backend + frontend + deploy)"
```

## Task 0.2: 部署 Dify 社区版 + 验证管理 API

**Files:**
- 无代码变更（运维操作）

- [ ] **Step 1: 克隆 Dify 仓库**

```bash
cd /opt
git clone https://github.com/langgenius/dify.git
cd dify
git checkout 1.1.0  # 锁定版本
cd docker
cp .env.example .env
```

- [ ] **Step 2: 修改 Dify 配置（外部化 Postgres——可选但推荐）**

编辑 `docker/.env`：

```bash
# 把内置 Postgres 改成外部（指向本项目 Postgres）
DB_USERNAME=agent
DB_PASSWORD=agent_dev_only
DB_HOST=postgres  # docker network 中 postgres 服务名
DB_PORT=5432
DB_DATABASE=dify
```

- [ ] **Step 3: 创建 dify 数据库**

```bash
docker exec -it ap-postgres psql -U agent -c "CREATE DATABASE dify;"
```

- [ ] **Step 4: 启动 Dify**

```bash
docker compose -f docker-compose.yaml up -d
# 等待 1-2 分钟
docker compose -f docker-compose.yaml ps  # 确认 api worker web 都 healthy
```

- [ ] **Step 5: 用 API 初始化 Dify 管理员**（不走 Web UI）

```bash
curl -X POST http://localhost:5001/v1/setup/init-admin \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@dify.local","password":"DifyAdmin123!","name":"Dify Admin"}'
```

密码 ≥ 9 字符，含大小写字母+数字，保存到 1Password/密码管理器。

- [ ] **Step 6: 验证 API 可达**

```bash
curl http://localhost/v1/setup/initialize-status  # 返回 {"finished": true}
```

- [ ] **Step 7: 在 Dify 中创建一个测试 Agent**

1. 浏览器进入 Dify → "工作室" → "创建空白应用" → "聊天助手"
2. 名称：`test-agent`
3. 在编排页随便填点 prompt（不接 LLM 也能跑通 API 测试）
4. 发布 → "API 访问" → 复制 API Key

- [ ] **Step 8: 验证应用 API 可调通**

```bash
curl -X POST http://localhost/v1/chat-messages \
  -H "Authorization: Bearer app-xxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": {},
    "query": "hello",
    "response_mode": "streaming",
    "conversation_id": "",
    "user": "test-user"
  }' --no-buffer
```

预期：看到 SSE 流式事件输出（`event: message`、`event: message_end` 等）。

- [ ] **Step 9: 提交验证记录**

```bash
cd /mnt/e/program/agent_platform
cat >> deploy/DIFY_DEPLOY.md <<'EOF'
# Dify 部署验证记录

- 版本：1.1.0
- 部署时间：YYYY-MM-DD
- 管理后台：http://dify.internal.com（生产域名替换）
- 测试 Agent：test-agent (dify_app_id: ...)
- API Key 已存入 1Password：条目名 `Dify-test-agent`
EOF

git add deploy/DIFY_DEPLOY.md
git commit -m "docs: record dify deployment verification"
```

---

# 阶段 1：后端鉴权与权限模型（1.5 周）

目标：用户可注册/登录，颁发 JWT，CSRF 防护到位，权限模型数据表就位，管理员后台用户管理可用。

## Task 1.1: 配置 + 数据库连接 + Alembic 迁移

**Files:**
- Create: `backend/app/core/config.py`
- Create: `backend/app/db/base.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/db/deps.py`
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/user.py`
- Create: `backend/app/models/department.py`
- Create: `backend/app/models/role.py`
- Create: `backend/app/models/refresh_token.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_db.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_db.py`：

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

async def test_db_session_works(session: AsyncSession):
    result = await session.execute("SELECT 1")
    assert result.scalar() == 1
```

`backend/tests/conftest.py`（仅 session fixture，client fixture 在 Task 1.3 引入）：

```python
import asyncio
import os

# 测试环境变量必须在任何 app.* 导入前设置
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://agent:agent_dev_only@localhost:5432/agent_platform_test")
os.environ.setdefault("JWT_SECRET", "test-secret-32-bytes-minimum-padding-padding")
os.environ.setdefault("ENCRYPTION_KEY", "ZGV2LWtleS1tdXN0LWJlLTMyLWJ5dGVzLWJhc2U2NC0=")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:8000")
os.environ.setdefault("DIFY_BASE_URL", "http://localhost:5001")

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import get_settings
get_settings.cache_clear()

from app.db.base import Base


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def engine():
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine):
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    # 测试间隔离：每个测试清表
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    async with SessionLocal() as s:
        yield s
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd backend
pip install -e ".[dev]"
pytest tests/test_db.py -v
```

预期：FAIL —— `app.core.config` / `app.db.base` 等模块不存在（ImportError）。

- [ ] **Step 3: 实现配置模块**

`backend/app/core/config.py`：

```python
"""应用配置（从环境变量加载）。"""
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "Agent Platform"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    ALLOWED_ORIGINS: str = ""

    # JWT
    JWT_SECRET: str = Field(..., min_length=32)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # 加密
    ENCRYPTION_KEY: str  # Fernet base64 key

    # DB
    DATABASE_URL: str

    # Dify
    DIFY_BASE_URL: str = "http://localhost:5001"
    DIFY_ADMIN_EMAIL: str = ""
    DIFY_ADMIN_PASSWORD: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
```

`backend/app/core/__init__.py`：空文件。

- [ ] **Step 4: 实现 DB 模块**

`backend/app/db/base.py`：

```python
"""SQLAlchemy declarative base。"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

`backend/app/db/session.py`：

```python
"""异步 DB engine 和 session 工厂。"""
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
```

`backend/app/db/deps.py`：

```python
"""FastAPI 依赖注入。"""
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
```

`backend/app/db/__init__.py`：空文件。

- [ ] **Step 5: 实现最小 User 模型**

`backend/app/models/__init__.py`：

```python
"""所有 ORM 模型的导入聚合（供 Alembic 自动发现）。

注意：app.models.role 里的 user_roles_table 会被 Alembic 自动检测到，
但 User.roles relationship 的 secondary 引用在 Task 1.4 才启用；
为避免循环依赖，本阶段仅 User 引用"user_roles" 字符串，
实际 Table 对象在 Task 1.4 创建（保持字符串引用，SQLAlchemy 会延迟解析）。
"""
from app.models.user import User
from app.models.department import Department
from app.models.role import Role
from app.models.refresh_token import RefreshToken
# 后续 Task 加 app / app_authorization / conversation / message / dify_api_key / audit_log

__all__ = ["User", "Department", "Role", "RefreshToken"]
```

`backend/app/models/user.py`：

```python
"""User 模型（MVP 最小集，后续 Task 扩展）。"""
from datetime import datetime
from sqlalchemy import BigInteger, String, SmallInteger, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    dept_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    department: Mapped["Department | None"] = relationship("Department", lazy="joined", back_populates="users")
    roles: Mapped[list["Role"]] = relationship("Role", secondary="user_roles", back_populates="users", lazy="selectin")
```

`backend/app/models/department.py`、`role.py`、`refresh_token.py`：本 Task 留占位（仅 class 定义 + `__tablename__` + 一个 id 主键），后续 Task 1.4 完善。

`backend/app/models/refresh_token.py`（**Task 1.4 Step 3 完成本实现**）：

```python
"""RefreshToken 模型：存储 hashed refresh token，支持主动撤销（spec 4.1）。"""
from datetime import datetime
from sqlalchemy import BigInteger, String, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 6: 初始化 Alembic**

```bash
cd backend
alembic init alembic
```

`backend/alembic.ini` 修改：

```ini
# 注释掉 sqlalchemy.url（我们用 env.py 注入）
sqlalchemy.url = 
```

`backend/alembic/env.py` 修改：

```python
import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.core.config import settings
from app.db.base import Base
from app.models import *  # noqa: 触发所有模型注册

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Step 7: 生成首个迁移**

```bash
alembic revision --autogenerate -m "init users departments roles refresh_tokens"
alembic upgrade head
```

- [ ] **Step 8: 验证数据库表已创建**

```bash
docker exec -it ap-postgres psql -U agent -d agent_platform -c "\dt"
```

预期：看到 `users`、`departments`、`roles`、`user_roles`、`refresh_tokens`、`alembic_version`。

- [ ] **Step 9: 重新运行测试，应通过**

```bash
pytest tests/test_db.py -v
```

预期：PASS。

- [ ] **Step 10: 提交**

```bash
git add backend/
git commit -m "feat(backend): db engine + alembic + base models"
```

## Task 1.2: JWT 签发与校验

**Files:**
- Create: `backend/app/core/security.py`
- Create: `backend/tests/test_security.py`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_security.py`：

```python
import pytest
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    create_refresh_token,
    hash_refresh_token,
)


def test_hash_and_verify_password():
    h = hash_password("hello123")
    assert h != "hello123"
    assert verify_password("hello123", h) is True
    assert verify_password("wrong", h) is False


def test_jwt_roundtrip():
    token = create_access_token(user_id=42, roles=["USER"], dept_id=7)
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "42"
    assert payload["roles"] == ["USER"]
    assert payload["dept_id"] == 7
    assert "jti" in payload
    assert "exp" in payload


def test_refresh_token_hash_is_deterministic_per_token():
    raw = "abcdef123456"
    h1 = hash_refresh_token(raw)
    h2 = hash_refresh_token(raw)
    assert h1 == h2
    assert h1 != raw
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_security.py -v
```

预期：FAIL —— `app.core.security` 不存在。

- [ ] **Step 3: 实现 security 模块**

`backend/app/core/security.py`：

```python
"""密码哈希、JWT 签发/校验、refresh token 哈希。"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from jose import jwt, JWTError
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(*, user_id: int, roles: list[str], dept_id: int | None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "roles": roles,
        "dept_id": dept_id,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


def create_refresh_token() -> tuple[str, str]:
    """返回 (raw_token, hashed_token)。"""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
```

- [ ] **Step 4: 运行测试，应通过**

```bash
pytest tests/test_security.py -v
```

预期：3 passed。

- [ ] **Step 5: 提交**

```bash
git add backend/app/core/security.py backend/tests/test_security.py
git commit -m "feat(backend): password hash + JWT + refresh token"
```

## Task 1.3: 认证 API（登录 / 注销 / 刷新）+ CSRF 中间件

**Files:**
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/app/schemas/auth.py`
- Create: `backend/app/auth/__init__.py`
- Create: `backend/app/auth/router.py`
- Create: `backend/app/auth/service.py`
- Create: `backend/app/auth/deps.py`
- Create: `backend/app/core/middleware.py`
- Modify: `backend/app/models/user.py`
- Create: `backend/tests/test_auth.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_auth.py`：

```python
import pytest
from httpx import AsyncClient


@pytest.fixture
async def created_user(session):
    from app.core.security import hash_password
    from app.models.user import User
    u = User(email="alice@example.com", name="Alice", password_hash=hash_password("secret123"))
    session.add(u)
    await session.commit()
    return u


async def test_login_success(client: AsyncClient, created_user):
    resp = await client.post("/api/auth/login", json={"email": "alice@example.com", "password": "secret123"})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" not in resp.text  # access 走 cookie，不走 body
    assert resp.cookies.get("access_token_cookie")
    assert resp.cookies.get("refresh_token_cookie")
    assert resp.cookies.get("access_token_cookie").get("httponly") is True
    assert resp.cookies.get("access_token_cookie").get("samesite") == "Strict"


async def test_login_wrong_password(client: AsyncClient, created_user):
    resp = await client.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrong"})
    assert resp.status_code == 401


async def test_me_requires_auth(client: AsyncClient):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_csrf_blocks_unauthorized_origin(client: AsyncClient, created_user):
    await client.post("/api/auth/login", json={"email": "alice@example.com", "password": "secret123"})
    # 模拟跨站 POST
    resp = await client.post(
        "/api/auth/logout",
        headers={"Origin": "https://evil.com"},
    )
    assert resp.status_code == 403


async def test_access_token_expired_returns_401(client: AsyncClient):
    """过期 JWT 应被拒绝。"""
    from app.core.security import create_access_token
    from datetime import datetime, timedelta, timezone
    expired = create_access_token.__wrapped__ if hasattr(create_access_token, "__wrapped__") else create_access_token
    # 直接构造一个已过期的 token
    import jose.jwt as jwt
    from app.core.config import settings
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {
        "sub": "1", "roles": ["USER"], "dept_id": None, "jti": "expired",
        "iat": int((past - timedelta(minutes=15)).timestamp()),
        "exp": int(past.timestamp()),
    }
    expired_token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    client.cookies.set("access_token_cookie", expired_token)
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_refresh_token_rotation_invalidates_old(client: AsyncClient, created_user):
    """refresh 轮转：旧 token 二次使用应失败。"""
    login = await client.post("/api/auth/login", json={"email": "alice@example.com", "password": "secret123"})
    assert login.status_code == 200
    refresh_raw = client.cookies.get("refresh_token_cookie")
    assert refresh_raw

    # 第一次刷新成功
    resp1 = await client.post("/api/auth/refresh")
    assert resp1.status_code == 200

    # 第二次用同一个旧 refresh 应失败（已轮转撤销）
    client.cookies.set("refresh_token_cookie", refresh_raw)
    resp2 = await client.post("/api/auth/refresh")
    assert resp2.status_code == 401
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_auth.py -v
```

预期：FAIL —— auth router 不存在。

- [ ] **Step 3: 实现 schemas**

`backend/app/schemas/__init__.py`：空。

`backend/app/schemas/auth.py`：

```python
"""认证相关 DTO。"""
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class MeResponse(BaseModel):
    id: int
    email: str
    name: str
    roles: list[str]
    dept_id: int | None
```

- [ ] **Step 4: 实现 service**

`backend/app/auth/__init__.py`：空。

`backend/app/auth/service.py`：

```python
"""认证业务逻辑。"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or user.status != 1:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def issue_tokens(session: AsyncSession, user: User) -> tuple[str, str]:
    """返回 (access_token, refresh_raw_token)。"""
    roles = [r.code for r in user.roles] if user.roles else ["USER"]
    access = create_access_token(user_id=user.id, roles=roles, dept_id=user.dept_id)
    refresh_raw, refresh_hashed = create_refresh_token()
    rt = RefreshToken(
        user_id=user.id,
        token_hash=refresh_hashed,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    session.add(rt)
    await session.flush()
    return access, refresh_raw


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> bool:
    from app.core.security import hash_refresh_token
    token_hash = hash_refresh_token(raw_token)
    result = await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    rt = result.scalar_one_or_none()
    if rt is None or rt.revoked_at is not None:
        return False
    rt.revoked_at = datetime.now(timezone.utc)
    await session.flush()
    return True


async def rotate_refresh_token(session: AsyncSession, raw_token: str) -> tuple[User, str, str] | None:
    """校验 + 轮转 refresh token。返回 (user, new_access, new_refresh_raw)。"""
    from app.core.security import hash_refresh_token
    token_hash = hash_refresh_token(raw_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt = result.scalar_one_or_none()
    if rt is None or rt.revoked_at is not None or rt.expires_at < datetime.now(timezone.utc):
        return None
    user = await session.get(User, rt.user_id)
    if user is None or user.status != 1:
        return None
    # 轮转：旧的标记撤销，签发新的
    rt.revoked_at = datetime.now(timezone.utc)
    access, new_refresh_raw = await issue_tokens(session, user)
    return user, access, new_refresh_raw
```

- [ ] **Step 5: 实现 deps（current_user 依赖）**

`backend/app/auth/deps.py`：

```python
"""FastAPI 依赖：当前用户。"""
from typing import Annotated
from fastapi import Depends, Request, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.deps import get_db
from app.models.user import User


async def current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    token = request.cookies.get("access_token_cookie")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    user = await db.get(User, int(payload["sub"]))
    if user is None or user.status != 1:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user
```

- [ ] **Step 6: 实现 router**

`backend/app/auth/router.py`：

```python
"""认证路由：登录、注销、刷新、当前用户。"""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.auth.service import authenticate, issue_tokens, revoke_refresh_token, rotate_refresh_token
from app.core.config import settings
from app.db.deps import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, MeResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_KWARGS_BASE = {
    "httponly": True,
    "samesite": "Strict",
    "path": "/",
}


def _cookie_kwargs() -> dict:
    """根据 DEBUG 模式返回 cookie 配置。生产 secure=True；本地 HTTP 开发 secure=False。"""
    from app.core.config import settings
    return {**COOKIE_KWARGS_BASE, "secure": not settings.DEBUG}


def _set_auth_cookies(response: Response, access: str, refresh_raw: str) -> None:
    kwargs = _cookie_kwargs()
    response.set_cookie("access_token_cookie", access, max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, **kwargs)
    response.set_cookie("refresh_token_cookie", refresh_raw, max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400, **kwargs)


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie("access_token_cookie", path="/")
    response.delete_cookie("refresh_token_cookie", path="/")


@router.post("/login")
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user = await authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    access, refresh_raw = await issue_tokens(db, user)
    resp = Response(status_code=200)
    _set_auth_cookies(resp, access, refresh_raw)
    return resp


@router.post("/logout")
async def logout(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    refresh_raw = request.cookies.get("refresh_token_cookie")
    if refresh_raw:
        await revoke_refresh_token(db, refresh_raw)
    resp = Response(status_code=200)
    _clear_auth_cookies(resp)
    return resp


@router.post("/refresh")
async def refresh(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    refresh_raw = request.cookies.get("refresh_token_cookie")
    if not refresh_raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")
    result = await rotate_refresh_token(db, refresh_raw)
    if result is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    _, new_access, new_refresh_raw = result
    resp = Response(status_code=200)
    _set_auth_cookies(resp, new_access, new_refresh_raw)
    return resp


@router.get("/me", response_model=MeResponse)
async def me(user: Annotated[User, Depends(current_user)]):
    return MeResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        roles=[r.code for r in user.roles],
        dept_id=user.dept_id,
    )
```

- [ ] **Step 7: 实现 CSRF 中间件**

`backend/app/core/middleware.py`：

```python
"""CSRF 防护中间件：校验敏感写接口的 Origin/Referer。"""
from urllib.parse import urlparse
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PROTECTED_PATHS = ("/api/chat/send", "/api/chat/files", "/api/auth/", "/api/admin/")


def _extract_origin_from_referer(referer: str | None) -> str:
    if not referer:
        return ""
    try:
        parsed = urlparse(referer)
        # scheme://host:port → 统一格式（不含末尾斜杠）
        host = parsed.hostname or ""
        if not host:
            return ""
        if parsed.port and parsed.port not in (80, 443):
            return f"{parsed.scheme}://{host}:{parsed.port}"
        return f"{parsed.scheme}://{host}"
    except (ValueError, TypeError):
        return ""


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_origins: list[str]):
        super().__init__(app)
        self.allowed = {o.rstrip("/") for o in allowed_origins}

    async def dispatch(self, request: Request, call_next):
        if request.method in WRITE_METHODS and any(request.url.path.startswith(p) for p in PROTECTED_PATHS):
            origin = request.headers.get("origin") or _extract_origin_from_referer(request.headers.get("referer"))
            origin = (origin or "").rstrip("/")
            if not origin or origin not in self.allowed:
                return JSONResponse({"detail": "Forbidden: invalid origin"}, status_code=403)
        return await call_next(request)
```

- [ ] **Step 8: 修改 main.py**

`backend/app/main.py`：

```python
"""FastAPI 应用入口。"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.middleware import CSRFMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动逻辑（阶段 3 加 httpx 客户端单例）
    yield


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

# CORS（开发模式需要；生产由 Nginx 同源代理，无需 CORS）
if settings.DEBUG:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# CSRF（生产环境永远启用）
# 生产：ALLOWED_ORIGINS 仅含生产域名（如 https://portal.internal.company.com）
# 开发：DEBUG=true 时追加 localhost:5173 (Vite) / localhost:8000 (FastAPI) / 127.0.0.1 同上
_csrf_origins = list(settings.allowed_origins_list)
if settings.DEBUG:
    _csrf_origins += [
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8000", "http://127.0.0.1:8000",
    ]
app.add_middleware(CSRFMiddleware, allowed_origins=_csrf_origins)

app.include_router(auth_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
```

- [ ] **Step 9: 修改 conftest.py 加入 test client + 测试 DB setup**

`backend/tests/conftest.py`（完整重写）：

```python
import asyncio
import os

# 强制使用测试 DB
os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL", "postgresql+asyncpg://agent:agent_dev_only@localhost:5432/agent_platform_test")
os.environ.setdefault("JWT_SECRET", "test-secret-32-bytes-minimum-padding-padding")
os.environ.setdefault("ENCRYPTION_KEY", "ZGV2LWtleS1tdXN0LWJlLTMyLWJ5dGVzLWJhc2U2NC0=")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
os.environ.setdefault("DIFY_BASE_URL", "http://localhost:5001")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import get_settings
get_settings.cache_clear()  # 重读 env

from app.db.base import Base
from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def engine():
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine):
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        # 每个测试开始前回滚到干净状态
        async with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())
        yield s


@pytest.fixture
async def client(engine):
    from app.db.deps import get_db
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async def _get_db():
        async with SessionLocal() as s:
            yield s
    app.dependency_overrides[get_db] = _get_db
    # lifespan 不在 ASGITransport 中运行，测试前手动注入 state
    # 默认提供一个简单的 FakeDify；具体测试可在 fixture 内覆盖
    from app.dify.client import DifyClient
    app.state.dify = DifyClient(base_url="http://test-dify", encryption_key=os.environ["ENCRYPTION_KEY"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 10: 创建测试 DB**

```bash
docker exec -it ap-postgres psql -U agent -c "CREATE DATABASE agent_platform_test;"
```

- [ ] **Step 11: 运行所有测试，应通过**

```bash
pytest -v
```

预期：`test_db.py::test_db_session_works`、`test_security.py::test_hash_and_verify_password`、`test_security.py::test_jwt_roundtrip`、`test_security.py::test_refresh_token_hash_is_deterministic_per_token`、`test_auth.py::test_login_success`、`test_auth.py::test_login_wrong_password`、`test_auth.py::test_me_requires_auth`、`test_auth.py::test_csrf_blocks_unauthorized_origin` 全部 PASS。

- [ ] **Step 12: 提交**

```bash
git add backend/
git commit -m "feat(backend): auth API + JWT cookie + CSRF middleware"
```

## Task 1.4: 组织架构 CRUD（部门/角色）

**Files:**
- Modify: `backend/app/models/department.py`
- Modify: `backend/app/models/role.py`
- Create: `backend/app/schemas/dept.py`
- Create: `backend/app/schemas/role.py`
- Create: `backend/app/depts/__init__.py`
- Create: `backend/app/depts/router.py`
- Create: `backend/app/roles/__init__.py`
- Create: `backend/app/roles/router.py`
- Create: `backend/app/admin/__init__.py`
- Create: `backend/app/admin/router.py`
- Create: `backend/tests/test_admin.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_admin.py`：

```python
import pytest
from httpx import AsyncClient


@pytest.fixture
async def platform_admin_token(client: AsyncClient):
    """先创建一个 PLATFORM_ADMIN 用户并登录。"""
    from app.core.security import hash_password
    from app.models.user import User
    from app.models.role import Role
    from app.models.department import Department
    # 用 test session 注入
    from app.db.session import SessionLocal
    from app.core.security import create_access_token

    async with SessionLocal() as s:
        dept = Department(name="总公司", path="/1/")
        s.add(dept)
        await s.flush()
        role = Role(code="PLATFORM_ADMIN", name="平台管理员")
        s.add(role)
        await s.flush()
        u = User(email="admin@x.com", name="Admin", password_hash=hash_password("admin123"), dept_id=dept.id)
        s.add(u)
        await s.flush()
        from app.models.role import user_roles_table
        from sqlalchemy import insert
        await s.execute(insert(user_roles_table).values(user_id=u.id, role_id=role.id))
        await s.commit()

    resp = await client.post("/api/auth/login", json={"email": "admin@x.com", "password": "admin123"})
    assert resp.status_code == 200


async def test_list_depts_requires_platform_admin(client: AsyncClient):
    resp = await client.get("/api/admin/departments")
    assert resp.status_code == 401  # 未登录


async def test_create_and_list_dept(client: AsyncClient, platform_admin_token):
    resp = await client.post("/api/admin/departments", json={"name": "研发中心", "parent_id": 1})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "研发中心"
    assert body["path"].startswith("/1/")

    resp2 = await client.get("/api/admin/departments")
    assert resp2.status_code == 200
    names = [d["name"] for d in resp2.json()]
    assert "研发中心" in names


async def test_create_and_list_role(client: AsyncClient, platform_admin_token):
    resp = await client.post("/api/admin/roles", json={"code": "USER", "name": "普通用户"})
    assert resp.status_code == 201
    resp2 = await client.get("/api/admin/roles")
    codes = [r["code"] for r in resp2.json()]
    assert "USER" in codes
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_admin.py -v
```

预期：FAIL。

- [ ] **Step 3: 完善 Department / Role / user_roles 模型**

`backend/app/models/department.py`：

```python
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import BigInteger, String, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True)
    path: Mapped[str] = mapped_column(String(500), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship("User", back_populates="department")
```

`backend/app/models/role.py`：

```python
from sqlalchemy import BigInteger, String, Table, Column, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.user import User  # noqa


user_roles_table = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    users: Mapped[list["User"]] = relationship("User", secondary=user_roles_table, back_populates="roles", lazy="selectin")
```

`backend/app/models/user.py`（修改顶部 import + 关系）：

在文件顶部 `if TYPE_CHECKING` 块添加 `from app.models.role import Role`，并导入 `user_roles_table`（确保 `User.roles` 关系能正确解析）。

- [ ] **Step 4: schemas**

`backend/app/schemas/dept.py`：

```python
from pydantic import BaseModel, Field


class DeptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: int | None = None


class DeptOut(BaseModel):
    id: int
    name: str
    parent_id: int | None
    path: str

    class Config:
        from_attributes = True
```

`backend/app/schemas/role.py`：

```python
from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Z_]+$")
    name: str = Field(min_length=1, max_length=100)


class RoleOut(BaseModel):
    id: int
    code: str
    name: str

    class Config:
        from_attributes = True
```

- [ ] **Step 5: dept router**

`backend/app/depts/__init__.py`：空。

`backend/app/depts/router.py`：

```python
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.deps import get_db
from app.models.department import Department
from app.models.user import User
from app.schemas.dept import DeptCreate, DeptOut

router = APIRouter(prefix="/api/admin/departments", tags=["admin-depts"])


def require_platform_admin(user: User) -> None:
    if "PLATFORM_ADMIN" not in [r.code for r in user.roles]:
        raise HTTPException(403, "Requires PLATFORM_ADMIN")


@router.post("", response_model=DeptOut, status_code=201)
async def create_dept(
    body: DeptCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    require_platform_admin(user)
    parent_path = "/"
    if body.parent_id:
        parent = await db.get(Department, body.parent_id)
        if not parent:
            raise HTTPException(400, "Parent department not found")
        # parent.path 形如 "/1/3/"，去掉尾部斜杠再拼接
        parent_path = parent.path.rstrip("/") + "/"
    dept = Department(name=body.name, parent_id=body.parent_id, path="")
    db.add(dept)
    await db.flush()
    # 统一格式：根部门 → "/1/"，子部门 → "/1/3/7/"
    dept.path = f"{parent_path}{dept.id}/"
    await db.flush()
    return dept


@router.get("", response_model=list[DeptOut])
async def list_depts(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    require_platform_admin(user)
    result = await db.execute(select(Department).order_by(Department.id))
    return result.scalars().all()
```

- [ ] **Step 6: role router**

`backend/app/roles/__init__.py`：空。

`backend/app/roles/router.py`：

```python
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.deps import get_db
from app.models.role import Role
from app.models.user import User
from app.schemas.role import RoleCreate, RoleOut

router = APIRouter(prefix="/api/admin/roles", tags=["admin-roles"])


@router.post("", response_model=RoleOut, status_code=201)
async def create_role(
    body: RoleCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    if "PLATFORM_ADMIN" not in [r.code for r in user.roles]:
        raise HTTPException(403, "Requires PLATFORM_ADMIN")
    role = Role(code=body.code, name=body.name)
    db.add(role)
    await db.flush()
    return role


@router.get("", response_model=list[RoleOut])
async def list_roles(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    if "PLATFORM_ADMIN" not in [r.code for r in user.roles]:
        raise HTTPException(403, "Requires PLATFORM_ADMIN")
    result = await db.execute(select(Role).order_by(Role.id))
    return result.scalars().all()
```

- [ ] **Step 7: admin 主路由（用户管理占位）**

`backend/app/admin/__init__.py`：空。

`backend/app/admin/router.py`：

```python
"""管理员主路由聚合。"""
from fastapi import APIRouter

from app.depts.router import router as depts_router
from app.roles.router import router as roles_router

router = APIRouter(prefix="/api/admin")
router.include_router(depts_router)
router.include_router(roles_router)
```

- [ ] **Step 8: 修改 main.py**

`backend/app/main.py` 加：

```python
from app.admin.router import router as admin_router
# ...
app.include_router(admin_router)
```

- [ ] **Step 9: 生成迁移 + 应用**

```bash
cd backend
alembic revision --autogenerate -m "add dept path + roles + user_roles"
alembic upgrade head
```

- [ ] **Step 10: 运行所有测试，应通过**

```bash
pytest -v
```

预期：所有测试 PASS。

- [ ] **Step 11: 提交**

```bash
git add backend/
git commit -m "feat(backend): department + role CRUD for platform admin"
```

---

# 阶段 2：Dify 适配层 + Agent 同步 + 授权（1 周）

目标：Dify 应用镜像到本地 DB，按用户/部门/角色授权，权限解析函数单测覆盖。

## Task 2.1: Dify 客户端 + httpx 单例

**Files:**
- Create: `backend/app/dify/__init__.py`
- Create: `backend/app/dify/client.py`
- Create: `backend/app/core/lifespan.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_dify_client.py`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_dify_client.py`：

```python
import pytest
from app.dify.client import DifyClient


async def test_dify_client_has_app_method():
    c = DifyClient(base_url="http://localhost:5001")
    assert hasattr(c, "list_apps")
    assert hasattr(c, "chat_messages_stream")


async def test_dify_client_decrypt_api_key():
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    fernet = Fernet(key)
    encrypted = fernet.encrypt(b"app-raw-key").decode()
    c = DifyClient(base_url="http://x", encryption_key=key.decode())
    assert c._decrypt_key(encrypted) == "app-raw-key"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_dify_client.py -v
```

预期：FAIL。

- [ ] **Step 3: 实现 DifyClient**

`backend/app/dify/client.py`：

```python
"""Dify API 客户端封装。

注意：本类的 httpx.AsyncClient 由 lifespan 在 worker 启动时构造并传入，
整个进程生命周期复用同一客户端（spec 13.1）。DifyClient 本身持有该
客户端，所有方法直接使用 self._client。
"""
from typing import Any
import httpx
from cryptography.fernet import Fernet


class DifyClient:
    def __init__(
        self,
        base_url: str,
        encryption_key: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
        connect_timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self._fernet = Fernet(encryption_key.encode())
        self._timeout = httpx.Timeout(timeout, connect=connect_timeout)
        # 允许外部注入（生产路径：lifespan 注入）；未注入则临时创建（测试用）
        self._client = http_client
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        """lifespan 关闭时调用。"""
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def _decrypt_key(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode()).decode()

    def _auth_headers(self, encrypted_api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._decrypt_key(encrypted_api_key)}"}

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def list_apps(self, encrypted_admin_key: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
        """列出所有应用（管理员视角）。"""
        admin_key = self._fernet.decrypt(encrypted_admin_key.encode()).decode()
        client = await self._ensure_client()
        resp = await client.get(
            f"{self.base_url}/v1/apps",
            params={"page": page, "limit": limit},
            headers={"Authorization": f"Bearer {admin_key}"},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def upload_file(self, encrypted_api_key: str, file_bytes: bytes, filename: str, mime: str) -> str:
        """上传文件，返回 dify_file_id。"""
        client = await self._ensure_client()
        resp = await client.post(
            f"{self.base_url}/v1/files/upload",
            headers=self._auth_headers(encrypted_api_key),
            files={"file": (filename, file_bytes, mime)},
            data={"user": "agent-platform"},
        )
        resp.raise_for_status()
        return resp.json()["id"]

    async def chat_messages_stream(self, encrypted_api_key: str, payload: dict[str, Any]):
        """SSE 流式对话，yield 原始行。"""
        client = await self._ensure_client()
        async with client.stream(
            "POST",
            f"{self.base_url}/v1/chat-messages",
            headers=self._auth_headers(encrypted_api_key),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                yield line
```

- [ ] **Step 4: 实现 lifespan 单例（spec 13.1）**

`backend/app/core/lifespan.py`：

```python
"""应用生命周期管理：httpx 客户端 + DifyClient 单例。"""
import os
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI

from app.core.config import settings
from app.dify.client import DifyClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    """每个 worker 进程启动时初始化客户端单例，关闭时清理。"""
    # worker_id 用作日志字段，便于排障（spec 9.6）
    worker_id = f"{os.getpid()}-{id(app)}"
    app.state.worker_id = worker_id

    # 共享的 httpx AsyncClient（连接池复用）
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    )
    dify = DifyClient(
        base_url=settings.DIFY_BASE_URL,
        encryption_key=settings.ENCRYPTION_KEY,
        http_client=http_client,
    )
    app.state.dify = dify
    app.state.http_client = http_client
    try:
        yield
    finally:
        await http_client.aclose()
```

- [ ] **Step 5: 修改 main.py 用 lifespan**

`backend/app/main.py`：

```python
from app.core.lifespan import lifespan as app_lifespan
# ...
app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=app_lifespan)
```

- [ ] **Step 6: 运行测试，应通过**

```bash
pytest tests/test_dify_client.py -v
```

预期：2 passed。

- [ ] **Step 7: 提交**

```bash
git add backend/
git commit -m "feat(backend): dify client wrapper with encryption"
```

## Task 2.2: Agent 同步（启动时拉取 Dify 应用列表）

**Files:**
- Create: `backend/app/models/app.py`
- Create: `backend/app/models/dify_api_key.py`
- Create: `backend/app/apps/__init__.py`
- Create: `backend/app/apps/sync.py`
- Create: `backend/app/apps/router.py`
- Create: `backend/app/schemas/app.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/tests/test_app_sync.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_app_sync.py`：

```python
import pytest
from sqlalchemy import select
from app.models.app import App
from app.models.dify_api_key import DifyAPIKey
from app.apps.sync import sync_apps_from_dify


async def test_sync_creates_apps_and_keys(session, monkeypatch):
    """Mock Dify API 返回 2 个应用，验证本地写入 + 加密存储 API Key。"""
    fake_apps = [
        {"id": "dify-app-1", "name": "HR Bot", "description": "HR 助手", "mode": "chat"},
        {"id": "dify-app-2", "name": "IT Bot", "description": "IT 助手", "mode": "chat"},
    ]

    class FakeDify:
        async def list_apps(self, encrypted_admin_key, page=1, limit=100):
            return fake_apps

    monkeypatch.setattr("app.apps.sync.get_dify_client", lambda: FakeDify())

    # 预置管理员加密 key（用测试 fernet key 加密 "fake-admin-key"）
    from cryptography.fernet import Fernet
    import os
    key = os.environ["ENCRYPTION_KEY"].encode()
    admin_encrypted = Fernet(key).encrypt(b"fake-admin-key").decode()

    await sync_apps_from_dify(session, admin_encrypted)

    result = await session.execute(select(App))
    apps = result.scalars().all()
    assert len(apps) == 2
    assert {a.dify_app_id for a in apps} == {"dify-app-1", "dify-app-2"}

    result2 = await session.execute(select(DifyAPIKey))
    keys = result2.scalars().all()
    assert len(keys) == 2
    for k in keys:
        assert k.api_key_encrypted.startswith("gAAAAA")  # Fernet 加密前缀


async def test_sync_updates_existing_app(session, monkeypatch):
    fake = [{"id": "dify-app-1", "name": "HR Bot Updated", "description": "new", "mode": "chat"}]

    class FakeDify:
        async def list_apps(self, encrypted_admin_key, page=1, limit=100):
            return fake

    monkeypatch.setattr("app.apps.sync.get_dify_client", lambda: FakeDify())
    from cryptography.fernet import Fernet
    import os
    key = os.environ["ENCRYPTION_KEY"].encode()
    admin_encrypted = Fernet(key).encrypt(b"fake-admin-key").decode()

    await sync_apps_from_dify(session, admin_encrypted)
    await sync_apps_from_dify(session, admin_encrypted)  # 第二次跑

    result = await session.execute(select(App))
    apps = result.scalars().all()
    assert len(apps) == 1
    assert apps[0].name == "HR Bot Updated"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_app_sync.py -v
```

预期：FAIL。

- [ ] **Step 3: 实现 App / DifyAPIKey 模型**

`backend/app/models/app.py`：

```python
from datetime import datetime
from sqlalchemy import BigInteger, String, SmallInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class App(Base):
    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dify_app_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`backend/app/models/dify_api_key.py`：

```python
from datetime import datetime
from sqlalchemy import BigInteger, String, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DifyAPIKey(Base):
    __tablename__ = "dify_api_keys"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    app_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("apps.id", ondelete="CASCADE"), unique=True, nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(String(500), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

修改 `models/__init__.py` 加上 `App`、`DifyAPIKey`。

- [ ] **Step 4: 实现 sync 函数**

`backend/app/apps/__init__.py`：空。

`backend/app/apps/sync.py`：

```python
"""从 Dify 同步应用到本地。"""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.dify.client import DifyClient
from app.models.app import App
from app.models.dify_api_key import DifyAPIKey


def get_dify_client() -> DifyClient:
    """运维脚本入口构造一次性 DifyClient。

    注意：与 request 路径上的 lifespan 单例不同（spec 13.1），
    此处用于一次性同步任务，调用完毕即丢弃。
    """
    from app.core.config import settings
    return DifyClient(base_url=settings.DIFY_BASE_URL, encryption_key=settings.ENCRYPTION_KEY)


async def sync_apps_from_dify(session: AsyncSession, admin_encrypted_key: str | None = None) -> int:
    """全量同步。返回处理的应用数。"""
    from cryptography.fernet import Fernet
    from app.core.config import settings

    if admin_encrypted_key is None:
        raise NotImplementedError("MVP 阶段必须显式传入 admin_encrypted_key（由运维脚本生成）")

    client = get_dify_client()
    dify_apps = await client.list_apps(admin_encrypted_key)

    count = 0
    for app_data in dify_apps:
        # upsert
        stmt = (
            pg_insert(App)
            .values(
                dify_app_id=app_data["id"],
                name=app_data["name"],
                description=app_data.get("description", ""),
                mode=app_data.get("mode", "chat"),
                status=1,
            )
            .on_conflict_do_update(
                index_elements=[App.dify_app_id],
                set_={"name": app_data["name"], "description": app_data.get("description", ""), "mode": app_data.get("mode", "chat")},
            )
        )
        await session.execute(stmt)

        # 创建或更新 API Key（这里假设 dify_apps 里包含 api_key 字段；如无则需调用 /v1/apps/{id}/api-keys）
        api_key = app_data.get("api_key")
        if api_key:
            result = await session.execute(select(App).where(App.dify_app_id == app_data["id"]))
            app_obj = result.scalar_one()
            fernet = Fernet(settings.ENCRYPTION_KEY.encode())
            encrypted = fernet.encrypt(api_key.encode()).decode()
            stmt2 = (
                pg_insert(DifyAPIKey)
                .values(app_id=app_obj.id, api_key_encrypted=encrypted)
                .on_conflict_do_update(
                    index_elements=[DifyAPIKey.app_id],
                    set_={"api_key_encrypted": encrypted},
                )
            )
            await session.execute(stmt2)
        count += 1

    await session.commit()
    return count
```

- [ ] **Step 5: 实现 schemas**

`backend/app/schemas/app.py`：

```python
from pydantic import BaseModel


class AppOut(BaseModel):
    id: int
    dify_app_id: str
    name: str
    description: str | None
    mode: str

    class Config:
        from_attributes = True
```

- [ ] **Step 6: 实现 user 视角的 app list router**

`backend/app/apps/router.py`：

```python
from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.deps import get_db
from app.models.user import User
from app.schemas.app import AppOut

router = APIRouter(prefix="/api/apps", tags=["apps"])


@router.get("/me", response_model=list[AppOut])
async def list_my_apps(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    """返回当前用户可访问的 Agent 列表（占位：MVP 第一版仅返回全部启用的）。"""
    from sqlalchemy import select
    from app.models.app import App
    result = await db.execute(select(App).where(App.status == 1).order_by(App.id))
    return result.scalars().all()
```

- [ ] **Step 7: 修改 main.py**

```python
from app.apps.router import router as apps_router
# ...
app.include_router(apps_router)
```

- [ ] **Step 8: 生成迁移**

```bash
alembic revision --autogenerate -m "add apps and dify_api_keys tables"
alembic upgrade head
```

- [ ] **Step 9: 运行测试，应通过**

```bash
pytest -v
```

预期：所有 PASS。

- [ ] **Step 10: 提交**

```bash
git add backend/
git commit -m "feat(backend): app sync from dify + apps/me endpoint"
```

## Task 2.3: 三态主体授权（user / dept / role）

**Files:**
- Create: `backend/app/models/app_authorization.py`
- Create: `backend/app/schemas/authorization.py`
- Create: `backend/app/apps/authorization.py`
- Create: `backend/tests/test_authorization.py`
- Modify: `backend/app/apps/router.py`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_authorization.py`：

```python
import pytest
from sqlalchemy import select
from app.models.app import App
from app.models.user import User
from app.models.role import Role
from app.models.department import Department
from app.models.app_authorization import AppAuthorization
from app.apps.authorization import user_can_access_app, grant_authorization, revoke_authorization


async def test_user_direct_grant(session):
    app = App(dify_app_id="x1", name="X1", mode="chat")
    user = User(email="a@x.com", name="A", password_hash="x")
    session.add_all([app, user])
    await session.flush()

    await grant_authorization(session, app_id=app.id, principal_type="user", principal_id=user.id)
    await session.commit()

    assert await user_can_access_app(session, user.id, app.id) is True


async def test_user_via_dept(session):
    app = App(dify_app_id="x2", name="X2", mode="chat")
    dept = Department(name="D", path="/")
    user = User(email="b@x.com", name="B", password_hash="x", dept_id=2)
    user.dept_id = None  # 先创建
    session.add_all([app, dept])
    await session.flush()
    user.dept_id = dept.id
    await session.flush()

    await grant_authorization(session, app_id=app.id, principal_type="dept", principal_id=dept.id)
    await session.commit()

    assert await user_can_access_app(session, user.id, app.id) is True


async def test_user_via_role(session):
    app = App(dify_app_id="x3", name="X3", mode="chat")
    role = Role(code="USER", name="User")
    user = User(email="c@x.com", name="C", password_hash="x")
    session.add_all([app, role, user])
    await session.flush()
    from app.models.role import user_roles_table
    from sqlalchemy import insert
    await session.execute(insert(user_roles_table).values(user_id=user.id, role_id=role.id))
    await session.flush()

    await grant_authorization(session, app_id=app.id, principal_type="role", principal_id=role.id)
    await session.commit()

    assert await user_can_access_app(session, user.id, app.id) is True


async def test_user_no_grant_returns_false(session):
    app = App(dify_app_id="x4", name="X4", mode="chat")
    user = User(email="d@x.com", name="D", password_hash="x")
    session.add_all([app, user])
    await session.commit()
    assert await user_can_access_app(session, user.id, app.id) is False


async def test_compound_authorization(session):
    """复合授权：用户同时拥有 dept + role 两条授权路径，解析应返回 True（不重复出错）。"""
    app = App(dify_app_id="x5", name="X5", mode="chat")
    dept = Department(name="D5", path="/")
    role = Role(code="APP_ADMIN", name="AA")
    user = User(email="e@x.com", name="E", password_hash="x")
    session.add_all([app, dept, role, user])
    await session.flush()
    user.dept_id = dept.id
    await session.flush()
    from app.models.role import user_roles_table
    from sqlalchemy import insert
    await session.execute(insert(user_roles_table).values(user_id=user.id, role_id=role.id))
    await session.flush()

    # 同时给 dept 和 role 授权
    await grant_authorization(session, app_id=app.id, principal_type="dept", principal_id=dept.id)
    await grant_authorization(session, app_id=app.id, principal_type="role", principal_id=role.id)
    await session.commit()

    assert await user_can_access_app(session, user.id, app.id) is True
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_authorization.py -v
```

预期：FAIL。

- [ ] **Step 3: 实现 AppAuthorization 模型**

`backend/app/models/app_authorization.py`：

```python
from sqlalchemy import BigInteger, String, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppAuthorization(Base):
    __tablename__ = "app_authorizations"

    app_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("apps.id", ondelete="CASCADE"), primary_key=True)
    principal_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    principal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    __table_args__ = (
        CheckConstraint("principal_type IN ('user', 'dept', 'role')", name="ck_principal_type"),
    )
```

修改 `models/__init__.py` 加入 `AppAuthorization`。

- [ ] **Step 4: 实现 authorization 服务**

`backend/app/apps/authorization.py`：

```python
"""授权管理 + 权限解析。"""
from sqlalchemy import select, exists, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_authorization import AppAuthorization


async def grant_authorization(
    session: AsyncSession, *, app_id: int, principal_type: str, principal_id: int
) -> None:
    assert principal_type in ("user", "dept", "role"), "Invalid principal_type"
    stmt = (
        pg_insert(AppAuthorization)
        .values(app_id=app_id, principal_type=principal_type, principal_id=principal_id)
        .on_conflict_do_nothing()
    )
    await session.execute(stmt)


async def revoke_authorization(
    session: AsyncSession, *, app_id: int, principal_type: str, principal_id: int
) -> bool:
    auth = await session.execute(
        select(AppAuthorization).where(
            and_(
                AppAuthorization.app_id == app_id,
                AppAuthorization.principal_type == principal_type,
                AppAuthorization.principal_id == principal_id,
            )
        )
    )
    obj = auth.scalar_one_or_none()
    if obj is None:
        return False
    await session.delete(obj)
    return True


async def user_can_access_app(session: AsyncSession, user_id: int, app_id: int) -> bool:
    """用户对 App 的访问权限 = 自己授权 ∪ 所属部门授权 ∪ 拥有角色授权。"""
    from app.models.user import User
    from app.models.role import user_roles_table

    user = await session.get(User, user_id)
    if user is None:
        return False

    # 1. 直接 user 授权
    direct = await session.execute(
        select(
            exists().where(
                and_(
                    AppAuthorization.app_id == app_id,
                    AppAuthorization.principal_type == "user",
                    AppAuthorization.principal_id == user_id,
                )
            )
        )
    )
    if direct.scalar():
        return True

    # 2. dept 授权
    if user.dept_id is not None:
        via_dept = await session.execute(
            select(
                exists().where(
                    and_(
                        AppAuthorization.app_id == app_id,
                        AppAuthorization.principal_type == "dept",
                        AppAuthorization.principal_id == user.dept_id,
                    )
                )
            )
        )
        if via_dept.scalar():
            return True

    # 3. role 授权
    role_ids_subq = (
        select(user_roles_table.c.role_id)
        .where(user_roles_table.c.user_id == user_id)
        .scalar_subquery()
    )
    via_role = await session.execute(
        select(
            exists().where(
                and_(
                    AppAuthorization.app_id == app_id,
                    AppAuthorization.principal_type == "role",
                    AppAuthorization.principal_id.in_(role_ids_subq),
                )
            )
        )
    )
    return bool(via_role.scalar())
```

- [ ] **Step 5: 修改 `/api/apps/me` 用权限解析**

`backend/app/apps/router.py`：

```python
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.deps import get_db
from app.models.app import App
from app.models.user import User
from app.schemas.app import AppOut
from app.apps.authorization import user_can_access_app

router = APIRouter(prefix="/api/apps", tags=["apps"])


@router.get("/me", response_model=list[AppOut])
async def list_my_apps(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    all_apps_result = await db.execute(select(App).where(App.status == 1).order_by(App.id))
    all_apps = all_apps_result.scalars().all()

    visible = []
    for app in all_apps:
        if await user_can_access_app(db, user.id, app.id):
            visible.append(app)
    return visible


@router.get("/{app_id}", response_model=AppOut)
async def get_app(
    app_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    if not await user_can_access_app(db, user.id, app_id):
        raise HTTPException(403, "No access to this app")
    app = await db.get(App, app_id)
    if app is None:
        raise HTTPException(404, "App not found")
    return app
```

- [ ] **Step 6: 生成迁移**

```bash
alembic revision --autogenerate -m "add app_authorizations table"
alembic upgrade head
```

- [ ] **Step 7: 运行所有测试**

```bash
pytest -v
```

预期：所有 PASS。

- [ ] **Step 8: 提交**

```bash
git add backend/
git commit -m "feat(backend): three-principal authorization model"
```

---

# 阶段 3：对话代理 + SSE + 对话镜像（2 周）

目标：实现 `/api/chat/send`，流式调通 Dify，对话/消息落库，断流兜底。

## Task 3.1: Conversation / Message 模型

**Files:**
- Create: `backend/app/models/conversation.py`
- Create: `backend/app/models/message.py`
- Create: `backend/app/models/audit_log.py`
- Modify: `backend/app/models/__init__.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_models.py`：

```python
import pytest
from sqlalchemy import select
from app.models.conversation import Conversation
from app.models.message import Message


async def test_create_conversation(session):
    conv = Conversation(user_id=1, app_id=1)
    session.add(conv)
    await session.flush()
    assert conv.id is not None
    assert conv.created_at is not None


async def test_message_dedupe_key_unique(session):
    from app.models.conversation import Conversation
    conv = Conversation(user_id=1, app_id=1)
    session.add(conv)
    await session.flush()

    m1 = Message(conversation_id=conv.id, role="assistant", content="x", dify_message_id="dify-msg-1")
    session.add(m1)
    await session.flush()

    m2 = Message(conversation_id=conv.id, role="assistant", content="y", dify_message_id="dify-msg-1")
    session.add(m2)
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        await session.flush()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_models.py -v
```

预期：FAIL —— `app.models.conversation` / `app.models.message` 不存在（ImportError）。

- [ ] **Step 3: 实现模型**

`backend/app/models/conversation.py`：

```python
import uuid
from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, ForeignKey, JSON, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    app_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True)
    dify_conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`backend/app/models/message.py`：

```python
from datetime import datetime
from sqlalchemy import BigInteger, String, ForeignKey, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    dify_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    files: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`backend/app/models/audit_log.py`：

```python
from datetime import datetime
from sqlalchemy import BigInteger, String, ForeignKey, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB, INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
```

修改 `models/__init__.py` 加入 `Conversation, Message, AuditLog`。

- [ ] **Step 4: 生成迁移 + 应用**

```bash
alembic revision --autogenerate -m "add conversations messages audit_logs"
alembic upgrade head
```

- [ ] **Step 5: 运行测试，应通过**

```bash
pytest tests/test_models.py -v
```

- [ ] **Step 6: 提交**

```bash
git add backend/
git commit -m "feat(backend): conversation + message + audit_log models"
```

## Task 3.2: Chat API + SSE 流式代理

**Files:**
- Create: `backend/app/chat/__init__.py`
- Create: `backend/app/chat/router.py`
- Create: `backend/app/chat/service.py`
- Create: `backend/app/schemas/chat.py`
- Create: `backend/tests/test_chat_sse.py`

- [ ] **Step 1: 写失败的测试**

`backend/tests/test_chat_sse.py`：

```python
import pytest
import json
from httpx import AsyncClient
from app.models.app import App
from app.models.user import User
from app.models.dify_api_key import DifyAPIKey
from app.models.message import Message
from cryptography.fernet import Fernet
import os


@pytest.fixture
async def app_with_key(session):
    from app.core.security import hash_password
    user = User(email="e@x.com", name="E", password_hash=hash_password("p123456"))
    app = App(dify_app_id="dify-1", name="T", mode="chat")
    session.add_all([user, app])
    await session.flush()
    fernet = Fernet(os.environ["ENCRYPTION_KEY"].encode())
    encrypted = fernet.encrypt(b"app-fake-key").decode()
    key = DifyAPIKey(app_id=app.id, api_key_encrypted=encrypted)
    session.add(key)
    await session.commit()
    return user, app


async def test_chat_send_streams_sse(client: AsyncClient, app_with_key):
    user, app = app_with_key

    # 登录
    resp = await client.post("/api/auth/login", json={"email": "e@x.com", "password": "p123456"})
    assert resp.status_code == 200

    # Mock Dify 客户端：产生 3 个 SSE 帧
    class FakeDify:
        async def chat_messages_stream(self, encrypted_key, payload):
            yield 'event: message\ndata: {"event":"message","answer":"Hello"}\n'
            yield 'event: message\ndata: {"event":"message","answer":" world"}\n'
            yield 'event: message_end\ndata: {"event":"message_end","id":"dify-msg-1","metadata":{"usage":{"total_tokens":10}}}\n'

    # 注入 lifespan state（lifespan 在 ASGI 测试 transport 中不运行）
    from app.main import app
    app.state.dify = FakeDify()

    # 预先给 app 授权
    from app.apps.authorization import grant_authorization
    from app.db.session import SessionLocal
    async with SessionLocal() as s:
        await grant_authorization(s, app_id=app.id, principal_type="user", principal_id=user.id)
        await s.commit()

    # 发起对话
    resp = await client.post("/api/chat/send", json={"app_id": app.id, "query": "hi"}, headers={"Accept": "text/event-stream"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    # 收集所有数据
    chunks = []
    async for line in resp.aiter_lines():
        chunks.append(line)

    full = "\n".join(chunks)
    assert "Hello" in full
    assert "world" in full
    assert "message_end" in full
    assert "agent_done" in full  # 我们的兜底事件


async def test_chat_send_requires_authorization(client: AsyncClient, app_with_key):
    user, app = app_with_key
    resp = await client.post("/api/auth/login", json={"email": "e@x.com", "password": "p123456"})
    assert resp.status_code == 200
    # 没有授权 → 403
    resp = await client.post("/api/chat/send", json={"app_id": app.id, "query": "hi"})
    assert resp.status_code == 403


async def test_sse_finally_writes_assistant_message_on_disconnect(client: AsyncClient, app_with_key, session):
    """客户端断流：assistant 消息依然入库（spec 5.2 验收清单）。"""
    user, app = app_with_key
    from app.apps.authorization import grant_authorization
    from app.db.session import SessionLocal
    async with SessionLocal() as s:
        await grant_authorization(s, app_id=app.id, principal_type="user", principal_id=user.id)
        await s.commit()

    await client.post("/api/auth/login", json={"email": "e@x.com", "password": "p123456"})

    class FakeDify:
        async def chat_messages_stream(self, encrypted_key, payload):
            yield 'event: message\ndata: {"event":"message","answer":"par"}\n'
            yield 'event: message\ndata: {"event":"message","answer":"tial"}\n'
            # 模拟流中断（不再 yield message_end）
            raise GeneratorExit("client disconnected")

    from app.main import app
    app.state.dify = FakeDify()

    # 中途断开
    with pytest.raises(Exception):
        async with client.stream(
            "POST",
            "/api/chat/send",
            json={"app_id": app.id, "query": "hi"},
        ) as resp:
            async for line in resp.aiter_lines():
                if "message" in line and "par" in line:
                    break  # 模拟前端读完一段就断开

    # 验证 assistant 消息已写入（finally 块兜底）
    from sqlalchemy import select
    msgs = (await session.execute(select(Message).order_by(Message.id))).scalars().all()
    assistant_msgs = [m for m in msgs if m.role == "assistant"]
    assert len(assistant_msgs) == 1
    assert "partial" in assistant_msgs[0].content


async def test_dify_5xx_yields_error_event_and_audits(client: AsyncClient, app_with_key, session):
    """Dify 5xx 错误：前端收到 event:error，审计日志有记录。"""
    user, app = app_with_key
    from app.apps.authorization import grant_authorization
    from app.db.session import SessionLocal
    async with SessionLocal() as s:
        await grant_authorization(s, app_id=app.id, principal_type="user", principal_id=user.id)
        await s.commit()

    await client.post("/api/auth/login", json={"email": "e@x.com", "password": "p123456"})

    import httpx
    class FakeDify:
        async def chat_messages_stream(self, encrypted_key, payload):
            raise httpx.HTTPStatusError("500", request=None, response=None)
            yield  # 让它成为 async generator

    from app.main import app
    app.state.dify = FakeDify()

    resp = await client.post("/api/chat/send", json={"app_id": app.id, "query": "hi"})
    assert resp.status_code == 200  # SSE 返回 200，错误在事件流里

    chunks = []
    async for line in resp.aiter_lines():
        chunks.append(line)
    full = "\n".join(chunks)
    assert "error" in full
    assert "agent_done" in full  # finally 块仍发

    # 审计日志
    from sqlalchemy import select
    from app.models.audit_log import AuditLog
    logs = (await session.execute(select(AuditLog).where(AuditLog.action == "chat.error"))).scalars().all()
    assert len(logs) >= 1
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_chat_sse.py -v
```

预期：FAIL。

- [ ] **Step 3: 实现 schemas**

`backend/app/schemas/chat.py`：

```python
from pydantic import BaseModel, Field


class ChatSendRequest(BaseModel):
    app_id: int
    query: str = Field(min_length=1, max_length=10000)
    conversation_id: str | None = None  # UUID str
    inputs: dict | None = None
```

- [ ] **Step 4: 实现 chat service**

`backend/app/chat/__init__.py`：空。

`backend/app/chat/service.py`：

```python
"""Chat 业务逻辑：SSE 流式代理 + 对话镜像。"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession

from app.dify.client import DifyClient
from app.models.app import App
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation
from app.models.dify_api_key import DifyAPIKey
from app.models.message import Message
from app.models.user import User

log = logging.getLogger(__name__)


def get_dify_client_from_request(request) -> DifyClient:
    """从 FastAPI request.app.state 拿 lifespan 注入的单例（spec 13.1）。"""
    return request.app.state.dify


async def _save_message(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    dify_message_id: str | None = None,
    files: dict | None = None,
) -> None:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        dify_message_id=dify_message_id,
        dedupe_key=dify_message_id,
        files=files,
    )
    session.add(msg)


async def stream_chat(
    session: AsyncSession,
    request,
    user: User,
    app_id: int,
    query: str,
    conversation_id: str | None,
    inputs: dict | None,
) -> AsyncIterator[str]:
    """SSE 流式生成器。来一行 yield 一行；客户端断流 finally 块保证消息入库。"""
    dify = get_dify_client_from_request(request)

    # 1. 取 App + API Key
    app = await session.get(App, app_id)
    if app is None:
        yield 'event: error\ndata: {"message": "App not found"}\n\n'
        return
    api_key_obj = await session.get(DifyAPIKey, app_id)
    if api_key_obj is None:
        yield 'event: error\ndata: {"message": "App not configured"}\n\n'
        return

    # 2. 解析 conversation
    conv_uuid: uuid.UUID
    dify_conv_id: str | None = None
    if conversation_id:
        try:
            conv_uuid = uuid.UUID(conversation_id)
        except ValueError:
            yield 'event: error\ndata: {"message": "Invalid conversation_id"}\n\n'
            return
        conv = await session.get(Conversation, conv_uuid)
        if conv is None or conv.user_id != user.id or conv.deleted_at is not None:
            yield 'event: error\ndata: {"message": "Conversation not found"}\n\n'
            return
        dify_conv_id = conv.dify_conversation_id
    else:
        conv = Conversation(user_id=user.id, app_id=app_id)
        session.add(conv)
        await session.flush()
        conv_uuid = conv.id

    # 3. 写 user message
    await _save_message(session, conv_uuid, "user", query)
    conv.message_count += 1

    # 4. 准备 Dify 请求
    payload = {
        "inputs": inputs or {},
        "query": query,
        "response_mode": "streaming",
        "conversation_id": dify_conv_id or "",
        "user": f"user-{user.id}",
    }
    if not dify_conv_id:
        # 删除空字段，Dify 会自动创建新会话
        del payload["conversation_id"]

    # 5. 流式调用
    accumulated = ""
    final_dify_message_id: str | None = None
    token_usage: dict | None = None
    try:
        async for line in dify.chat_messages_stream(api_key_obj.api_key_encrypted, payload):
            if not line:
                continue
            # 累加
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                    if data.get("event") == "message":
                        accumulated += data.get("answer", "")
                    elif data.get("event") == "message_end":
                        final_dify_message_id = data.get("id") or data.get("message_id")
                        if "metadata" in data and "usage" in data["metadata"]:
                            token_usage = data["metadata"]["usage"]
                    elif data.get("event") == "message_file":
                        # 暂存到 messages.files
                        pass
                except (json.JSONDecodeError, KeyError):
                    pass
            yield line + "\n"
    except Exception as e:
        log.exception("Chat SSE error: user=%s app=%s", user.id, app_id)
        yield 'event: error\ndata: {"message": "Stream error"}\n\n'
        await _write_audit(session, user.id, "chat.error", "conversation", str(conv_uuid), error=str(e)[:200])
    finally:
        # 6. 落库 assistant 消息（断流也要保证入库）
        try:
            if accumulated or final_dify_message_id:
                await _save_message(
                    session,
                    conv_uuid,
                    "assistant",
                    accumulated,
                    dify_message_id=final_dify_message_id,
                )
                conv.message_count += 1
                if token_usage:
                    conv.token_usage = token_usage
                if final_dify_message_id and not dify_conv_id:
                    conv.dify_conversation_id = final_dify_message_id[:64]
            await session.commit()
        except Exception:
            log.exception("Failed to persist chat session state")
            await session.rollback()
        finally:
            # 兜底：始终发 agent_done 让前端知道流结束
            yield 'event: agent_done\ndata: {}\n\n'


async def _write_audit(
    session: AsyncSession,
    user_id: int,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    **metadata,
) -> None:
    entry = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata or None,
    )
    session.add(entry)
```

- [ ] **Step 5: 实现 chat router**

`backend/app/chat/router.py`：

```python
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.authorization import user_can_access_app
from app.auth.deps import current_user
from app.chat.service import stream_chat
from app.db.deps import get_db
from app.models.user import User
from app.schemas.chat import ChatSendRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/send")
async def send(
    body: ChatSendRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    if not await user_can_access_app(db, user.id, body.app_id):
        raise HTTPException(403, "No access to this app")
    return StreamingResponse(
        stream_chat(
            db,
            request,
            user,
            body.app_id,
            body.query,
            body.conversation_id,
            body.inputs,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

- [ ] **Step 6: 修改 main.py**

```python
from app.chat.router import router as chat_router
# ...
app.include_router(chat_router)
```

- [ ] **Step 7: 运行所有测试**

```bash
pytest -v
```

预期：所有 PASS，包括 SSE 流式测试。

- [ ] **Step 8: 手动验证（可选，真实 Dify）**

```bash
# 启动后端，登录后调用
curl -X POST http://localhost:8000/api/chat/send \
  -H "Content-Type: application/json" \
  -H "Cookie: access_token_cookie=..." \
  -d '{"app_id": 1, "query": "hello"}' \
  --no-buffer
```

预期：看到 SSE 流式输出。

- [ ] **Step 9: 提交**

```bash
git add backend/
git commit -m "feat(backend): chat SSE streaming proxy + conversation mirror"
```

## Task 3.3: 对话历史查询

**Files:**
- Create: `backend/app/conversations/__init__.py`
- Create: `backend/app/conversations/router.py`
- Create: `backend/app/schemas/conversation.py`
- Create: `backend/tests/test_conversations.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_conversations.py`：

```python
import pytest
import uuid
from httpx import AsyncClient
from app.models.user import User
from app.models.conversation import Conversation
from app.models.message import Message
from app.core.security import hash_password


async def test_list_my_conversations(client: AsyncClient, session):
    u = User(email="x@y.com", name="X", password_hash=hash_password("p123456"))
    session.add(u)
    await session.flush()
    c = Conversation(user_id=u.id, app_id=1)
    session.add(c)
    await session.flush()
    m = Message(conversation_id=c.id, role="user", content="hi")
    session.add(m)
    await session.commit()

    resp = await client.post("/api/auth/login", json={"email": "x@y.com", "password": "p123456"})
    assert resp.status_code == 200

    resp = await client.get("/api/conversations/")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(c.id)
    assert body[0]["message_count"] >= 1


async def test_get_conversation_messages(client: AsyncClient, session):
    u = User(email="z@y.com", name="Z", password_hash=hash_password("p123456"))
    session.add(u)
    await session.flush()
    c = Conversation(user_id=u.id, app_id=1)
    session.add(c)
    await session.flush()
    session.add_all([
        Message(conversation_id=c.id, role="user", content="hi"),
        Message(conversation_id=c.id, role="assistant", content="hello"),
    ])
    await session.commit()

    await client.post("/api/auth/login", json={"email": "z@y.com", "password": "p123456"})

    resp = await client.get(f"/api/conversations/{c.id}/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


async def test_cannot_access_others_conversation(client: AsyncClient, session):
    u1 = User(email="u1@y.com", name="U1", password_hash=hash_password("p123456"))
    u2 = User(email="u2@y.com", name="U2", password_hash=hash_password("p123456"))
    session.add_all([u1, u2])
    await session.flush()
    c = Conversation(user_id=u1.id, app_id=1)
    session.add(c)
    await session.commit()

    await client.post("/api/auth/login", json={"email": "u2@y.com", "password": "p123456"})

    resp = await client.get(f"/api/conversations/{c.id}/messages")
    assert resp.status_code == 404  # 不能泄漏存在性
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_conversations.py -v
```

预期：FAIL —— `app.schemas.conversation` / `app.conversations.router` 不存在（ImportError）。

- [ ] **Step 3: 实现 schemas**

`backend/app/schemas/conversation.py`：

```python
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class ConversationOut(BaseModel):
    id: UUID
    app_id: int
    title: str | None
    message_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    files: dict | None
    created_at: datetime

    class Config:
        from_attributes = True
```

- [ ] **Step 4: 实现 router**

`backend/app/conversations/__init__.py`：空。

`backend/app/conversations/router.py`：

```python
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.deps import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.conversation import ConversationOut, MessageOut

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
async def list_my_conversations(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None))
        .order_by(Conversation.updated_at.desc())
    )
    return result.scalars().all()


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def get_messages(
    conversation_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id or conv.deleted_at is not None:
        raise HTTPException(404, "Not found")
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return result.scalars().all()


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(404, "Not found")
    conv.deleted_at = datetime.now()  # 软删
    await db.flush()
```

- [ ] **Step 5: 修改 main.py**

```python
from app.conversations.router import router as conversations_router
# ...
app.include_router(conversations_router)
```

- [ ] **Step 6: 运行所有测试**

```bash
pytest -v
```

预期：所有 PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/
git commit -m "feat(backend): conversation history API"
```

---

# 阶段 4：文件上传（带安全约束）（0.5 周）

目标：员工可上传文件（PDF/DOCX/TXT/MD/PNG/JPG）到 Dify，大小 ≤ 20MB，安全校验到位。

## Task 4.1: 文件上传 API

**Files:**
- Create: `backend/app/chat/files.py`
- Create: `backend/app/schemas/file.py`
- Create: `backend/tests/test_file_upload.py`
- Modify: `backend/app/chat/router.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_file_upload.py`：

```python
import io
import pytest
from httpx import AsyncClient


ALLOWED_MIME = ["application/pdf", "text/plain", "image/png"]
DISALLOWED_MIME = ["application/x-msdownload", "text/html"]


async def test_upload_rejects_oversize(client: AsyncClient, app_with_key):
    user, app = app_with_key
    await client.post("/api/auth/login", json={"email": user.email, "password": "p123456"})
    from app.apps.authorization import grant_authorization
    from app.db.session import SessionLocal
    async with SessionLocal() as s:
        await grant_authorization(s, app_id=app.id, principal_type="user", principal_id=user.id)
        await s.commit()

    # 21MB 文件
    big = io.BytesIO(b"x" * (21 * 1024 * 1024))
    resp = await client.post(
        "/api/chat/files",
        files={"file": ("big.txt", big, "text/plain")},
        data={"app_id": str(app.id)},
    )
    assert resp.status_code == 413


async def test_upload_rejects_disallowed_mime(client: AsyncClient, app_with_key):
    user, app = app_with_key
    await client.post("/api/auth/login", json={"email": user.email, "password": "p123456"})
    from app.apps.authorization import grant_authorization
    from app.db.session import SessionLocal
    async with SessionLocal() as s:
        await grant_authorization(s, app_id=app.id, principal_type="user", principal_id=user.id)
        await s.commit()

    resp = await client.post(
        "/api/chat/files",
        files={"file": ("evil.html", io.BytesIO(b"<script>"), "text/html")},
        data={"app_id": str(app.id)},
    )
    assert resp.status_code == 415


async def test_upload_accepts_pdf(client: AsyncClient, app_with_key):
    user, app = app_with_key
    await client.post("/api/auth/login", json={"email": user.email, "password": "p123456"})
    from app.apps.authorization import grant_authorization
    from app.db.session import SessionLocal
    async with SessionLocal() as s:
        await grant_authorization(s, app_id=app.id, principal_type="user", principal_id=user.id)
        await s.commit()

    class FakeDify:
        async def upload_file(self, encrypted_key, file_bytes, filename, mime):
            return "dify-file-id-123"

    from app.main import app
    app.state.dify = FakeDify()

    resp = await client.post(
        "/api/chat/files",
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
        data={"app_id": str(app.id)},
    )
    assert resp.status_code == 200
    assert resp.json()["file_id"] == "dify-file-id-123"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_file_upload.py -v
```

预期：FAIL —— `app.schemas.file` / `app.chat.files` 不存在（ImportError）。

- [ ] **Step 3: 实现 schemas**

`backend/app/schemas/file.py`：

```python
from pydantic import BaseModel


class FileUploadResponse(BaseModel):
    file_id: str
    name: str
    mime: str
    size: int
```

- [ ] **Step 4: 实现上传 endpoint**

`backend/app/chat/files.py`：

```python
"""文件上传：安全校验 + 转发 Dify。"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated
from werkzeug.utils import secure_filename
import hashlib
import io

from app.apps.authorization import user_can_access_app
from app.auth.deps import current_user
from app.db.deps import get_db
from app.models.user import User
from app.schemas.file import FileUploadResponse

router = APIRouter(prefix="/api/chat", tags=["chat-files"])

MAX_SIZE = 20 * 1024 * 1024  # 20MB

ALLOWED_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "image/png",
    "image/jpeg",
}


def get_dify_from_request(request: Request):
    """从 lifespan state 拿 DifyClient 单例（spec 13.1）。"""
    return request.app.state.dify


@router.post("/files", response_model=FileUploadResponse)
async def upload_file(
    request: Request,
    app_id: Annotated[int, Form()],
    file: Annotated[UploadFile, File()],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    # 1. 权限
    if not await user_can_access_app(db, user.id, app_id):
        raise HTTPException(403, "No access")

    # 2. Content-Length 快速拒绝
    cl = request.headers.get("content-length")
    if cl and int(cl) > MAX_SIZE:
        raise HTTPException(413, "File too large")

    # 3. MIME 白名单
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(415, f"MIME type not allowed: {file.content_type}")

    # 4. 文件名清洗
    safe_name = secure_filename(file.filename or "upload")

    # 5. 流式读取 + 大小累计
    chunks = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_SIZE:
            raise HTTPException(413, "File too large")
        chunks.append(chunk)
    file_bytes = b"".join(chunks)

    # 6. 取 API Key 并转发 Dify
    key_obj = await db.get(DifyAPIKey, app_id)
    if key_obj is None:
        raise HTTPException(400, "App not configured")
    dify_file_id = await get_dify_from_request(request).upload_file(
        key_obj.api_key_encrypted,
        file_bytes,
        safe_name,
        file.content_type,
    )

    # 7. 审计
    sha = hashlib.sha256(file_bytes).hexdigest()
    from app.models.audit_log import AuditLog
    db.add(AuditLog(
        user_id=user.id, action="file.upload", resource_type="app", resource_id=str(app_id),
        metadata_json={"file_name": safe_name, "file_size": total, "mime": file.content_type, "sha256": sha},
    ))
    await db.commit()

    return FileUploadResponse(file_id=dify_file_id, name=safe_name, mime=file.content_type, size=total)
```

- [ ] **Step 5: 修改 main.py 挂载 chat_files_router**

```python
from app.chat.files import router as chat_files_router
app.include_router(chat_files_router)
```

- [ ] **Step 6: 验证完整文件上传链路**

实测流程：登录 → 上传 PDF → 拿到 dify_file_id → 在 Chat UI 用此 file_id 发起对话。

- [ ] **Step 7: 运行所有测试，应通过**

```bash
pytest -v
```

预期：所有 PASS。

- [ ] **Step 8: 提交**

```bash
git add backend/
git commit -m "feat(backend): file upload with size+mime+filename security"
```

---

# 阶段 5 补充：种子用户脚本（紧随 Task 1.4）

> **新加 Task 1.4.5**：在阶段 1 末尾增加运维引导脚本，便于本地/测试/CI 创建 PLATFORM_ADMIN。

## Task 1.5: 种子管理员 + 用户管理 CRUD（spec MVP 必要）

**Files:**
- Create: `backend/app/scripts/__init__.py`
- Create: `backend/app/scripts/seed_admin.py`
- Create: `backend/app/users/__init__.py`
- Create: `backend/app/users/router.py`
- Create: `backend/app/schemas/user.py`
- Create: `backend/tests/test_user_admin.py`
- Create: `backend/tests/test_seed_admin.py`
- Modify: `backend/app/admin/router.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 写失败测试（种子脚本）**

`backend/tests/test_seed_admin.py`：

```python
import os
import pytest
from sqlalchemy import select

@pytest.fixture(autouse=True)
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "seed@x.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "SeedPass123")
    monkeypatch.setenv("ADMIN_NAME", "Seeded Admin")


async def test_seed_creates_first_platform_admin(session):
    from app.scripts.seed_admin import seed_admin
    await seed_admin(session)
    await session.commit()

    from app.models.user import User
    from app.models.role import Role
    result = await session.execute(select(User).where(User.email == "seed@x.com"))
    user = result.scalar_one()
    assert user.name == "Seeded Admin"
    assert any(r.code == "PLATFORM_ADMIN" for r in user.roles)
```

- [ ] **Step 2: 实现种子脚本**

`backend/app/scripts/__init__.py`：空。

`backend/app/scripts/seed_admin.py`：

```python
"""一键创建首个 PLATFORM_ADMIN + 根部门 + 默认角色。

用法：
    python -m app.scripts.seed_admin

环境变量：
    ADMIN_EMAIL（必填）
    ADMIN_PASSWORD（必填，≥8 位）
    ADMIN_NAME（可选，默认"平台管理员"）
"""
import asyncio
import os
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.department import Department
from app.models.role import Role
from app.models.user import User


async def seed_admin(session) -> User:
    """幂等：若 email 已存在则跳过；否则创建。"""
    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")
    name = os.environ.get("ADMIN_NAME", "平台管理员")
    if not email or not password:
        raise ValueError("ADMIN_EMAIL and ADMIN_PASSWORD must be set")

    # 已存在则跳过
    existing_result = await session.execute(select(User).where(User.email == email))
    existing_user = existing_result.scalar_one_or_none()
    if existing_user is not None:
        return existing_user

    # 根部门
    dept = (await session.execute(select(Department).where(Department.parent_id.is_(None)))).scalar_one_or_none()
    if dept is None:
        dept = Department(name="总公司", parent_id=None, path="")
        session.add(dept)
        await session.flush()
        dept.path = f"/{dept.id}/"

    # PLATFORM_ADMIN 角色
    role = (await session.execute(select(Role).where(Role.code == "PLATFORM_ADMIN"))).scalar_one_or_none()
    if role is None:
        role = Role(code="PLATFORM_ADMIN", name="平台管理员")
        session.add(role)
        await session.flush()

    # USER 角色（普通用户）
    user_role = (await session.execute(select(Role).where(Role.code == "USER"))).scalar_one_or_none()
    if user_role is None:
        user_role = Role(code="USER", name="普通用户")
        session.add(user_role)
        await session.flush()

    # 创建用户
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
        dept_id=dept.id,
        status=1,
    )
    session.add(user)
    await session.flush()
    from app.models.role import user_roles_table
    from sqlalchemy import insert
    await session.execute(insert(user_roles_table).values(user_id=user.id, role_id=role.id))
    return user


async def main():
    async with SessionLocal() as s:
        u = await seed_admin(s)
        await s.commit()
        print(f"PLATFORM_ADMIN ready: id={u.id} email={u.email}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 运行测试，应通过**

```bash
pytest tests/test_seed_admin.py -v
```

预期：PASS。

- [ ] **Step 4: 写失败测试（用户 CRUD）**

`backend/tests/test_user_admin.py`：

```python
import pytest
from httpx import AsyncClient
from app.core.security import hash_password
from app.models.user import User
from app.models.role import Role, user_roles_table
from sqlalchemy import insert


async def _make_admin_and_login(client, session) -> str:
    role = Role(code="PLATFORM_ADMIN", name="P")
    user = User(email="admin@x.com", name="A", password_hash=hash_password("admin123"))
    session.add_all([role, user])
    await session.flush()
    await session.execute(insert(user_roles_table).values(user_id=user.id, role_id=role.id))
    await session.commit()
    await client.post("/api/auth/login", json={"email": "admin@x.com", "password": "admin123"})


async def test_create_user_as_admin(client: AsyncClient, session):
    await _make_admin_and_login(client, session)

    resp = await client.post(
        "/api/admin/users",
        json={"email": "new@x.com", "name": "New", "password": "newpass123", "dept_id": None, "role_codes": ["USER"]},
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "new@x.com"


async def test_list_users_as_admin(client: AsyncClient, session):
    await _make_admin_and_login(client, session)
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_disable_user_as_admin(client: AsyncClient, session):
    await _make_admin_and_login(client, session)
    # 先创建一个目标用户
    target = User(email="t@x.com", name="T", password_hash=hash_password("t123456"))
    session.add(target)
    await session.commit()

    resp = await client.patch(f"/api/admin/users/{target.id}", json={"status": 0})
    assert resp.status_code == 200
    assert resp.json()["status"] == 0


async def test_non_admin_cannot_create_user(client: AsyncClient, session):
    role = Role(code="USER", name="U")
    user = User(email="u@x.com", name="U", password_hash=hash_password("user123"))
    session.add_all([role, user])
    await session.flush()
    from app.models.role import user_roles_table as urt
    await session.execute(insert(urt).values(user_id=user.id, role_id=role.id))
    await session.commit()
    await client.post("/api/auth/login", json={"email": "u@x.com", "password": "user123"})

    resp = await client.post(
        "/api/admin/users",
        json={"email": "hacker@x.com", "name": "H", "password": "hack1234", "role_codes": ["PLATFORM_ADMIN"]},
    )
    assert resp.status_code == 403
```

- [ ] **Step 5: 实现 schemas**

`backend/app/schemas/user.py`：

```python
from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    dept_id: int | None = None
    role_codes: list[str] = []


class UserUpdate(BaseModel):
    name: str | None = None
    dept_id: int | None = None
    status: int | None = None
    role_codes: list[str] | None = None


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    status: int
    dept_id: int | None
    roles: list[str]

    class Config:
        from_attributes = True
```

- [ ] **Step 6: 实现 router**

`backend/app/users/__init__.py`：空。

`backend/app/users/router.py`：

```python
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, insert, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.core.security import hash_password
from app.db.deps import get_db
from app.models.role import Role, user_roles_table
from app.models.user import User
from app.schemas.user import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


def _require_admin(user: User) -> None:
    if "PLATFORM_ADMIN" not in [r.code for r in user.roles]:
        raise HTTPException(403, "Requires PLATFORM_ADMIN")


async def _load_roles(db: AsyncSession, user: User) -> list[str]:
    # 简单实现：再查一次以拿到最新
    return [r.code for r in user.roles]


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(current_user)],
):
    _require_admin(admin)
    # 邮箱唯一
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already exists")
    # 校验角色 code
    roles_q = await db.execute(select(Role).where(Role.code.in_(body.role_codes or [])))
    roles = roles_q.scalars().all()
    if body.role_codes and len(roles) != len(body.role_codes):
        raise HTTPException(400, "Unknown role code")

    user = User(
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        dept_id=body.dept_id,
        status=1,
    )
    db.add(user)
    await db.flush()
    for r in roles:
        await db.execute(insert(user_roles_table).values(user_id=user.id, role_id=r.id))
    await db.flush()
    user.roles = roles  # type: ignore[assignment]
    return UserOut(
        id=user.id, email=user.email, name=user.name, status=user.status,
        dept_id=user.dept_id, roles=[r.code for r in roles],
    )


@router.get("", response_model=list[UserOut])
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(current_user)],
):
    _require_admin(admin)
    result = await db.execute(select(User).order_by(User.id))
    users = result.scalars().all()
    out = []
    for u in users:
        out.append(UserOut(
            id=u.id, email=u.email, name=u.name, status=u.status, dept_id=u.dept_id,
            roles=[r.code for r in u.roles],
        ))
    return out


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(current_user)],
):
    _require_admin(admin)
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if body.name is not None:
        user.name = body.name
    if body.dept_id is not None:
        user.dept_id = body.dept_id
    if body.status is not None:
        user.status = body.status
    if body.role_codes is not None:
        # 替换角色
        await db.execute(delete(user_roles_table).where(user_roles_table.c.user_id == user.id))
        if body.role_codes:
            roles_q = await db.execute(select(Role).where(Role.code.in_(body.role_codes)))
            roles = roles_q.scalars().all()
            for r in roles:
                await db.execute(insert(user_roles_table).values(user_id=user.id, role_id=r.id))
    await db.flush()
    return UserOut(
        id=user.id, email=user.email, name=user.name, status=user.status,
        dept_id=user.dept_id, roles=[r.code for r in user.roles],
    )
```

- [ ] **Step 7: 修改 admin router**

`backend/app/admin/router.py`：

```python
from fastapi import APIRouter
from app.depts.router import router as depts_router
from app.roles.router import router as roles_router
from app.users.router import router as users_router

router = APIRouter(prefix="/api/admin")
router.include_router(depts_router)
router.include_router(roles_router)
router.include_router(users_router)
```

- [ ] **Step 8: 生成迁移（无新增表，仅 user_roles 数据可能改动）**

```bash
alembic revision --autogenerate -m ""  # 若无变化则不会生成
# 若无新表可跳过
```

- [ ] **Step 9: 运行所有测试**

```bash
pytest -v
```

预期：所有 PASS（含 test_user_admin.py 的 4 个用例）。

- [ ] **Step 10: 提交**

```bash
git add backend/
git commit -m "feat(backend): user CRUD for admin + seed_admin script"
```

---

# 阶段 3 补充：Webhook 兜底端点（spec 7.5、13.3）

## Task 3.4: Dify Webhook 接收 + 去重落库

**Files:**
- Create: `backend/app/webhooks/__init__.py`
- Create: `backend/app/webhooks/router.py`
- Create: `backend/tests/test_webhook.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_webhook.py`：

```python
import pytest
import uuid
from httpx import AsyncClient
from app.models.app import App
from app.models.user import User
from app.models.conversation import Conversation
from app.models.message import Message


async def test_webhook_message_end_dedupes_by_dify_message_id(client: AsyncClient, session):
    user = User(email="w@x.com", name="W", password_hash="x")
    app = App(dify_app_id="dify-1", name="T", mode="chat")
    session.add_all([user, app])
    await session.flush()
    conv = Conversation(user_id=user.id, app_id=app.id, dify_conversation_id="dify-conv-1")
    session.add(conv)
    await session.flush()
    # 已存在 message
    existing = Message(
        conversation_id=conv.id, role="assistant", content="hello",
        dify_message_id="dify-msg-1", dedupe_key="dify-msg-1",
    )
    session.add(existing)
    await session.commit()

    payload = {
        "event": "message_end",
        "conversation_id": "dify-conv-1",
        "message_id": "dify-msg-1",
        "answer": "hello (retry)",
        "metadata": {"usage": {"total_tokens": 5}},
    }
    resp = await client.post("/internal/webhook/dify/message-end", json=payload)
    assert resp.status_code == 200

    # 验证未产生重复
    from sqlalchemy import select, func
    count = await session.execute(
        select(func.count()).select_from(Message).where(Message.dedupe_key == "dify-msg-1")
    )
    assert count.scalar() == 1


async def test_webhook_inserts_new_message(client: AsyncClient, session):
    user = User(email="w2@x.com", name="W", password_hash="x")
    app = App(dify_app_id="dify-2", name="T2", mode="chat")
    session.add_all([user, app])
    await session.flush()
    conv = Conversation(user_id=user.id, app_id=app.id, dify_conversation_id="dify-conv-2")
    session.add(conv)
    await session.commit()

    payload = {
        "event": "message_end",
        "conversation_id": "dify-conv-2",
        "message_id": "dify-msg-2",
        "answer": "first response",
    }
    resp = await client.post("/internal/webhook/dify/message-end", json=payload)
    assert resp.status_code == 200

    from sqlalchemy import select
    msg = (await session.execute(select(Message).where(Message.dedupe_key == "dify-msg-2"))).scalar_one()
    assert msg.content == "first response"
```

- [ ] **Step 2: 实现 webhook router**

`backend/app/webhooks/__init__.py`：空。

`backend/app/webhooks/router.py`：

```python
"""Dify Webhook 接收端点。

Dify 事件 → 我们补齐 messages（防 SSE 中断漏写）。
去重策略：dedupe_key UNIQUE + INSERT ON CONFLICT DO NOTHING（spec 13.3）。
"""
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.models.conversation import Conversation
from app.models.message import Message

router = APIRouter(prefix="/internal/webhook/dify", tags=["webhook"])


@router.post("/message-end")
async def on_message_end(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Dify 工作流/Agent 完成事件 → 兜底补齐 messages。"""
    # MVP：无签名校验（Dify 1.x Webhook 签名机制待确认；先按内网网闸保护）
    payload = await request.json()
    event = payload.get("event")
    if event != "message_end":
        return {"status": "ignored", "reason": f"event={event}"}

    dify_conv_id = payload.get("conversation_id")
    dify_msg_id = payload.get("message_id")
    answer = payload.get("answer", "")
    if not dify_conv_id or not dify_msg_id:
        raise HTTPException(400, "Missing conversation_id or message_id")

    # 找内部对话
    conv_q = await db.execute(select(Conversation).where(Conversation.dify_conversation_id == dify_conv_id))
    conv = conv_q.scalar_one_or_none()
    if conv is None:
        return {"status": "ignored", "reason": "unknown conversation_id"}

    # 用 PG 的 ON CONFLICT DO NOTHING 去重
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    stmt = (
        pg_insert(Message)
        .values(
            conversation_id=conv.id,
            role="assistant",
            content=answer,
            dify_message_id=dify_msg_id,
            dedupe_key=dify_msg_id,
        )
        .on_conflict_do_nothing(index_elements=[Message.dedupe_key])
    )
    await db.execute(stmt)
    await db.commit()
    return {"status": "ok"}
```

- [ ] **Step 3: 修改 main.py**

```python
from app.webhooks.router import router as webhooks_router
app.include_router(webhooks_router)
```

- [ ] **Step 4: 运行所有测试**

```bash
pytest -v
```

预期：webhook 测试通过，所有现有测试仍 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/
git commit -m "feat(backend): dify webhook endpoint with dedup"
```

---

# 后续阶段（占位 - 后续 plan 文件）

阶段 5–7 留作后续 plan：

- **阶段 5**: 前端骨架 + 登录 + 对话 UI（Vite + React + Antd）—— 约 2 周
- **阶段 6**: 管理员后台（用户管理 / 部门管理 / Agent 授权界面）—— 约 1 周
- **阶段 7**: 生产部署（Docker Compose / Nginx TLS / 可观测性）—— 约 1 周

每个阶段都将作为独立 plan 文件，按本文档的 TDD 节奏继续实施。

---

# 验收清单（阶段 0–4 完成时）

- [ ] `pytest -v` 全部通过（阶段 0–4 引入的全部测试）
- [ ] `alembic upgrade head` 无报错
- [ ] curl 可调用 `/api/health` 返回 200
- [ ] curl 可走通登录 → 调 Dify → SSE 流式响应
- [ ] 文件上传大小 / MIME 限制生效（curl 测试超限文件）
- [ ] 跨域 Origin 校验生效（CSRF 中间件测试）
- [ ] 对话消息入库验证（查 conversations / messages 表）
- [ ] 关闭浏览器断流，assistant 消息依然入库（finally 块验证）

完成本计划后，进入阶段 5 之前应再做一轮代码评审与 SSE 压测验证首字节延迟 ≤ 300ms。
# Agent Platform

企业内部 Agent 智能体平台——面向员工的统一"问 AI"入口：React 门户 + FastAPI 网关（鉴权/代理/审计）+ Dify 社区版（编排/RAG 引擎，已独立部署于 `http://192.168.20.226`）。

## 目录结构

```
agent_plantform/
├── backend/      # FastAPI 网关层（鉴权 + Dify 代理 + 审计）
├── frontend/     # React 员工门户（Vite + AntD5 + Zustand）
├── deploy/       # docker compose 编排 + nginx（SSE 关缓冲）+ Makefile
└── docs/
    ├── api-contract.md                        # 前后端 API 契约（只读引用）
    └── superpowers/                           # 设计文档与实施计划
        ├── specs/2026-08-28-agent-platform-design.md
        └── plans/2026-08-28-mvp-phase-1-infra-auth.md
```

## 快速开始

### 开发模式（热重载，前后端分离）

```bash
# 1. Postgres
cd deploy && cp .env.example .env && docker compose up -d postgres

# 2. 后端（uv 管理）
cd ../backend && uv sync
# 按需设置环境变量（参照 deploy/.env.example），然后：
uv run uvicorn app.main:app --reload --port 8000

# 3. 前端（Vite dev server，/api 自动代理到 :8000）
cd ../frontend && npm install && npm run dev
# 打开 http://localhost:5173
```

### 生产模式（全容器）

```bash
cd deploy
cp .env.example .env   # 编辑密钥（生成命令见文件内注释）
make up                # postgres + backend + frontend（nginx）
# Portal:  http://localhost
# 健康检查: http://localhost/api/health
```

> backend/frontend 的 Dockerfile 由各自模块目录提供，详见 `deploy/BUILD_NOTES.md`。

## 开发 vs 生产

| | 开发模式 | 生产模式 |
|---|---|---|
| 后端 | 本机 `uvicorn --reload` | 容器（compose build） |
| 前端 | Vite dev server :5173，`/api` proxy | nginx 静态托管 dist，同源反代 `/api` |
| CORS | DEBUG=true 时后端启用 CORS 中间件 | 同源，无需 CORS |
| 数据库 | Postgres 容器（compose 单服务） | 同左（卷持久化） |
| Dify | `DIFY_BASE_URL` 指向现有实例 | 同左 |

SSE 流式对话依赖 `proxy_buffering off`（已在 `deploy/nginx/conf.d/portal.conf` 配置）；开发模式经 Vite proxy 天然无缓冲。

## 文档索引

- 设计文档：`docs/superpowers/specs/2026-08-28-agent-platform-design.md`
- MVP 实施计划：`docs/superpowers/plans/2026-08-28-mvp-phase-1-infra-auth.md`
- 环境准备：`docs/superpowers/plans/2026-08-28-environment-setup.md`
- API 契约：`docs/api-contract.md`
- 设计稿（Stitch 同步）：`.stitch/designs/`

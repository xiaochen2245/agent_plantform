# 企业内部 Agent 智能体平台 设计文档

| 项目 | 值 |
|---|---|
| 日期 | 2026-08-28 |
| 状态 | 待评审 |
| 范围 | MVP（对话门户 + 多 Agent 授权 + 审计） |

---

## 1. 背景与目标

构建一个企业内部的 Agent 智能体平台，面向公司员工提供统一的"问 AI"入口。底层复用 Dify（社区版）作为 Agent 编排和 RAG 引擎，前端只做面向员工的对话门户，由我们自研的 FastAPI 后端作为鉴权 + 代理 + 审计层。

### 关键决策
- **Dify 角色 = 引擎**（管理员/编辑者使用 Dify 工作流画布和知识库）
- **FastAPI 角色 = 网关**（员工鉴权、App 授权、调用转发、审计落库）
- **前端角色 = 入口**（React 对话 UI，对员工屏蔽 Dify 存在）

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────┐
│ 浏览器 (React SPA)                                      │
│   - Ant Design UI │
│   - axios + fetch+ReadableStream(SSE 消费)               │
│   - JWT 存 httpOnly cookie │
└──────────────────────────┬───────────────────────────────┘
                         │ HTTPS / SSE
                         ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI 后端（BFF + 业务层）                             │
│   - /api/auth/*          JWT 登录、刷新、注销            │
│   - /api/apps/*          用户可见 App 列表、权限过滤     │
│   - /api/chat/*          对话消息、流式响应              │
│   - /api/conversations/* 对话历史（从我们 DB 查）        │
│   - /api/admin/*         用户/部门/角色管理              │
│   - /internal/dify/*     Dify 适配器（API Key 注入、转发）│
│   - /internal/webhook/*  Dify 事件回调                   │
└──────────────┬──────────────────────┬─────────────────────┘
               │                      │
               ▼                      ▼
   ┌───────────────────┐   ┌──────────────────────────────┐
   │ Postgres 16        │   │ Dify 社区版（独立部署）        │
   │ - users            │   │ - 工作流编排画布              │
   │ - departments      │   │ - RAG / 知识库管理            │
   │ - roles            │   │ - 应用 API（chat-messages）   │
   │ - user_roles       │   │ - Webhook → 我们回调          │
   │ - apps             │   └──────────────────────────────┘
   │ - app_authorizations│
   │ - dify_api_keys    │
   │ - conversations    │
   │ - messages         │
   │ - audit_logs       │
   └───────────────────┘
```

---

## 3. 技术栈

### 前端 `frontend/`
- React 18 + TypeScript + Vite
- Ant Design 5
- React Router 6
- Zustand（状态管理）
- Axios（普通请求） + 原生 `fetch + ReadableStream`（SSE 消费）
- dayjs / echarts-for-react（统计）

模块：
- `pages/Login`
- `pages/Chat`（多 App 切换 + 对话窗口）
- `pages/History`
- `pages/Admin/*`（用户/部门/Agent 授权）

### 后端 `backend/`
- Python 3.11+ / FastAPI
- SQLAlchemy 2.0 异步 + asyncpg + Alembic 迁移
- Pydantic v2
- python-jose（JWT）、passlib[bcrypt]（密码哈希）
- httpx（异步 HTTP 客户端，调用 Dify）
- sse-starlette + StreamingResponse（SSE 响应）
- loguru（日志）

模块（按业务域拆分）：
- `app/auth/`：JWT、登录、依赖注入
- `app/users/`、`app/depts/`、`app/roles/`：组织架构 CRUD
- `app/apps/`：App 授权关系（user ↔ app）
- `app/chat/`：对话消息、SSE 代理
- `app/conversations/`：对话历史查询
- `app/dify/`：Dify 适配层（API Key 管理、所有 Dify 调用都在这里）
- `app/admin/`：管理员操作
- `app/db/`：models、session、迁移
- `app/core/`：配置、安全、异常

### 基础设施
- Postgres 16（主存储）
- Redis：MVP 不引入（限流/黑名单二期再加）
- Dify：官方 Docker Compose 独立部署
- Nginx：HTTPS 反向代理、SSE 关闭缓冲
- 整体：Docker Compose 一把梭，企业内网部署

---

## 4. 鉴权与权限模型

### 4.1 身份层
- 邮箱 + 密码（bcrypt 哈希）
- 颁发 JWT：access 15 分钟 + refresh 7 天
- JWT 存 httpOnly + Secure + **SameSite=Strict** cookie
- JWT 载荷：`user_id, roles[], dept_id, jti, exp`（`jti` 用于二期 token 黑名单/主动下线）
- **CSRF 防护**：依赖 `SameSite=Strict`（基本盘）+ 敏感写接口校验 `Origin` / `Referer` 头白名单（`portal.internal.company.com`）
- **Refresh token 入库**：`refresh_tokens` 表（`user_id, token_hash, expires_at, revoked_at, created_at`），支持主动撤销
- 不接企业 SSO，代码预留 `/api/auth/oauth/{provider}/callback` 接口备用

### 4.1.1 密钥拆分
- `JWT_SECRET`：仅用于 JWT 签名
- `ENCRYPTION_KEY`：仅用于 Dify API Key AES 加密（用 `cryptography.fernet.Fernet.generate_key()` 生成）
- 两个 key 完全独立，**任一独立轮转不影响另一个**

### 4.2 组织架构
- `users`：id, email, name, password_hash, status, dept_id, created_at, updated_at
- `departments`：id, name, parent_id（多级）, path（物化路径 `/1/3/7/`)
- `roles`：id, code（USER / APP_ADMIN / PLATFORM_ADMIN）, name
- `user_roles`：user_id, role_id

### 4.3 应用授权
**Dify 自带的"应用"在我们这里叫"Agent"。**

- `apps`：Dify 应用的本地镜像，字段包括 dify_app_id（unique）、name、description、mode、status、synced_at
- `app_authorizations`：app_id + (principal_type, principal_id)，三态主体 `'user' | 'dept' | 'role'`
- 用户对 Agent 的访问权限 = 自己的授权 ∪ 所属部门的授权 ∪ 拥有角色的授权
- `GET /api/apps/me` 返回按当前用户解析后的可见列表
- 调 Dify 前 FastAPI 必须**再校验一次**权限（防绕过前端）

### 4.4 JWT 载荷
- user_id, roles[], dept_id, exp — 足够权限判断，无需每次查 DB

### 4.5 管理员后台（PLATFORM_ADMIN）
- CRUD 用户/部门/角色
- 给用户/部门/角色授权 Agent
- 查看审计日志、对话统计

---

## 5. 对话流与 SSE 代理

### 5.1 消息时序

```
前端                FastAPI                  Dify
 │ POST /api/chat    │                       │
 │ {app_id, query,   │                       │
 │  conversation_id?,│                       │
 │  files?}          │                       │
 │─────────────────> │                       │
 │                   │ 1. JWT 鉴权           │
 │                   │ 2. 权限校验           │
 │                   │ 3. 限流（可选）       │
 │                   │ 4. 落库 user_msg      │
 │                   │ 5. 转发到 Dify        │
 │                   │    POST /chat-messages│
 │                   │─────────────────────>│
 │                   │<─────────────────────│
 │                   │   SSE chunks          │
 │   SSE: event:     │                       │
 │   message,        │                       │
 │   data:{...}      │                       │
 │<──────────────────│                       │
 │                   │                       │
 │   SSE: event:     │                       │
 │   message_end,    │                       │
 │   data:{...}      │                       │
 │<──────────────────│                       │
 │                   │ 6. 落库 assistant_msg │
 │                   │ 7. 审计日志           │
```

### 5.2 SSE 透传（关键）

**反例（buffer）**：

```python
resp = await client.post(...)
full_body = await resp.aread()      # ❌ 阻塞等 Dify 全部生成完
return Response(full_body)          # 用户等 10 秒才看到整段回复
```

**正例（逐 chunk 透传 + 错误兜底）**：

```python
from fastapi.responses import StreamingResponse
import httpx

# 120s 总超时 / 10s 连接超时：Dify 卡死时 worker 可释放；Dify 工作流执行慢也够用
DIFY_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

async def _dify_event_generator(request_body: dict, conv_id: str, app_id: int, user_id: int):
    accumulated_content = ""
    try:
        async with httpx.AsyncClient(timeout=DIFY_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{DIFY_BASE}/v1/chat-messages",
                headers={"Authorization": f"Bearer {DIFY_KEY}"},
                json=request_body,
            ) as resp:
                async for line in resp.aiter_lines():   # ✅ 来一行读一行
                    if not line:
                        continue
                    # 累加内容用于事后入库（仅 message 类型）
                    if line.startswith("data:") and '"event":"message"' in line:
                        try:
                            data = json.loads(line[5:].strip())
                            accumulated_content += data.get("answer", "")
                        except Exception:
                            pass
                    yield line + "\n"                   # ✅ 立即写给前端
    except httpx.TimeoutException:
        yield f"event: error\ndata: {{\"message\": \"Dify timeout\"}}\n\n"
        await audit_log(user_id, "chat.timeout", conv_id, app_id)
    except Exception as e:
        yield f"event: error\ndata: {{\"message\": \"Proxy error\"}}\n\n"
        await audit_log(user_id, "chat.error", conv_id, app_id, error=str(e))
    finally:
        # ✅ 必须 finally：客户端断流时也要保证 assistant 消息入库
        if accumulated_content:
            await save_assistant_message(
                conversation_id=conv_id,
                content=accumulated_content,
                user_id=user_id,
                app_id=app_id,
            )
        yield "event: agent_done\ndata: {}\n\n"

@app.post("/api/chat/send")
async def send_message(body: ChatRequest, user = Depends(current_user)):
    # 权限校验、落库 user_msg ...
    return StreamingResponse(
        _dify_event_generator(dify_payload, conv.id, app.id, user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",      # 关掉 nginx 缓冲
        },
    )
```

### 5.3 易踩坑点

| 坑 | 现象 | 解法 |
|---|---|---|
| `await resp.aread()` | 用户等到结束才看到字 | 改用 `aiter_lines()` / `aiter_bytes()` |
| uvicorn 默认缓冲 | 大响应偶发卡顿 | header `X-Accel-Buffering: no` |
| nginx `proxy_buffering on` | 同上 | nginx 配置 `proxy_buffering off;` |
| 前端用 `EventSource` | 断线自动重发整条消息 | 改用 `fetch + ReadableStream` |

### 5.4 Dify 流式事件类型
- `message`（增量 token）
- `message_end`（结束，含 metadata）
- `error`
- `message_file`（图片等附件）

我们在 `message_end` 后**追加一个自有事件 `agent_done`**，告诉前端"这轮结束，可落本地缓存/打点了"。

### 5.5 流中断与重连
- 鉴权：cookie 自动带 JWT（EventSource 不支持自定义 header）
- 中断：MVP 不做断点续传，二期再实现 `fetch` 手动控制中断 + 重发机制

### 5.6 文件上传（带安全约束）
- 前端 `multipart/form-data` POST `/api/chat/files`
- FastAPI 侧**前置校验**：
  - **大小上限 20MB**（`Content-Length` + 流式检查双重）
  - **MIME 白名单**：`application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `text/plain`, `text/markdown`, `image/png`, `image/jpeg`
  - **文件名清洗**：`werkzeug.utils.secure_filename` 防路径穿越 / 脚本注入
  - **审计**：每条上传记录 `audit_logs`（user_id, file_name, file_size, mime, sha256）
- FastAPI 选存本地 / MinIO
- 转发到 Dify `/files/upload` 拿 `dify_file_id`
- 后续 message 用 `dify_file_id` 作变量 inputs
- **二期再加**：ClamAV 病毒扫描、Sensitive Word 扫描

### 5.7 对话 ID
- 我们生成的 `conversations.id` 用 **UUID**，作为内部 API 主键
- Dify 的 `conversation_id` 存 `conversations.dify_conversation_id` 做关联
- 多轮对话：前端带内部 conv_id 来，FastAPI 取对应的 Dify conv_id 透传

---

## 6. 数据模型（Postgres）

```sql
-- 用户/组织/角色
users
  id BIGSERIAL PK
  email VARCHAR UNIQUE NOT NULL
  name VARCHAR NOT NULL
  password_hash VARCHAR NOT NULL  -- bcrypt
  status SMALLINT DEFAULT 1      -- 1启用0禁用
  dept_id BIGINT FK departments(id)
  created_at, updated_at TIMESTAMPTZ

departments
  id BIGSERIAL PK
  name VARCHAR NOT NULL
  parent_id BIGINT NULL
  path VARCHAR                   -- 物化路径 /1/3/7/
  created_at TIMESTAMPTZ

roles
  id BIGSERIAL PK
  code VARCHAR UNIQUE            -- USER / APP_ADMIN / PLATFORM_ADMIN
  name VARCHAR

user_roles
  user_id, role_id  (复合 PK)

-- Agent 镜像 + 授权
apps
  id BIGSERIAL PK
  dify_app_id VARCHAR UNIQUE
  name, description
  mode VARCHAR                   -- chat / completion / workflow / agent
  status SMALLINT
  synced_at TIMESTAMPTZ
  created_at TIMESTAMPTZ

app_authorizations
  app_id BIGINT FK
  principal_type VARCHAR         -- 'user' | 'dept' | 'role'
  principal_id BIGINT
  PRIMARY KEY (app_id, principal_type, principal_id)

-- Dify 凭据
dify_api_keys
  app_id BIGINT FK apps
  api_key_encrypted VARCHAR       -- AES(SECRET_KEY, raw_key)
  updated_at TIMESTAMPTZ

-- 对话镜像
conversations
  id UUID PK
  user_id BIGINT FK users
  app_id BIGINT FK apps
  dify_conversation_id VARCHAR
  title VARCHAR
  message_count INT DEFAULT 0
  token_usage JSONB              -- {prompt, completion, total}
  created_at, updated_at, deleted_at TIMESTAMPTZ  -- 软删

messages
  id BIGSERIAL PK
  conversation_id UUID FK
  role VARCHAR                   -- 'user' | 'assistant'
  content TEXT
  dify_message_id VARCHAR
  files JSONB                    -- [{file_id, name, url}]
  created_at TIMESTAMPTZ

-- 审计
audit_logs
  id BIGSERIAL PK
  user_id BIGINT
  action VARCHAR                 -- 'chat.send' / 'login' / 'admin.update_user' ...
  resource_type, resource_id
  ip, user_agent
  metadata JSONB
  created_at TIMESTAMPTZ
```

### 关键决策
1. **`conversations.id` 用 UUID**：避免泄露平台用户规模，对外引用安全
2. **软删 `deleted_at`**：合规场景"员工离职删账号"必须保留审计痕迹
3. **`messages.content` 直接 TEXT**：MVP 不做向量化检索；跨对话检索二期再加 pgvector
4. **`audit_logs` 不存 message 原文**：按 `resource_id` join；日志表只存元数据
5. **不引 Redis**：JWT 无状态；限流二期
6. **物化路径索引**：`CREATE INDEX idx_dept_path ON departments(path text_pattern_ops);`（加速子树查询）
7. **Refresh token 入库**：`refresh_tokens(id, user_id, token_hash, expires_at, revoked_at, created_at)`，独立表

### 6.1 术语约定
- **对外**（前端 UI、文档）：统一称"Agent"
- **对内**（DB、代码、API）：统一用 `app` / `apps` / `app_id`
- 这两个是同一个东西：`apps` 表里的一行 ≈ 前端展示的一个 Agent

---

## 7. Dify 集成

### 7.1 API Key 管理
- 每个 Dify 应用一个 API Key
- 存 `dify_api_keys.api_key_encrypted`（用 **`ENCRYPTION_KEY`** + `cryptography.fernet.Fernet` 加密，独立于 JWT 密钥，详见 4.1.1）
- 启动时解密加载到内存，运维改 Key 走管理接口不需重启

### 7.2 应用列表同步
- 启动时：`GET /v1/apps` 全量拉，upsert 到 `apps`
- 增量：Webhook `/internal/webhook/dify/app-events`（MVP 可先不做，**定时任务每 5 分钟全量同步**跑通后再升级 Webhook）

### 7.3 调用 Dify 应用
- httpx 异步客户端，单例 + 连接池
- 超时：`httpx.Timeout(120.0, connect=10.0)`（见 5.2 代码示例）
- **重试策略（按调用类型区分）**：
  - **流式调用**（chat-messages）：**不可重试**——重试会丢失已发出的 token。失败直接给前端 `event: error` + 写审计
  - **非流式调用**（apps 列表、files/upload、webhook 验证）：指数退避（1s / 2s / 4s），最多 3 次；只重试 5xx 和网络错误，4xx 不重试

### 7.4 文件上传
```
前端 → POST /api/chat/files (multipart) → FastAPI
    → 存本地 / MinIO
    → POST Dify /files/upload → 拿 dify_file_id
    → 存 messages.files 关联 dify_file_id
```

### 7.5 Dify Webhook
- `/internal/webhook/dify/*` 接收 Dify 事件
- 应用 CRUD → 更新 `apps`
- 工作流/Agent 完成事件 → 兜底补齐 `messages`（防 SSE 中断漏写）

### 7.6 部署
- 官方 `docker-compose.yaml`，生产改 Postgres/Redis 外部化
- 子域名 `dify.internal.company.com`（内网）
- FastAPI 通过 `DIFY_BASE_URL` 环境变量连接

---

## 8. 社区版 Dify 的限制与对策

社区版相对于企业版有以下限制，列出每条**对我们设计的影响**与**对策**：

| 限制 | 影响范围 | 对策 |
|---|---|---|
| 无原生多租户 | 不影响——我们只装一个工作空间，"多租户"由 FastAPI 这层实现 | — |
| 应用级权限缺失 | 仅影响 Dify 内 5–20 个管理员/编辑者 | 编辑者按角色分组成员；普通员工由 FastAPI `app_authorizations` 授权 |
| 不官方支持 SSO/MFA | 不影响——普通员工走 FastAPI JWT，不登录 Dify | Dify 账号持有者少，靠管理流程约束 |
| 共享工作空间 | 离职编辑者可改坏所有 App | 见下方"运营流程" |

### 运营流程补充（必须执行）

**离职联动禁用**：
- 我们平台 `users.status=0` 时
- 自动调用 Dify 管理 API 禁用其 Dify 账号（如持有）
- 软删该用户在我们 DB 里的对话（`deleted_at = now()`）
- 记录审计日志 `action='user.terminate'`

**Dify 账号最小化原则**：
- 仅给 5–20 人发 Dify 账号（管理员 + Agent 编辑者）
- 编辑者按小组隔离：每个业务 Agent 指定 1–2 名负责人为编辑者
- 任何 Dify App 配置变更都通过我们的审计日志间接记录（操作者是我们这边有 Dify 账号的人）

---

## 9. 部署架构

### 9.1 网络拓扑

```
企业内网
   │
   ▼
浏览器 (员工电脑)
   │
   ▼
Nginx 反向代理/HTTPS
   │ portal.internal.company.com
   ├─ /api/*  →  FastAPI
   └─ /*      →  前端静态
   │
   ▼
FastAPI (2 实例)
   │
   ├──> Postgres 16
   │
   └──> Dify (独立部署)
            │
            └──> 独立 Postgres / Redis
```

### 9.2 网络隔离分档

| 档位 | 措施 | 适用 |
|---|---|---|
| 基础档 | Dify 公网/内网可达，靠 Dify 账号体系约束 | MVP |
| 加固档 | 加网络 ACL：仅 FastAPI 所在网段访问 Dify | 中型合规企业 |
| 极致档 | Dify 离线，仅堡垒机访问 | 金融/政府 |

**MVP 推荐基础档。** 我们 FastAPI 体系已规避大部分暴露面。

### 9.3 Docker Compose 编排

```
/deploy/
├── docker-compose.yml
├── nginx/conf.d/portal.conf
├── backend/
│   ├── Dockerfile
│   ├── alembic/
│   └── .env.example
└── frontend/
    ├── Dockerfile         # 多阶段构建
    └── dist/
```

### 9.4 启动顺序
1. Postgres（独立实例）
2. Dify（官方 compose，外部 Postgres）
3. FastAPI 跑迁移 `alembic upgrade head`，触发 Dify 应用同步初始化 `apps`
4. Nginx 起，门户可用

### 9.5 关键配置
- `nginx.conf`：`proxy_buffering off;`（SSE 必需）
- `backend/.env`：
  - `JWT_SECRET`（强随机，仅 JWT 签名）
  - `ENCRYPTION_KEY`（`Fernet.generate_key()`，仅加密 Dify Key）
  - `DIFY_BASE_URL`
  - `DATABASE_URL`
  - `ALLOWED_ORIGINS`（生产：`https://portal.internal.company.com`；dev：`*`）
- FastAPI 启动脚本：`uvicorn --workers 2`，健康检查 `/api/health`
- **CORS 策略**：
  - 生产：Nginx 同源反代 → 后端无需 CORS 头
  - 开发：Vite 5173 → FastAPI 8000 跨域 → FastAPI 启用 CORS 中间件，`allow_origins` 读取 `ALLOWED_ORIGINS`

### 9.6 FastAPI 多实例与 SSE
- "实例数 × workers" = 容器副本数 × uvicorn worker 数
- SSE 连接由 nginx 随机分到任一 worker，**这是 OK 的**（SSE 长连接本身无状态）
- 每个 worker 各自维护自己的 httpx AsyncClient 单例（**不要跨 worker 共享**）
- 排障：每个 worker 打 `worker_id` 到日志，便于定位是哪个 worker 处理了某次 SSE

---

## 10. 分阶段交付

### MVP（6–10 周，2 后端 + 1 前端 + 0.3 SRE）
> **4–6 周过于乐观**——Dify 生产部署、外部化 PG/Redis、SSE 三层联调、多态授权一致性、CSRF/文件上传安全均需工时。

**核心交付**：
- Dify 部署调通（外部化 Postgres / Redis、镜像版本锁定）
- FastAPI 骨架 + JWT 登录 + CSRF 防护 + 密钥拆分
- 对话流（含 SSE 透传 + 错误兜底 + 写库时机）
- 多 Agent 授权（用户/部门/角色三态）
- 对话镜像 + 审计日志
- 文件上传（大小 / MIME / 文件名安全约束）
- 基础管理员后台：**用户管理 + Agent 授权**（部门 / 角色 CRUD 放二期）

### 二期
- 限流（Redis 令牌桶）
- 用量配额（每用户/部门每月 token 上限）
- 用户反馈（👍/👎）落库
- 管理员统计视图（活跃用户、热门 App、token 成本）
- SSE 断点续传
- 离职联动禁用 Dify 账号的自动化脚本
- pgvector 跨对话检索（如果需要）
- 部门 / 角色 CRUD
- ClamAV 病毒扫描 / Sensitive Word 扫描

### 三期（视需求）
- 多模态上传预览
- 团队协作（共享对话）
- 外部知识源集成（Confluence、SharePoint）
- SSO 接入企业身份源

---

## 11. 测试策略

- **后端**：pytest + httpx AsyncClient，关键模块单测覆盖率 ≥70%
  - `app/dify/` 适配层 mock Dify 响应
  - SSE 流式转发用 fake async generator 验证透传 + finally 块写库
  - 权限解析函数重点单测（多态主体）
- **前端**：Vitest + React Testing Library
- **E2E**：Playwright，覆盖以下路径：
  - 登录 → 选 Agent → 发起对话 → 看到流式回复 → 历史里能看到
  - 权限拒绝路径：用户试图调未授权 Agent
  - Dify 5xx 路径：mock Dify 返回 500，验证前端看到 `event: error` + 审计落库
  - 文件上传失败路径：超大文件 / 非法 MIME
  - JWT 过期刷新路径：access 过期自动用 refresh 续期
- **手动验证清单**：SSE 首字节延迟 ≤300ms、断流后页面不崩、删除用户联动禁用 Dify

### 11.1 可观测性（二期重点）
MVP 至少保证**日志结构化**：
- FastAPI 关键节点：JWT 鉴权失败、权限拒绝、SSE 流开始/结束、Dify 调用耗时
- loguru + JSON formatter，关键字段：`worker_id`, `user_id`, `conv_id`, `app_id`, `duration_ms`
- 日志聚合：MVP 用 `docker logs` + 文件落盘；二期接 ELK / Loki
- 监控指标：SSE 首字节延迟 P50/P95、Dify 调用成功率、worker 进程内存——二期接 Prometheus

---

## 12. 项目结构

```
/mnt/e/program/agent_platform/
├── backend/                  # FastAPI 后端
│   ├── app/
│   │   ├── auth/
│   │   ├── users/
│   │   ├── depts/
│   │   ├── roles/
│   │   ├── apps/
│   │   ├── chat/
│   │   ├── conversations/
│   │   ├── dify/             # Dify 适配层
│   │   ├── admin/
│   │   ├── db/
│   │   └── core/
│   ├── alembic/
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
│
├── frontend/                 # React 前端
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Login.tsx
│   │   │   ├── Chat.tsx
│   │   │   ├── History.tsx
│   │   │   └── Admin/
│   │   ├── components/
│   │   ├── api/
│   │   ├── stores/
│   │   └── App.tsx
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile
│
├── deploy/
│   ├── docker-compose.yml
│   └── nginx/
│
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-08-28-agent-platform-design.md
```

---

## 13. 实施前补充（来自第二轮评审）

实施初期就要对齐的工程细节，避免后期返工：

### 13.1 httpx 客户端生命周期
- `httpx.AsyncClient` **在 `app/core/lifespan` 中按 worker 初始化**（FastAPI `lifespan` context manager），作为模块级单例
- 整个进程生命周期复用同一客户端，复用连接池
- 关闭时 `await client.aclose()`
- **不要每次请求 `async with httpx.AsyncClient(...)` 新建**（早期 5.2 示例代码是教学简化，实施时按本节执行）

### 13.2 CSRF 校验的最小端点清单
"敏感写接口"的最小覆盖范围（`Origin` / `Referer` 校验）：

| 端点 | 方法 | 必须校验 Origin |
|---|---|---|
| `/api/chat/send` | POST | ✅ |
| `/api/chat/files` | POST | ✅ |
| `/api/auth/refresh` | POST | ✅ |
| `/api/auth/logout` | POST | ✅ |
| `/api/admin/*` | 任意写 | ✅ |
| `GET / HEAD / OPTIONS` | 任意 | ❌ 跳过 |

实现位置：`app/core/middleware.py` 的 `CSRFMiddleware`，所有写方法自动拦截。

### 13.3 Webhook 兜底的去重策略
- `messages.dedupe_key = dify_message_id`（**新增字段**，UNIQUE 索引）
- Webhook 补写时 `INSERT ... ON CONFLICT (dedupe_key) DO NOTHING`
- SSE 已写入 + Webhook 重放 → 不会产生重复

### 13.4 流式累加的完整字段
- `event: message` → 累加 `answer` 到 `accumulated_content`
- `event: message_end` → 解析 `metadata.usage` → 同步更新 `conversations.token_usage`（JSONB）
- `event: message_file` → 单独存到 `messages.files` JSONB（图片、附件引用）
- 流结束后 `finally` 块用完整字段写 messages 表

### 13.5 文件上传大小校验实现
- 双层校验：
  1. 请求进来时读 `Content-Length` header 立即拒绝（>20MB 直接 413）
  2. 流式边读边累加字节数，超限立即中断连接（防止恶意请求不发 Content-Length 绕过）

---

## 14. 待办与风险

### 待办
- [ ] 选定部署机器规格（FastAPI 2 实例至少 2C4G，**Dify 至少 8C16G——官方镜像内嵌 Postgres + Redis 很重，生产必须外部化**）
- [ ] 选定 Dify 镜像版本（**只取后端**：dify-api / dify-sandbox / dify-plugin-daemon / dify-plugin-runtime，**不要 dify-web 与 Dify 自带 nginx**；锁定次版本号，定期升级）
- [ ] Dify API Key 轮转策略（建议 90 天）
- [ ] **离职联动禁用 Dify 账号的实现路径确认**：
  - 优先：调研 Dify 社区版"禁用账号"管理 API 是否真的存在（查 Swagger）
  - 备选：若 API 不存在或权限不够，降级为"手动禁用脚本 + 审计触发提醒"
- [ ] **日志脱敏清单**：
  - loguru 严禁打印完整 message content / 用户密码 / Dify API Key
  - `audit_logs.metadata` 不存 message 原文
  - 错误日志中 token / cookie 一律脱敏为 `***`
- [ ] 对账脚本设计：每天凌晨基于 `dify_app_id` 集合 diff，丢失的本地授权关系通过管理员告警而不是自动删除（避免脚本误删授权）

### 风险
| 风险 | 严重度 | 缓解 |
|---|---|---|
| SSE 缓冲导致体感差 | 高 | header + 代码双重保险；上线前压测首字节延迟 |
| Dify API 变更破坏适配层 | 中 | 锁版本，升级有专项 review |
| 离职员工用旧 cookie 调通 | 中 | JWT 短过期 + jti 黑名单机制（二期） |
| 对话数据双写不一致 | 中 | Webhook 兜底同步；定期对账脚本（仅告警不删） |
| LLM 成本失控 | 中 | 二期引入 token 配额 |
| CSRF / 文件上传安全 | 高 | SameSite=Strict + Origin 校验 + 文件白名单 + 大小限制 |
| SECRET_KEY 单点泄漏 | 中 | 已拆分为 JWT_SECRET + ENCRYPTION_KEY（见 4.1.1） |
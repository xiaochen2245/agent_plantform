# API 契约 v1（切片只读引用 · 无人修改此文件）

> 来源：docs/superpowers/plans/2026-08-28-mvp-phase-1-infra-auth.md 的 router + Pydantic DTO。
> s1（backend）按此实现；s2（frontend）按此 mock。变更须走 orchestrator。

## 通用
- Base: `/api`；JSON UTF-8
- 鉴权：JWT 存 httpOnly cookie `access_token_cookie`（15min）；刷新令牌 `refresh_token_cookie`（7d）；均 `SameSite=Strict; Path=/`（dev HTTP 下 `secure=false`）
- CSRF：所有写接口校验 `Origin` 白名单（dev 含 `http://localhost:5173`），非法 → 403 `{"detail":"Forbidden: invalid origin"}`
- 错误形：`{"detail": "<message>"}`；未认证 → 401

## Auth（s1 本切片实现）
| 端点 | 请求 | 成功 | 失败 |
|---|---|---|---|
| POST /api/auth/login | `{"email":"a@b.com","password":"..."}` | 200 空体 + Set-Cookie ×2 | 401 `Invalid credentials` |
| POST /api/auth/refresh | cookie | 200 新 cookie ×2（旧 refresh 轮转作废） | 401 |
| POST /api/auth/logout | cookie | 200 清 cookie | — |
| GET /api/auth/me | cookie | 200 `{"id":1,"email":"...","name":"...","roles":["USER"],"dept_id":null}` | 401 |

- 密码 bcrypt；email 唯一
- 种子用户（dev）：`admin@company.com / admin123`（roles 含 `PLATFORM_ADMIN`）

## Apps（s2 mock · s1 后续切片实现）
- GET /api/apps/me → `{"apps":[{"id":1,"name":"IT 运维助手","description":"解答服务器、网络与账号问题","mode":"chat"},{"id":2,"name":"报销政策问答","description":"差旅与报销规则查询","mode":"chat"},{"id":3,"name":"代码评审助手","description":"MR 预审与规范检查","mode":"agent"}]}`

## Chat（s2 mock · SSE）
- POST /api/chat/send `{"app_id":1,"query":"...","conversation_id":""}`
- 响应 `text/event-stream`，事件（`data:` 为 JSON）：
  - `event: message` → `{"answer":"增量文本"}`
  - `event: message_end` → `{"metadata":{"usage":{"total":128}}}`
  - `event: error` → `{"message":"..."}`
  - `event: agent_done` → `{}`（自有事件，前端落缓存/打点信号）

## Conversations（s2 mock）
- GET /api/conversations?app_id=1 → `{"items":[{"id":"uuid","title":"首条问题截断","message_count":6,"updated_at":"ISO8601"}]}`

## 前后端联调（dev）
- Vite dev server 5173，`/api` proxy → `http://localhost:8000`（同源携带 cookie）

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

---

# v2 增补（wave3 · orchestrator 批准）

## 会话消息详情（替代 History 页 mock 兜底）
- GET /api/conversations/{id}/messages（需登录，仅本人会话）→
  `{"messages":[{"id":"<int>","role":"user|assistant","content":"...","created_at":"ISO8601"}]}`（按 created_at asc）

## Admin（仅 PLATFORM_ADMIN，403 否则）
- GET /api/admin/users?query=&status=&page=1&page_size=20 →
  `{"total":128,"items":[{"id":1,"name":"张明","email":"...","dept":null,"roles":["PLATFORM_ADMIN"],"status":1,"created_at":"ISO8601"}]}`
- POST /api/admin/users `{"name":"李雷","email":"lei@company.com","password":"...","dept_id":null,"roles":["USER"]}` → 201 同上形状（缺省 roles=["USER"]）
- PATCH /api/admin/users/{id} `{"name"?,"dept_id"?,"roles"?,"status"?}`（status 1/0）→ 200 同上形状
- POST /api/admin/users/{id}/reset_password → `{"password":"<8位随机>"}`（同时失效其全部 refresh token）
- GET /api/admin/users/{id}/apps → `{"app_ids":[1,3]}`
- PUT /api/admin/users/{id}/apps `{"app_ids":[1,3]}` → 200（用户级授权全量替换；dept/role 级授权本期无端点，模型保留三态）

## 授权语义（/api/apps/me 行为变更）
- 可见 = 用户直授 ∪ 所属部门 ∪ 拥有角色；无任何授权的 App 不可见、chat/send 调用 403 `{"detail":"Not authorized for this app"}`
- 种子：三个 App 默认授给角色 `USER`（全员可见，保持现有体验）；admin（PLATFORM_ADMIN）不受限
- 支撑表：roles(id,code,name)+user_roles、departments(id,name,parent_id,path)、app_authorizations(app_id,principal_type['user'|'dept'|'role'],principal_id)

---

# v3 增补（wave4 · orchestrator 批准 · 工作流模式）

## AppOut 扩展
- 新增 `mode:"workflow"` 应用 + `inputs_schema` 字段（可空数组）：
  `"inputs_schema":[{"name":"business_card","label":"名片内容","type":"paragraph","required":true}]`（type: text|paragraph）
- 种子新增 app 4：`{"id":4,"name":"名片生成助手","description":"输入名片信息，生成排版名片","mode":"workflow","inputs_schema":[如上]}`

## chat/send 扩展（请求体）
- `"inputs": {"<变量名>":"<值>"}`（可选对象；workflow 模式应用按 inputs_schema 校验，缺必填 → 400 `{"detail":"missing required input: <name>"}`）

## chat/send 行为（workflow 模式）
- 后端调 Dify `POST /v1/workflows/run`（response_mode=streaming），**事件翻译为统一对话词汇表**后再透传，前端无感知：
  - `workflow_started` / `node_started` / `node_finished` / `ping` → 丢弃（不透传）
  - `text_chunk`（data.text）→ `message`（answer=data.text）
  - `workflow_finished` → `message_end`（metadata.usage.total_tokens=data.total_tokens）
  - 上游 error → `error`（契约形状）
  - 结束仍追加自有 `agent_done`
- workflow 模式无 Dify 会话概念：dify_conversation_id 置空；我方 conversation 照常创建/复用（title 取首个 inputs 值或 query 前 20 字）

---

# v4 增补（wave5 · orchestrator 批准 · 文件上传）

## 上传
- POST /api/chat/files（需登录+CSRF，multipart/form-data，字段 file）→ 201
  `{"file_id":"f_01H...","name":"报告.pdf","size":1048576,"mime":"application/pdf"}`
- 前置校验（前端+后端双重）：大小 ≤ 20MB（超限 413）；MIME 白名单：
  application/pdf, application/vnd.openxmlformats-officedocument.wordprocessingml.document,
  text/plain, text/markdown, image/png, image/jpeg（非法 400 `{"detail":"unsupported file type"}`）

## 消息携带附件
- chat/send 请求体新增 `"files":["f_01H..."]`（可选数组，chat/agent 模式有效；workflow 模式暂不 attachment）
- AppOut 无变化；conversations/{id}/messages 的 message 新增 `"files":[{"file_id","name","size","mime"}]`（可空数组）

## 后端语义（wave6 实现声明）
- 校验通过后转存（MVP 本地卷）并同步上传 Dify /files/upload 换取 dify_file_id；消息发送时作为 inputs 附件变量传给 chat-messages

---

# v5 增补（wave6 hotfix · orchestrator 批准）

## agent_done 事件携带会话 id
- `event: agent_done` → `data: {"conversation_id":"<我方内部 UUID>"}`（原先恒为 {}）
- 语义：前端首轮发送（conversation_id 传空串）后，凭此 id 认领会话；前端**不得**自行生成会话 id

---

# v6 增补（wave7 · orchestrator 批准 · 思考过程透传）

## 流式新事件：reasoning（条件出现）
- `event: reasoning` → `data: {"content":"<增量思考文本>"}`
- 仅当上游模型返回思考内容时出现；普通回复流中完全缺失（前端零打扰）
- 后端兼容多种上游形态（自动探测）：message 事件携带 `reasoning_content`/`reasoning`/`thought` 字段，或独立 `agent_thought` 事件

## 消息持久化
- messages 详情端点的 message 新增 `"reasoning": string | null`（无思考为 null）

## 前端展示语义
- 思考面板：可折叠「思考过程」；生成中且尚无正文时默认展开，正文开始/流结束后默认收起；历史回放默认收起

---

# v7 增补（wave8 · orchestrator 批准 · 知识库）

## 通用
- 响应形状 = Dify Knowledge API **原样透传**（含 `data[]/total/has_more/page/limit`）
- 鉴权：后端用独立的 `DIFY_DATASET_API_KEY`（工作区级，与 per-app key 是两套凭证）；未配置 → 503 `{"detail":"knowledge base service not configured"}`
- 上游 4xx → 同码 + 精简 message；上游 5xx/网络错误 → 502 `{"detail":"knowledge service unavailable"}`

## 端点（prefix /api/kb）
| 端点 | 权限 | 说明 |
|---|---|---|
| GET /api/kb/datasets?page=&page_size= | 登录 | 知识库列表（透传） |
| GET /api/kb/datasets/{id}/documents?page=&page_size=&keyword= | 登录 | 文档列表（含 `indexing_status`；前端对未终态文档 5s 轮询） |
| POST /api/kb/datasets/{id}/documents/text `{"name","text","indexing_technique"}` | PLATFORM_ADMIN | 201 透传 create-by-text 响应 |
| POST /api/kb/datasets/{id}/documents/file（multipart：`file` + `indexing_technique` 表单域） | PLATFORM_ADMIN | ≤20MB；文档类 MIME 白名单（pdf/docx/pptx/xlsx/txt/md/csv/html/json）；201 透传 |
| DELETE /api/kb/datasets/{id}/documents/{document_id} | PLATFORM_ADMIN | 204 |
| POST /api/kb/datasets/{id}/retrieve `{"query"}` | 登录 | 命中测试，透传 `{query:{...}, records:[{score,segment:{content,document:{name}}}]}`（records 为顶层字段） |

## 语义与边界
- `indexing_technique`（high_quality|economy）必须与目标库一致；由前端从 datasets 列表取值透传
- App↔知识库的**绑定**只能在 Dify 控制台完成（Service API 无此能力）；门户 /kb = 文档管理 + 检索测试
- Dataset key 作用域 = 创建该 key 的成员可见的库；知识库需在 Dify 控制台授权全员后才对平台可见
- 操作审计：后端结构化日志（user/dataset/doc id），不落库

---

# v8 增补（wave9 · orchestrator 批准 · 知识库租户隔离）

## 语义
- 隔离在**网关层**实现（Dify 侧仍是工作区级 key，所有门户用户共享一个上游身份）
- `dataset_authorizations` 表：dataset_id（Dify UUID，无本地外键）× principal（user/dept/role 三态，同 app_authorizations）
- 可见 = 用户直授 ∪ 所属部门 ∪ 拥有角色；PLATFORM_ADMIN 不受限（恒全量）
- **默认关闭**：无任何授权记录的知识库对非管理员不可见；列表过滤 + 作用域端点（documents/上传/删除/retrieve）一律 403 `{"detail":"Not authorized for this dataset"}`

## 端点变更
- GET /api/kb/datasets：非管理员仅返回授权集合内的库（data 过滤、total 重算；分页取上游页后过滤，内部规模下语义等价）
- 其余 /api/kb/datasets/{id}/* 端点：入口统一过 `_require_dataset_access` 门

## Admin 新增（仅 PLATFORM_ADMIN）
- GET /api/admin/users/{id}/datasets → `{"dataset_ids":["<dify-uuid>"]}`
- PUT /api/admin/users/{id}/datasets `{"dataset_ids":[...]}` → 200（用户级全量替换；dataset_id 不做本地存在性校验——Dify 是真相源，前端仅从实际目录勾选）

---

# v9 增补（wave10 · orchestrator 批准 · 库级管理与审计）

## 端点（kb 前缀，除注明外均 PLATFORM_ADMIN）
| 端点 | 说明 |
|---|---|
| POST /api/kb/datasets `{"name","indexing_technique"?}` | 建空知识库 → 201 透传 Dify dataset |
| DELETE /api/kb/datasets/{id} | 删库 → 204；本地授权行同步清理 |
| GET /api/kb/datasets/{id}/grants | 该库三态授权全量 → `{"items":[{principal_type,principal_id,name}]}`（name 为主体名称，已删主体为 null） |
| POST /api/kb/datasets/{id}/grants `{"principal_type","principal_id"}` | 单条授权（幂等 upsert）；主体不存在 → 404 |
| DELETE /api/kb/datasets/{id}/grants/{principal_type}/{principal_id} | 移除单条授权（幂等）→ 204 |
| GET /api/kb/audit?page=&page_size= | 审计流水（新在前）→ `{"total","items":[{id,user,action,dataset_id,detail,created_at}]}` |

## 目录端点（授权选择器数据源）
- GET /api/admin/depts → `{"items":[{id,name}]}`
- GET /api/admin/roles → `{"items":[{id,code,name}]}`

## 审计语义
- 表 kb_audit_logs：user_id（无外键，用户删除后仍可追溯）/ action / dataset_id / detail(JSON) / created_at
- 记录动作：dataset_create / dataset_delete / doc_create_text / doc_create_file / doc_delete / grant_add / grant_remove
- 与主操作同事务提交；用户级全量替换端点（PUT /admin/users/{id}/datasets）不逐条审计

## 前端观感
- 思考过程面板与正文统一走打字机平滑（useTypewriter）

---

# v10 增补（RAG P0 · 检索参数化 + 切片通道 + 引用溯源）

## 端点（prefix /api/rag）
| 端点 | 权限 | 说明 |
|---|---|---|
| POST /api/rag/retrieval | 登录 | 请求体新增可选：`similarity_threshold`(0-1)、`vector_similarity_weight`(0-1)、`rerank_id`、`keyword`、`highlight`、`top_n`(默认 10，网关自有截断→引擎 `page_size`)；**不再透传** RAGFlow 已弃用的 `top_k`/`knn_top_k`。响应 chunks 每项：`id/content/document_id/document_keyword/dataset_id/similarity/term_similarity/vector_similarity/positions/highlight` |
| GET /api/rag/datasets/{ds}/documents/{doc}/chunks?keywords=&page=&page_size= | 登录 | 切片分页（page_size 默认 20 上限 100）→ `{"chunks":[{id,content,document_id,available,important_keywords,positions}],"total"}` |
| GET /api/rag/datasets/{ds}/documents/{doc}/chunks/{cid} | 登录 | 单切片同形状 |
| PATCH /api/rag/datasets/{ds}/documents/{doc}/chunks/{cid} `{content?,available?,important_keywords?}` | PLATFORM_ADMIN | 切片手动纠错；入 rag 审计 `chunk.update` |
| DELETE /api/rag/datasets/{ds}/documents/{doc}/chunks/{cid} | PLATFORM_ADMIN | 删切片（引擎批量端点收敛为单条语义）；`chunk.delete` |
| POST /api/rag/datasets/{ds}/documents/{doc}/parse | 登录 | 重试解析（口径对齐上传）→ 202；`doc.parse` |

## 语义变更
- **P0-0 全量库绑定**：portal-assistant 绑定租户全量库（原只绑首库 → 多库部门问答检索不全）。复用已有 assistant 时若绑定漂移自动 PUT 同步；同步失败仅告警并沿用旧绑定，不阻断问答
- **P0-① SSE 引用全字段**：POST /api/rag/chat/completions 的 `reference.chunks` 每项透传全字段并补 `document_name`（= 引擎 `document_keyword`），不做任何截断；无 reference 的帧字节原样透传

# 接入 Dify 知识库（/kb 页面实体化）

> 状态：终稿 —— 已完成全部实探（对 Dify 1.17.0 生产实例全链路冒烟），无未决设计问题

## Context

- 前端侧栏"编 辑"区已有 `/kb` 入口，当前是 ComingSoon 占位页；本次把它做成真实的知识库管理页
- 用户提供：Dataset API Key `dataset-ES8Jv6PD5jhChieCaT4u8wkh`，目标知识库 `General Mode-ECO 1`，端点 `http://192.168.20.226/v1`
- 前置故障已修复：Dify `FILES_URL` 为空导致 extractor 插件拿相对路径报错 → 已设为 `http://192.168.20.226` 并重建容器（备份 `.env.bak-filesurl`）

## 实探结论（Dify 1.17.0，已冒烟验证）

| 事项 | 结论 |
|---|---|
| key 作用域 | dataset key 只能看到**创建该 key 的成员**有权限的库（实测：新建库立即可见、删库即消失）。**`General Mode-ECO 1` 当前不可见 → 需在 Dify 控制台把该库权限改为全员，或用库主人账号重建 key**（用户操作，部署前完成） |
| create-by-text/file | 均必传 `indexing_technique`（须与目标库一致；datasets 列表返回该字段，前端透传即可，无需回查） |
| multipart 形状 | `data` 字段（JSON+`application/json`）携 `{indexing_technique, process_rule:{mode:"automatic"}}` + `file` 字段携内容 —— 已实测 200 |
| 文档列表 | 自带 `indexing_status`/`display_status`/`error`/`word_count`/`hit_count`/`enabled` → **轮询列表即得索引状态，无需 batch 状态端点** |
| retrieve | 端点可用；报错过一次是因冒烟新建库默认 OpenAI embedding 未配置，非 API 问题（真实库用已配模型，待库可见后复验） |
| 删除 | 文档/库均 204 |

## 方案

权限（沿用现有角色体系，最小授权）：
- **读**（列表/文档/命中测试）：所有登录用户
- **写**（上传/删除）：仅 `PLATFORM_ADMIN`（复用 `require_platform_admin`）
- 操作审计：仅后端日志（`app.core.config` 已有 logger 体系），不建新表——Dify 侧自带操作记录，YAGNI

边界说明（写进页面提示）：App ↔ 知识库的绑定只能在 Dify 控制台完成，Service API 无此能力；门户 /kb = 文档管理 + 检索测试。

### 后端

`app/dify/client.py` 追加（全部非流式 JSON）：
- `dataset_api_key()`：读 env `DIFY_DATASET_API_KEY`（每次调用时读，便于测试注入）
- `DifyDatasetError(status_code, message)`：上游 4xx/5xx 载体
- `DifyClient` 方法：`list_datasets` / `list_documents(keyword)` / `create_doc_by_text` / `create_doc_by_file`（multipart：data+file）/ `delete_document` / `retrieve`，共用 `_check_dataset_resp`（非 2xx → DifyDatasetError；message 取上游 code/message）

`app/kb/router.py`（新，prefix `/api/kb`）：

| 端点 | 权限 | 语义 |
|---|---|---|
| GET `/api/kb/datasets?page=&page_size=` | 登录 | 透传 Dify 列表（`{total,has_more,page,limit,data[]}`） |
| GET `/api/kb/datasets/{id}/documents?page=&page_size=&keyword=` | 登录 | 透传文档列表 |
| POST `/api/kb/datasets/{id}/documents/text` `{name,text,indexing_technique}` | admin | create-by-text → 201 透传响应 |
| POST `/api/kb/datasets/{id}/documents/file`（multipart `file`+`indexing_technique`） | admin | 大小≤20MB + 文档类 MIME 白名单 → 直转 Dify **不落本地盘** |
| DELETE `/api/kb/datasets/{id}/documents/{document_id}` | admin | 204 |
| POST `/api/kb/datasets/{id}/retrieve` `{query}` | 登录 | 命中测试，透传 records |

- key 未配置 → 503 `{"detail":"knowledge base service not configured"}`
- 上游 4xx → 同码 `{"detail": 精简message}`；5xx/网络 → 502；精简复用 `app.chat.service._summarize_upstream_error`（剥 URL/堆栈，B2 同款）
- 上传校验复用 `app/files/router.py` 的 `sanitize_filename` 与大小预检模式；MIME 白名单：pdf/docx/txt/md/csv/xlsx/pptx/html/json
- `main.py` 挂载 router；`app/schemas/kb.py` 放两个小 DTO

### 前端

- `src/api/kb.ts`（新）：axios 封装，类型对齐 Dify 透传形状（`KbDataset`/`KbDocument`/`RetrieveRecord`）；`createDocByFile` 用 FormData
- `src/pages/Knowledge.tsx`（新）替换 `/kb` 路由（`App.tsx`）：
  - 知识库表格（名称/文档数/字数/索引模式 tag/创建时间）→ 行点击选中
  - 文档表格（名称/字数/索引状态 tag/命中数/时间/删除-Popconfirm），**存在未终态文档时 5s 轮询列表**；状态色：completed=绿 error=红 paused=橙 其余=蓝（中文文案映射）
  - admin 工具条：antd Upload（customRequest 直传）+ "添加文本" 弹窗（名称+正文）
  - 命中测试卡：Input.Search → 记录列表（score + 片段 + 文档名）
  - `isAdmin` 取 `useAuthStore`，非 admin 不渲染写操作（后端 403 兜底）
- `src/mocks/handlers.ts`：补 `/api/kb/*` mock（种子库就叫 `General Mode-ECO 1`）
- 测试：`pages/Knowledge.test.tsx`（渲染列表 + admin/非admin 按钮可见性）、backend `tests/test_kb.py`（透传/403/错误映射/503，MockTransport 复用 fake_dify 模式）

### 契约与部署

- `docs/api-contract.md` 追加 v7 知识库段（端点表 + 权限 + 透传形状声明）
- `deploy/.env.example`、`backend/.env`（本地）、服务器 `/root/agent-platform/.env`：加 `DIFY_DATASET_API_KEY`
- 部署走既有流：commit → `deploy/scripts/server-build.sh`（push develop → 服务器构建 → compose up）

## Files to modify

- `backend/app/dify/client.py`、`backend/app/kb/router.py`（新）、`backend/app/schemas/kb.py`（新）、`backend/app/main.py`
- `backend/tests/test_kb.py`（新）、`backend/tests/fake_dify.py`（补 dataset 替身）
- `frontend/src/api/kb.ts`（新）、`frontend/src/pages/Knowledge.tsx`（新）、`frontend/src/App.tsx`、`frontend/src/mocks/handlers.ts`、`frontend/src/pages/Knowledge.test.tsx`（新）
- `docs/api-contract.md`、`deploy/.env.example`、`backend/.env`

## Steps

- [ ] 1. 后端：client dataset 方法 + kb router + schemas + main 挂载
- [ ] 2. 后端测试 `test_kb.py` 全绿（透传/权限 403/上游错误映射/key 缺失 503）
- [ ] 3. 前端：api/kb.ts + Knowledge 页 + 路由替换 + mock + 测试
- [ ] 4. 契约 v7 + env 三处（example/本地/服务器）
- [ ] 5. 本地端到端：uvicorn + vite，对着真实 Dify 传文件/文本 → 观察索引轮询 → 命中测试 → 删除
- [ ] 6. 提交并 `server-build.sh` 部署，线上冒烟
- [ ] 7. 用户在 Dify 控制台将 `General Mode-ECO 1` 权限改为全员（或换 key）→ 平台内复验该库可见可管

## Verification

1. `cd backend && uv run pytest`；`cd frontend && npx vitest run`；`npx tsc --noEmit`
2. 本地起前后端，用 `General Mode-ECO 1`（库共享后）或任一可见库：上传 txt → 状态从 indexing 轮询到 completed → 命中测试返回片段 → 删除
3. 普通员工账号登录：可见列表/命中测试，无上传删除按钮；直调写接口 403

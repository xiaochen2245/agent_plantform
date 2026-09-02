# 工作流编辑器（前端画布）设计文档

| 项目 | 值 |
|---|---|
| 日期 | 2026-09-01 |
| 状态 | 待评审（修订 v3） |
| 关联设计 | `2026-08-28-agent-platform-design.md` |
| 关联计划 | `2026-08-28-mvp-phase-1-infra-auth.md` |

## 修订记录

- **v3 (2026-09-01)**：第二轮评审反馈修正
  - **Critical**：`mode="yaml-content"`（非 workflow）；env var 重构到 workflow 作用域（不再伪造 `model.environment_variables`）；save 流程改 confirm→create_api_key 顺序；validator 补 orphan chain 检测；删除虚构的 `end` 终结类型
  - **Major**：If-Match 格式定为带引号 epoch_ms；webhook 不动我们乐观锁字段；API key 创建加审计；4E 拆分为 1w+1w；补 409 响应 body schema
  - **Minor**：参数重命名 `app_id`→`dify_app_id`；新增路由文档化；admin key 轮转说明；confirm_import 失败处理
- **v2**：Console API 路径、test-run body、optimistic lock、audit logs、knowledge-retrieval `multiple_retrieval_config` 等
- **v1**：初稿

---

## 1. 背景与目标

主设计文档定位 Dify 为"引擎"、FastAPI 为"网关/审计层"。管理员/编辑者（5–20 人）原本需要登录 Dify 自带 Web UI（仅内网）来编排 Agent 工作流。

**本设计目标**：在不替换 Dify 后端执行引擎的前提下，给管理员一个自研的拖拽式工作流画布，生成 Dify 兼容的 DSL 并通过 FastAPI 提交给 Dify 执行。员工侧体验不变。

### 关键决策
- **画布 = UI 层**：`@xyflow/react`
- **Dify = DAG 存储与执行层**：单一数据源，不在 DB 存 DSL
- **后端 FastAPI = DSL 校验 + 代理 + 审计 + 乐观锁**
- **范围收敛**：MVP 5 个节点（Start / LLM / KnowledgeRetrieval / IfElse / Answer）
- **并发控制**：乐观锁（`updated_at` + If-Match 头）
- **权限**：APP_ADMIN 全局可访问画布（不按 App 范围拆分，二期再做）

### 非目标
- ❌ 自研 DAG 执行引擎
- ❌ chatflow / agent / completion 模式画布
- ❌ 实时协作
- ❌ Dify 全部 20+ 节点全类型
- ❌ APP_ADMIN 按 App 范围隔离权限

---

## 2. 整体架构

```
┌────────────────────────────────────────────────────────┐
│ 浏览器 (React SPA + @xyflow/react + Tailwind prefix tw-)│
│   ┌──────────────────────┐ ┌─────────────────────────┐ │
│   │ 工作流画布（编辑态）   │ │ 调试面板（运行态）       │ │
│   │ - 节点拖拽 / 连线     │ │ - 输入 + 流式输出        │ │
│   │ - 节点配置抽屉        │ │ - 节点执行轨迹          │ │
│   │ - 撤销重做 + 乐观锁   │ │                         │ │
│   └──────────────────────┘ └─────────────────────────┘ │
└────────────┬───────────────────────────────────────────┘
             │ /api/admin/workflow/* + /api/admin/workflow/datasets
             │ 带 If-Match: "<updated_at_epoch_ms>"
             ▼
┌────────────────────────────────────────────────────────┐
│ FastAPI 后端                                            │
│   ┌─────────────────────────┐ ┌────────────────────────┐│
│   │ admin/workflow/* router │ │ dify/client.py（扩展）  ││
│   │ - DSL 校验（Pydantic v2）│ │ - import_app()         ││
│   │ - 乐观锁 409            │ │ - confirm_import()     ││
│   │ - audit_logs 写入       │ │ - export_app()         ││
│   │ - 创建/查询 API key     │ │ - create_api_key()     ││
│   │                         │ │ - run_workflow_stream()││
│   │                         │ │ - list_datasets()      ││
│   └─────────────────────────┘ └────────────────────────┘│
└────────────┬───────────────────────────────────────────┘
             │ Console API + Service API
             ▼
        Dify 1.17 (dify-api:5001)
```

**关键边界**：
- 画布 → 生成 DSL JSON，调 `/api/admin/workflow/save`（带 If-Match）
- FastAPI → DSL 校验 + 乐观锁 + 调 Dify + audit_logs + 回填 api_key
- Dify → DAG 存储与执行
- 编辑中状态仅在浏览器内存 + localStorage（配额满时按 LRU 驱逐 undo 历史）

**Dify 版本**：1.17.0（社区版）。**注意**：原 env-setup.md §3.2 固定 `langgenius/dify-api:1.1.0`、main plan Task 0.2 `git checkout 1.1.0` —— **实施阶段 4A 第一天**必须同步更新这两处到 1.17.0（含镜像 digest）。

---

## 3. DSL 数据模型

### 3.1 顶层结构

```typescript
export interface WorkflowDSL {
  app: {
    description: string;
    icon: string;                    // emoji
    icon_background: string;         // hex color
    mode: 'workflow';
    name: string;
    use_icon_as_answer_icon: boolean;
  };
  kind: 'app';
  version: '0.1.5';                  // DSL schema 版本（week 1 与 1.17 export 对比确认）
  workflow: {
    conversation_variables: [];
    environment_variables: EnvLLMVariable[];   // ★ 1.17 在这里，不是 model 字段
    features: WorkflowFeatures;
    graph: {
      edges: WorkflowEdge[];
      nodes: WorkflowNode[];
    };
    graph_persistence: true;
    version: '0.1.5';
    workflow_api_id?: string;        // 第一次保存后由 Dify 回填
  };
}
```

前端用 JSON 流转，提交 Dify 时用 `js-yaml` 序列化为 YAML。

### 3.2 节点类型（5 个）

| 类型 | 关键字段 |
|---|---|
| `start` | `variables[]` 定义工作流输入（如 `user_query`） |
| `knowledge-retrieval` | `dataset_ids[]`、`query_variable_selector`、`retrieval_mode`、`top_k`、`score_threshold`、`max_tokens`、`multiple_retrieval_config` |
| `llm` | `model.id`（引用 environment_variable 的 id）、`prompt_template[]`、`context?` |
| `if-else` | `cases[]` 含 `conditions[]`、`logical_operator` |
| `answer` | `answer` 字符串（支持 `{{#node_id.variable#}}`） |

**节点 ID 规范**：必须匹配正则 `^[a-z][a-z0-9_]*$`（Dify 模板 `{{#node_id.var#}}` 用下划线，不用连字符）。前端 `nanoid` 自定义 alphabet `abcdefghijklmnopqrstuvwxyz0123456789_`。

### 3.3 连线 schema

```typescript
export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  type: 'custom';
  data: {
    isInIteration: false;
    sourceType: NodeType;
    targetType: NodeType;
    sourceHandle?: 'true' | 'false';   // If/Else 分支标识
  };
}
```

**If/Else 约束**：必须有两个出边，分别带 `sourceHandle: 'true'` 和 `'false'`，且每个分支最终必须连接到一个**终结点**（answer）。

### 3.4 变量引用约定

- **节点输入**（如 `query_variable_selector: ["start_node", "user_query"]`）：`[节点id, 变量名]` 数组
- **Prompt / Answer 模板**：`{{#node_id.variable#}}` 文本占位符
  - 跨节点：`{{#llm_node.text#}}`
  - 当前节点变量：`{{#sys.user_query#}}`（Start 节点输入带 `sys.` 前缀；week 1 与 1.17 export 比对确认）

### 3.5 LLM 环境变量（workflow 作用域）

> ⚠️ **PENDING WEEK-1 VERIFICATION**：本节描述的 `workflow.environment_variables[].value_type: "llm"` 结构与 LLM 节点通过 `model.id` 引用的机制，基于 Dify 1.17 公开文档推断，**未直接验证 Dify 源码**。**week 1 day 1 必做**：在 Dify Web UI 里建一个工作流 → 加 LLM 节点 → 把 Model 切到 "variable mode" → 导出 YAML → 把原始字段粘到本节作为权威。如果 schema 不符，按本节末尾降级方案回退。

**重要更正（v3）**：Dify 1.17 的"可复用 LLM 环境变量"特性，env var **位于 `workflow.environment_variables[]`（workflow 作用域）**，而非 LLM 节点的 `model.environment_variables` 字段。每个 entry 形如：

```typescript
export interface EnvLLMVariable {
  id: string;                       // 唯一 ID，供 LLM 节点引用
  name: string;                     // 显示名
  value_type: 'llm';                // 1.17: 标记为 LLM 配置型
  value: {
    provider: string;               // 如 'langgenius/openai/openai'
    model_name: string;             // 如 'gpt-4'
    model_parameters: {
      temperature: number;
      max_tokens: number;
      top_p?: number;
      presence_penalty?: number;
      frequency_penalty?: number;
    };
  };
}
```

**LLM 节点的引用方式**：节点的 `model` 不再含 `provider/name/completion_params`，只存 `model.id` 指向 env var 的 id。Dify UI 里叫"switch the Model field to variable mode"。

**预设配置文件**（运维手动维护，是 env var 的源头）：

```yaml
# backend/config/llm_presets.yaml
# 注意：Dify 1.17 env var value 内的模型参数字段名待 week 1 验证（已知候选：
# `model_parameters` 或 `completion_params`）。若 Dify 实际用 `completion_params`，
# 本文件保持 `model_parameters` 不变，service.py 注入时做字段名映射。
presets:
  - id: for_qa
    display_name: 问答专用
    value_type: llm
    value:
      provider: langgenius/openai/openai
      model_name: gpt-4
      model_parameters:
        temperature: 0.3
        max_tokens: 1024

  - id: for_summary
    display_name: 摘要专用
    value_type: llm
    value:
      provider: langgenius/openai/openai
      model_name: gpt-3.5-turbo
      model_parameters:
        temperature: 0.5
        max_tokens: 512
```

**数据流**：
```
llm_presets.yaml (运维维护)
        ↓ FastAPI 启动时加载到 Settings.LLM_PRESETS
        ↓
GET /api/admin/workflow/llm-presets  → 前端 LLM 节点下拉
        ↓ 管理员在画布选 for_qa
        ↓ 保存时：FastAPI 把 presets 注入 workflow.environment_variables[]
        ↓
Dify /v1/apps/imports 提交 DSL（env vars 嵌在 DSL 里）
```

**降级方案**（如果 1.17 实际字段不是这样）：week 1 必须用 Dify 1.17 导出样例验证。若 schema 不同，最小代价的回退是直接让 LLM 节点配置完整的 `model.provider/name/model_parameters`（即绕过 env var 抽象），前端 LLMConfigForm 把 presets 字段平铺即可。

---

## 4. 前端组件结构

### 4.1 文件树（增量）

```
frontend/src/
├── workflow/
│   ├── components/
│   │   ├── Canvas/
│   │   │   ├── WorkflowCanvas.tsx
│   │   │   ├── NodePalette.tsx
│   │   │   ├── VariableInspector.tsx
│   │   │   ├── Toolbar.tsx
│   │   │   └── nodes/
│   │   │       ├── StartNode.tsx
│   │   │       ├── LLMNode.tsx
│   │   │       ├── KnowledgeRetrievalNode.tsx
│   │   │       ├── IfElseNode.tsx
│   │   │       └── AnswerNode.tsx
│   │   ├── ConfigDrawer/
│   │   │   ├── ConfigDrawer.tsx
│   │   │   ├── StartConfigForm.tsx
│   │   │   ├── LLMConfigForm.tsx
│   │   │   ├── KnowledgeRetrievalConfigForm.tsx
│   │   │   ├── IfElseConfigForm.tsx
│   │   │   └── AnswerConfigForm.tsx
│   │   ├── DebugPanel/
│   │   │   ├── DebugPanel.tsx
│   │   │   ├── InputForm.tsx
│   │   │   ├── StreamOutput.tsx
│   │   │   └── NodeTraceList.tsx
│   │   ├── ConflictModal/
│   │   │   └── ConflictModal.tsx        # 409 三选项
│   │   ├── RestoreDraftModal/
│   │   │   └── RestoreDraftModal.tsx    # 刷新后草稿恢复
│   │   └── ValidationPanel/
│   │       └── ValidationPanel.tsx
│   ├── store/
│   │   ├── workflowStore.ts
│   │   ├── historyStore.ts              # zundo
│   │   └── debugStore.ts
│   ├── types/
│   │   ├── dsl.ts
│   │   ├── nodes.ts
│   │   └── edges.ts
│   ├── serializer/
│   │   ├── toDSL.ts
│   │   ├── fromDSL.ts
│   │   └── validate.ts
│   └── api/
│       ├── workflowApi.ts               # save/load/test-run（含 409 处理）
│       ├── llmPresetsApi.ts
│       └── datasetsApi.ts
├── pages/Admin/
│   ├── UserManagement.tsx               # 已存在
│   ├── DepartmentManagement.tsx
│   ├── AppAuthorization.tsx
│   └── WorkflowEditor.tsx               # ★ 新增
```

### 4.2 路由（React Router）

```typescript
// 在现有 admin 路由下新增：
<Route path="/admin/workflow/new" element={<WorkflowEditor />} />
<Route path="/admin/workflow/:appId" element={<WorkflowEditor />} />
// :appId 是我们本地 apps.id（BigInt），不是 dify_app_id
// WorkflowEditor 根据有无 :appId 决定"新建"或"编辑现有"
```

### 4.3 Zustand Store（含乐观锁 + 草稿恢复）

```typescript
interface WorkflowStore {
  nodes: Node[];
  edges: Edge[];
  title: string;
  description: string;
  iconEmoji: string;

  // 同步状态
  difyAppId: string | null;
  updatedAt: number | null;             // 服务器最后更新时间（epoch_ms，用于 If-Match）
  updatedBy: { id: number; name: string } | null;
  dirty: boolean;
  lastSavedAt: Date | null;
  saveError: WorkflowSaveError | null;

  setNodes(nodes: Node[]): void;
  addNode(type: NodeType, position: XYPosition): void;
  updateNodeData(id: string, data: Partial<NodeData>): void;
  removeNode(id: string): void;
  setEdges(edges: Edge[]): void;
  markClean(): void;

  // 草稿 + 配额
  hydrateFromLocal(): { savedAt: Date; serverUpdatedAt: number | null } | null;
  saveToLocal(): void;                   // try/catch；QuotaExceededError 时 LRU 驱逐
  clearLocal(): void;
}

type WorkflowSaveError =
  | { kind: 'validation'; issues: { nodeId?: string; field?: string; message: string }[] }
  | { kind: 'conflict'; serverUpdatedAt: number; serverUpdatedBy: { id: number; name: string } }
  | { kind: 'network'; message: string }
  | { kind: 'dify_error'; message: string; details?: unknown };
```

**乐观锁保存流**：
```
1. 前端点击"保存"
2. workflowStore.serialize() → DSL JSON
3. POST /api/admin/workflow/save
   header: If-Match: "<updatedAt>"
   body: { dify_app_id, dsl }
4a. 200 OK → 更新 updatedAt + difyAppId + markClean
4b. 409 Conflict → 保存服务器版本到 store；弹 ConflictModal：
    "服务器版本更新于 <ts>，由 <user> 修改。本地版本更新于 <ts>。
     [保留我的（强制覆盖）]  [加载服务器版本]  [取消]"
4c. validation error → ValidationPanel 标错
```

**localStorage 草稿恢复**：
- 每次变更触发 `saveToLocal()`（节流 1s）
- `saveToLocal()` 包 try/catch，捕获 `QuotaExceededError` 时：
  - 调用 `historyStore.trim(10)`（zundo 减少 10 步）
  - 删最旧的 localStorage 备份键
  - 重试一次；仍失败则 toast "本地存储已满，无法保留草稿"
- 进编辑器时检测到草稿 → `RestoreDraftModal` 显示本地 vs 服务器时间戳，用户二选一

### 4.4 If-Match 头格式（前后端约定）

```
header:   If-Match: "<updated_at_epoch_ms>"
example:  If-Match: "1725148800123"
```

- **存储**：`updatedAt: number`（epoch_ms，整数）
- **HTTP 头**：带双引号的字符串（RFC 7232 强 ETag 格式）
- **解析**：FastAPI 端 `request.headers.get("If-Match", "").strip('"')` → 字符串 → `int()` → 比较
- **省略**：新建时无 If-Match 头（后端视为"无冲突基线"）

### 4.5 调试面板

布局：右侧抽屉，宽度 480px。功能：
- **输入表单**：根据 Start 节点 `variables` 动态生成
- **输出 Tab**：流式显示最终 answer
- **节点轨迹 Tab**：实时追加 Dify 全部 SSE 事件：
  - `workflow_started` / `workflow_finished`（含 `total_tokens`、`total_steps`、`status`）
  - `node_started` / `node_finished`（节点 ID、耗时）
  - `message` / `message_end` / `error` / `done`

实现：复用 chat 的 `fetch + ReadableStream` + `X-Accel-Buffering: no` + FastAPI 侧 `aiter_lines` 透传。

---

## 5. 后端集成

### 5.1 FastAPI 新增端点

```python
# /api/admin/workflow/* 仅 PLATFORM_ADMIN ∪ APP_ADMIN 可访问

POST   /api/admin/workflow/save
       headers: If-Match: "<updated_at_epoch_ms>"（新建时省略）
       body: { dify_app_id?: string, dsl: WorkflowDSL }
       流程：
         1. 校验角色 + If-Match 一致性 → 不一致返回 409
         2. Pydantic v2 model_validator 校验 DSL 结构
         3. 注入 llm_presets 到 workflow.environment_variables[]（具体算法）：
            a. 遍历 workflow.graph.nodes，收集所有 type="llm" 节点的 model.id 集合 → unique_ids
            b. 对每个 id，查 Settings.LLM_PRESETS；任一找不到 → 返回 422
               { error: "unknown_llm_preset", invalid_ids: [<missing_id>...] }
            c. 对每个有效 preset，按 §3.5 EnvLLMVariable 结构构造 entry
            d. 合并到 dsl.workflow.environment_variables：若已有同名 id 则**覆盖**
               （运维预设优先级 > DSL 内可能存在的旧值），否则追加
            e. LLM 节点的 model.id 字段保留不变（Dify 通过 model.id 解析引用）
         4. 序列化 DSL 为 YAML
         5. 调 Dify POST /console/api/apps/imports（mode="yaml-content", yaml_content, 可选 app_id）
            - 注：Dify 仅允许 workflow / chatflow app 通过 app_id 覆盖；其他 mode 会被拒
         6. 若返回 status="pending" → POST /console/api/apps/imports/{import_id}/confirm
         5'. 若返回 status="failed" → 返回 502
            { error: "dify_import_failed", details: import_response }
            audit_logs: action='workflow.save_failed'
            不调用 create_api_key；不更新 apps 表；不动 updated_at
         7. 拿到 dify_app_id；若是首次保存（dify_api_keys 无记录），调
            POST /console/api/apps/{id}/api-keys 拿 api_key，Fernet 加密存入 dify_api_keys
            audit_logs: action='workflow.create_api_key'
         8. 更新本地 apps 表（dify_app_id / name / description / mode='workflow' / api_key）
         9. 更新 apps.updated_at = now() / updated_by_user_id = actor（乐观锁字段）
        10. audit_logs: action='workflow.save'（含 actor_user_id / dify_app_id / dsl_version）
       响应: { dify_app_id, updated_at: epoch_ms }

       409 响应 body:
       { error: "version_conflict",
         server: { updated_at: epoch_ms, updated_by: { id, name } } }

GET    /api/admin/workflow/{app_id}
       → 查本地 apps 表拿 dify_app_id
       → 调 Dify GET /console/api/apps/{dify_app_id}/export?include_secret=false
       → 返回 WorkflowDSL JSON（前端编辑回填用）
       → audit_logs: action='workflow.load'

POST   /api/admin/workflow/test-run
       body: { app_id: int, inputs: dict, query: string, user?: string }
       → user 默认 f"workflow-editor-{admin_user_id}"
       → 查 apps.dify_api_keys 解密
       → 调 Dify POST /v1/workflows/run（workflow 模式专用）
         body: { inputs, query, user, response_mode: "streaming" }
       → SSE 流式透传
       → audit_logs: action='workflow.test_run'

GET    /api/admin/workflow/llm-presets
       → 返回 llm_presets.yaml 内容

GET    /api/admin/workflow/datasets?keyword=&page=1&limit=20
       → 调 Dify GET /console/api/datasets?keyword=&page=&limit=
       → 支持服务端搜索
       → 返回 [{ id, name, document_count, ... }]

DELETE /api/admin/workflow/{app_id}
       → 本地 apps.status=0（软删）
       → 调 Dify DELETE /console/api/apps/{dify_app_id}
       → audit_logs: action='workflow.delete'
```

### 5.2 DSL 校验（Pydantic v2）

```python
# backend/app/admin/workflow/schemas.py
from pydantic import BaseModel, model_validator

class WorkflowDSL(BaseModel):
    # ... 字段定义同 v2 ...

    @model_validator(mode="after")
    def validate_graph(self):
        """校验：
        1. 必有 1 个 start 节点
        2. 必有至少 1 个 answer 节点
        3. 每个非终结非 start 节点必须能从 start 到达
        4. 从 start 出发的每条路径必须到达 answer 节点
        """
        TERMINAL_TYPES = {"answer"}    # 仅 answer（end 是 chatflow 的概念）
        nodes_by_id = {n.id: n for n in self.workflow.graph.nodes}
        adj = {n.id: [] for n in self.workflow.graph.nodes}
        for e in self.workflow.graph.edges:
            adj[e.source].append((e.target, e.data.sourceHandle))

        # 节点类型计数
        types = [n.data.type for n in self.workflow.graph.nodes]
        if types.count("start") != 1:
            raise ValueError(f"工作流必须有且仅有一个 start 节点（当前 {types.count('start')}）")
        if types.count("answer") < 1:
            raise ValueError("工作流必须至少有一个 answer 终结节点")

        def reachable_terminal(start: str, visited: set) -> bool:
            """DFS 检查 start → 任意 answer。**目标：明确拒绝任何环**（包括自环和
            跨节点环）；命中已访问节点 = 路径上存在环 = 立刻返回 False（拒绝）。
            UI 层在创建边时也应禁止自环和双节点环（§5.2 是兜底安全网）。"""
            if start in visited:
                return False   # 命中已访问 = 环，拒绝
            visited = visited | {start}
            if nodes_by_id[start].data.type in TERMINAL_TYPES:
                return True
            for tgt, _ in adj.get(start, []):
                if reachable_terminal(tgt, visited):
                    return True
            return False

        def reachable_from(start: str, visited: set) -> set:
            """BFS 返回从 start 可达的所有节点 ID。"""
            if start in visited:
                return set()
            visited = visited | {start}
            out = {start}
            for tgt, _ in adj.get(start, []):
                out |= reachable_from(tgt, visited)
            return out

        # 找到 start 节点
        start_id = next(n.id for n in self.workflow.graph.nodes if n.data.type == "start")

        # 检查 1: start 必须有后继
        if not adj[start_id]:
            raise ValueError(f"Start 节点 {start_id} 没有后继")

        # 检查 2: 从 start 可达的每条路径必须到 answer
        for tgt, _ in adj[start_id]:
            if not reachable_terminal(tgt, set()):
                raise ValueError(f"Start {start_id} → {tgt} 路径未到达 answer")

        # 检查 3: If/Else 双分支约束
        for n in self.workflow.graph.nodes:
            if n.data.type == "if-else":
                branches = [t for t, h in adj[n.id] if h in ("true", "false")]
                if len(branches) != 2:
                    raise ValueError(f"IfElse {n.id} 必须有 2 个带 sourceHandle 的出边")
                for tgt in set(branches):  # 用 set 允许两条边指向同一节点
                    if not reachable_terminal(tgt, set()):
                        raise ValueError(f"IfElse {n.id} 分支 {tgt} 未到达 answer")

        # 检查 4: 没有悬空节点（不可达节点）
        reachable = reachable_from(start_id, set())
        orphans = [n.id for n in self.workflow.graph.nodes if n.id not in reachable]
        if orphans:
            raise ValueError(f"存在不可达节点（无路径从 start 连通）：{orphans}")

        return self
```

**允许的边界情况**：
- 单节点工作流：`Start → Answer`（最简）
- 空工作流不允许（提示"请添加至少一个节点"）
- 双分支汇合：`IfElse → Answer_true` 和 `IfElse → Answer_false` 指向同一 Answer（允许）
- 环：DFS 标记 visited 避免无限循环，命中环则不算"到达终结点"（不允许）

### 5.3 DifyClient 扩展（Console API）

```python
# backend/app/dify/client.py 新增方法

class DifyClient:
    def __init__(self, base_url, encryption_key, *, http_client=None, admin_key: str):
        self._admin_key = admin_key
        # ...

    @property
    def _console_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._admin_key}"}

    async def import_app(self, dsl_yaml: str, dify_app_id: str | None = None) -> dict:
        """两步 import：第一步返回 {id, status, app_id?}
        - status="completed" → 直接拿 app_id
        - status="pending" → 需调用 confirm_import
        仅 workflow / chatflow app 允许通过 dify_app_id 在 body 中传做就地覆盖。
        """
        body = {"mode": "yaml-content", "yaml_content": dsl_yaml}
        if dify_app_id:
            body["app_id"] = dify_app_id
        resp = await self._client.post(
            f"{self.base_url}/console/api/apps/imports",
            headers={**self._console_headers, "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        return resp.json()   # 返回完整响应（含 id/status/app_id）

    async def confirm_import(self, import_id: str) -> dict:
        """返回 {app_id, status}。status="failed" 抛异常。"""
        resp = await self._client.post(
            f"{self.base_url}/console/api/apps/imports/{import_id}/confirm",
            headers=self._console_headers,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "failed":
            raise RuntimeError(f"Dify import confirm failed: {data}")
        return data

    async def export_app(self, dify_app_id: str, include_secret: bool = False) -> dict:
        resp = await self._client.get(
            f"{self.base_url}/console/api/apps/{dify_app_id}/export",
            params={"include_secret": str(include_secret).lower()},
            headers=self._console_headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def create_api_key(self, dify_app_id: str) -> str:
        """import + confirm 成功后调用，存到 dify_api_keys（加密）"""
        resp = await self._client.post(
            f"{self.base_url}/console/api/apps/{dify_app_id}/api-keys",
            headers=self._console_headers,
        )
        resp.raise_for_status()
        return resp.json()["api_key"]

    async def run_workflow_stream(self, encrypted_api_key: str, payload: dict):
        """调 /v1/workflows/run 流式透传（workflow 模式专用）"""
        client = await self._ensure_client()
        async with client.stream(
            "POST",
            f"{self.base_url}/v1/workflows/run",
            headers={"Authorization": f"Bearer {self._decrypt_key(encrypted_api_key)}"},
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                yield line

    async def list_datasets(self, keyword: str = "", page: int = 1, limit: int = 20) -> list[dict]:
        resp = await self._client.get(
            f"{self.base_url}/console/api/datasets",
            params={"keyword": keyword, "page": page, "limit": limit},
            headers=self._console_headers,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
```

### 5.4 数据库变更（Alembic 迁移）

```python
# alembic/versions/xxx_add_workflow_editor.py
def upgrade():
    # 1. apps.mode 加 CHECK 约束
    op.create_check_constraint(
        "ck_apps_mode",
        "apps",
        "mode IN ('chat', 'completion', 'workflow', 'agent')",
    )
    # 2. apps 加乐观锁 + 审计字段（仅改我们本地的 apps 表，与 Dify 内部 schema 无关）
    op.add_column("apps", sa.Column(
        "updated_at", sa.DateTime(timezone=True),
        server_default=sa.func.now(), nullable=False,
    ))
    op.add_column("apps", sa.Column(
        "updated_by_user_id", sa.BigInteger,
        sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    ))
    op.create_index("ix_apps_updated_at", "apps", ["updated_at"])
```

**Dify admin key 加密存储**：
- 新增 `dify_admin_keys` 表（id, encrypted_key, created_at, updated_at）
- 启动时 `lifespan.py` 解密加载到 `app.state.dify_admin_key`
- 通过 env var `DIFY_ADMIN_KEY_ENCRYPTED`（Fernet 加密）注入

### 5.5 权限

- `/api/admin/workflow/*`：`require_platform_admin` ∪ `require_app_admin`（**全局 APP_ADMIN**，不分 App）
- CSRF middleware 已覆盖 `/api/admin/*` 前缀，无需改动
- 员工端 `/api/chat/*` 不变

### 5.6 Webhook 与乐观锁的边界

Dify 的 webhook handler（主设计 §7.5）会对 app 更新事件做同步。**关键约束**：

> 我们 apps 表的 `updated_at` 和 `updated_by_user_id` **专属于工作流编辑器**。Webhook handler 在收到 app update 事件时**不得**覆盖这两个字段，仅同步 `name/description/mode/status` 等基础元数据。

实现位置：`backend/app/apps/sync.py` 的 webhook 处理函数，加注释 + 单测断言"webhook 不修改 updated_by_user_id"。

---

## 6. 风险与验证

### 6.1 实施第一周必验证（6 项硬性）

| 验证项 | 通过标准 | 不通过的降级 |
|---|---|---|
| `POST /console/api/apps/imports` 两步流程 | 传 `dify_app_id` 字段实现就地覆盖；status=pending 时 confirm 返回真实 app_id | 不支持覆盖：每次新建 + DELETE 旧 app |
| `GET /console/api/apps/{id}/export` 返回完整 DSL | 响应含 `workflow.graph` | 改用其他 export 路径（参考 Dify `web/service/apps.ts`） |
| Dify 1.17 `workflow.environment_variables[]` 结构 + LLM 节点 `model.id` 引用机制 | 导入后 Dify Web 能看到 env var；LLM 节点引用 id 工作 | 退化到在 LLM 节点里直接配 model.provider/name/model_parameters（绕过 env var 抽象） |
| `POST /v1/workflows/run` 流式响应事件 | 含 `workflow_started` / `node_started` / `workflow_finished` | 退回到 `/v1/chat-messages`（schema 略不同） |
| `POST /console/api/apps/{id}/api-keys` 存在 | 返回 `api_key` 字符串 | 改用 `GET /console/api/apps/{id}/api-keys` 取已有 key |
| Webhook handler 不修改我们 `apps.updated_at` / `updated_by_user_id` | 触发 Dify app update webhook 后查本地 apps 表：updated_at 不变、updated_by_user_id 不变 | 单测 `test_webhook_isolation.py` 强制断言；违反则 CI 失败 |

**同步更新其他文档**：week 1 第一天把 `2026-08-28-environment-setup.md` §3.2 的镜像 tag 从 `langgenius/dify-api:1.1.0` 改为 `1.17.0`（含 digest），把 `2026-08-28-mvp-phase-1-infra-auth.md` Task 0.2 的 `git checkout 1.1.0` 改为 `1.17.0`。这两处改动单独提交。

### 6.2 风险清单

| 风险 | 严重度 | 缓解 |
|---|---|---|
| Dify 1.17 Console API 路径变更（v3 已修正核心路径） | 中 | §6.1 第一周验证 |
| env var 结构与设计不符 | 中 | §3.5 降级方案 |
| 本地草稿与服务器并发冲突 | 中 | §4.3 乐观锁 409 + ConflictModal 三选项 |
| localStorage 配额满 | 低 | §4.3 LRU 驱逐 |
| Dify 5xx 时编辑丢失 | 中 | FastAPI 侧指数退避重试（1s/2s/4s × 3）；客户端内存态保留，失败时不重置 |
| Dify webhook 覆盖我们乐观锁字段 | 中 | §5.6 webhook 不得改 updated_at / updated_by_user_id |
| Tailwind 与 Ant Design 样式冲突 | 低 | §7.3 `prefix: 'tw-'` 隔离 |
| 1.17 太新导致 schema 漂移 | 中 | 锁定次版本号（1.17.x），升级走专项 review |
| Dify admin key 轮转与 ENCRYPTION_KEY 冲突 | 低 | 轮转 admin key：重跑 setup 脚本（新 ENCRYPTION_KEY 重加密）；运维流程文档化 |

### 6.3 回退方案

- **Feature flag**：`FEATURE_WORKFLOW_EDITOR=false` 时路由 404，管理员继续用 Dify Web
- **数据回滚**：每周 `pg_dump` 备份 apps 表；误操作可通过恢复 `apps.dify_app_id` 映射重建
- **Dify 侧清理**：`docs/scripts/cleanup_dify_apps.py`（按 last_sync_at 删 N 天前未更新的 workflow app）

### 6.4 不在 MVP 范围（明确排除）

- 实时协作（OT/CRDT）
- 节点版本历史 / 草稿对比 / diff 可视化
- APP_ADMIN 按 App 范围隔离
- 节点模板市场 / 复制节点
- LLM env var 热加载（SIGHUP）
- chatflow / agent / completion 模式画布

---

## 7. 依赖与配置

### 7.1 package.json 新增

```json
{
  "dependencies": {
    "@xyflow/react": "^12.0.0",
    "@dagrejs/dagre": "^1.1.0",
    "tailwindcss": "^3.4.0",
    "js-yaml": "^4.1.0",
    "nanoid": "^5.0.0",
    "zod": "^3.22.0",
    "zundo": "^2.2.0",
    "immer": "^10.0.0"
  }
}
```

### 7.2 Tailwind 配置

```js
// tailwind.config.js
module.exports = {
  prefix: 'tw-',                    // 避免与 Ant Design class 冲突
  corePlugins: { preflight: false }, // 关闭 Tailwind reset（Ant Design 自带）
  content: ['./src/workflow/**/*.{ts,tsx}'],
};
```

### 7.3 backend 新增文件

```
backend/
├── config/
│   └── llm_presets.yaml
└── app/
    ├── admin/
    │   └── workflow/
    │       ├── __init__.py
    │       ├── router.py
    │       ├── schemas.py
    │       └── service.py           # 含 audit_logs 写入
    ├── apps/
    │   └── sync.py                  # ★ 加注释：webhook 不改 updated_at / updated_by_user_id
    ├── core/
    │   └── lifespan.py              # ★ 扩展：读 llm_presets + 解密 admin_key
    └── dify/
        └── client.py                # ★ 扩展（§5.3）
```

### 7.4 新增环境变量（`.env.example`）

```bash
# Dify 管理员 Console API long-lived key（在 Dify Web → 设置 → API 密钥生成）
DIFY_ADMIN_KEY_ENCRYPTED=<fernet-encrypted-dify-admin-key>

# LLM 预设文件路径
LLM_PRESETS_FILE=./config/llm_presets.yaml

# 工作流编辑器总开关（false 时路由 404）
FEATURE_WORKFLOW_EDITOR=true
```

加密方式同 `ENCRYPTION_KEY`（Fernet）。**admin key 轮转**：与 ENCRYPTION_KEY 解耦，admin key 轮转时需要用现有 ENCRYPTION_KEY 解密后用新 key 重新加密（运维脚本支持）；ENCRYPTION_KEY 轮转时所有 Fernet 加密值（含 JWT/Dify admin key/Dify api keys）需统一批量重加密。

---

## 8. 阶段划分（总 9.5 周）

| 阶段 | 内容 | 估时 |
|---|---|---|
| 4A | Dify API 验证 + env-setup.md/main plan 升 1.17 + Pydantic schema + DifyClient 扩展 | 1.5 周 |
| 4B | 前端画布骨架（@xyflow/react + Tailwind tw- + Zustand + zundo） | 1.5 周 |
| 4C | 5 节点 UI + ConfigDrawer + VariableInspector + 节点 ID 校验 | 2.5 周 |
| 4D | 调试面板（SSE 流式 + 节点轨迹全事件） | 1 周 |
| 4E | DSL 序列化 + validator（含 orphan/cycle/分支汇合）+ save/load + llm-presets 注入 + audit_logs | 1 周 |
| 4E.5 | 乐观锁（If-Match + 409 + ConflictModal）+ localStorage LRU + 草稿恢复 | 1 周 |
| 4F | E2E + Playwright + 文档 | 0.5 周 |
| 4G | **Dify 1.17 适配补丁**（验证发现的问题集中修复） | 0.5 周 |
| **合计** | | **9.5 周** |

---

## 9. 测试策略

**单测**：
- `test_dsl_serializer.py`：toDSL / fromDSL 往返一致性
- `test_dsl_validate.py`：If/Else 单分支、双分支汇合、环、未连通终结点、单节点、空图、悬空节点
- `test_workflow_router.py`：409 冲突路径、权限、audit_logs 写入、If-Match 解析（含/无引号）
- `test_dify_client.py`：mock Console API（import 两步流程 + confirm + 失败 status）
- `test_webhook_isolation.py`：断言 webhook handler 不修改 apps.updated_at / updated_by_user_id
- Vitest：`serialize.test.ts` / `validate.test.ts` / `ConflictModal.test.tsx`

**E2E（Playwright）**：
- 管理员登录 → 进画布 → 拖 5 节点 → start→llm→if-else→answer（双分支汇合）→ 配 LLM 选 for_qa → 保存 → 调试面板跑通 → 员工端可见
- 并发冲突：admin A 保存后 admin B 再保存触发 409，ConflictModal 出现
- 草稿恢复：刷新浏览器 → RestoreDraftModal "本地 <ts> / 服务器 <ts>" → 恢复
- **并发保存压力测试**（后端）：pytest-asyncio 起 2 个 task 同 app 并发 POST /save
  （一个带最新 If-Match、一个带过期），断言 1×200 + 1×409 且 apps.updated_at 是 200 那个 admin 的时间

**手动验证清单**：
- 浏览器刷新 → 草稿恢复 Modal 出现
- 撤销 5 次 → 重做 5 次 → 回到原点
- 保存失败（Pydantic 校验）→ ValidationPanel 标错
- 调试运行 → workflow_finished 事件 + 总 token/步骤数显示
- localStorage 模拟满 → 提示已清理 N 条历史
- Dify 临时下线 → 保存 5xx，前端保留编辑态，可重试
- 双 admin 同 app 并发保存 → 后保存者看到 ConflictModal

---

## 10. 关联文档

- 主设计：`docs/superpowers/specs/2026-08-28-agent-platform-design.md`
- 主实施计划：`docs/superpowers/plans/2026-08-28-mvp-phase-1-infra-auth.md`
- 环境准备：`docs/superpowers/plans/2026-08-28-environment-setup.md`（需 week 1 同步到 1.17）

---

## 11. 待办

- [ ] week 1 day 1：更新 env-setup.md §3.2 + main plan Task 0.2 到 Dify 1.17.0
- [ ] week 1：跑 §6.1 全部 5 项验证
- [ ] week 1：导出 Dify 1.17 真实 DSL 样例，确认 `workflow.environment_variables[]` 结构
- [ ] 二期：APP_ADMIN 按 App 范围隔离
- [ ] 二期：节点版本历史
- [ ] 二期：LLM 预设热加载
- [ ] 运维：Dify apps 清理脚本

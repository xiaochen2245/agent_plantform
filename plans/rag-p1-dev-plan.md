# P1 开发计划（2026-09-03 → 2026-11-30）

- 基线：2026-09-03，距 11-30 约 12.5 周
- 范围口径：P1 = 平台骨架 + 功能4 检索版 + 功能1 规则版 + 功能2 docx+强制人审版
- P2（2026 Q1）= 功能3 全量、扫描件 OCR、长文档一致性全量、功能4 自动关联提醒、细粒度 ACL
- 顾问会裁决前提：四功能 11-30 全量交付在数学上不可行（48 人月压缩进 12.5 周）；本计划即降级口径

## 进度基线：2026-09-04（协作启动点）
### 2026-09-05 会话增量（AI agent abcdqwerxsa，全部合入 develop）

- [x] #27 打标自动化：后端轮询 autotag（fire-and-forget，按钮兜底重试）
- [x] #28 RAG 写操作审计：rag_audit_logs + 7 写端点 + admin 查询 API
- [x] #30 功能① OOXML 规则引擎：stdlib 解析 + R1-R4 确定性规则（backend/app/review/）
- [x] #31 功能① 错别字 LLM 通道：72B 候选+置信度，不自动改
- [x] #32 功能① 审查应用页：/apps/review 双上传→报告→报告问答
- [x] #36 升级回归脚本入库：scripts/ragflow-regress.py 5 项（假引擎冒烟通过）
- [x] #38 ChatSurface 会话化：/api/rag/chat/sessions + persistence prop
- [x] #33 功能② 评分表结构化拆解：ScoringTable + 校验重试（+共享 llm.chat_json 底座）
- [x] #34 功能② 比对流水线：逐项裁决+漏判补全，偏离仅建议
- 测试：后端 168、前端 99（DeptsTab 存量 flake 未动）；共享文件改动均按 COLLAB 报备
- 追加（同日）：#29 方案 A 底座（document_ids 通道+policy 策略缝）、#35 回归 harness（标注格式+匹配+离线自检）
- 真卡点仅剩：#35 人工标注人力（owner）、#37 协作方 agent 配置实例



### 已完成（W1-W3 部分，全部已上线生产 8180）

- [x] RAGFlow v0.27.1 生产部署（20.226，租户隔离 4 路实测）+ SiliconFlow embedding/rerank/chat
- [x] 编排骨架：`backend/app/ragflow/`（client/解析路由槽/deps）+ `/api/rag/*` 网关
- [x] W2 租户绑定：`ragflow_bindings` 表 + 全自动影子账号开通（7 步零 UI）+ 部门级租户路由
- [x] W2 功能④打标管道：LLM 结构化抽取 → metadata → 过滤检索（生产实测：11s 打标、专业过滤命中/排空双向正确）
- [x] W3 门户换脸：四应用首页、知识库应用（全高问答[ChatSurface 组件化，引用来源] + 管理 CRUD）
- [x] Dify 提前退役（owner 决策）、旧对话/旧 kb 页降级、死数据清理
- [x] 生产部署流水线跑通（build-ship + remote-up，两条命令）
- [x] 测试：后端 141、前端 95+（DeptsTab 存量 flake 待修，非本次引入）

### 协作任务分解（GitHub Issues 同步维护，模块所有权隔离冲突）

| Stream | 范围 | 模块所有权（独占文件区） |
|---|---|---|
| A: W3 收尾 | 打标自动化/审计日志/ACL 预过滤 | backend/app/ragflow/* |
| B: 功能① 审查 | OOXML 规则引擎/错别字/审查问答 | backend/app/review/*（新建）、frontend/src/pages/Review* |
| C: 功能② 比对 | 评分表结构化/比对/金标回归 | backend/app/compare/*（新建）、scripts/golden/* |
| D: 基础设施 | 部署流水线/升级回归/协作规范 | deploy/*、scripts/*、.github/* |
| E: 前端通用 | ChatSurface 会话化/多应用复用 | frontend/src/components/* |

冲突约定：`app/main.py`、`app/core/config.py`、`App.tsx` 为共享文件——改动走小 PR，由 Stream D 合并；跨 Stream 接口先开 issue 对齐。

---
## 原始周计划（W1 起，存档）

- [x] RAGFlow v0.27.1 部署（20.226，与 Dify 共存，内存帽调优）+ 4G swap
- [x] 租户隔离验证（4 路越权全拒）、SiliconFlow embedding+rerank 接入（租户 C）
- [x] 9 份真实政府采购招标 PDF 下载并入库解析，评分表还原质量结论（见架构文档 §6）
- [x] 编排服务骨架：`backend/app/ragflow/`（client/parsing 路由槽/deps）+ `/api/rag/*` 路由 + 8 测试（全量 143 通过）
- [x] 架构文档：`docs/superpowers/specs/2026-09-03-rag-platform-architecture.md`
- [x] 版本升级演练 v0.26.4→v0.27.1 + 回归清单跑通

## 周计划

### W2（09-07 ~ 09-13）租户绑定 + 功能4 入库打标
- `ragflow_bindings` 表 + 迁移：平台租户 → RAGFlow 账号/key（替换单 key env）
- 租户 onboarding API（注册→绑 embedding→建库，固化顺序）
- 功能4 打标管道：设计审查单/经验反馈表 → LLM 结构化抽取（项目/专业/问题类型/时间）→ document metadata 写入
- 验收：新租户经 API 全自动开通并完成一次入库+元数据过滤检索

### W3（09-14 ~ 09-20）功能4 检索 + 门户集成
- 元数据过滤检索 API（`metadata_condition` 透传 + 网关侧部门 ACL 预过滤）
- 门户知识库页面接入 `/api/rag/*`
- 审计日志写入（沿用 KbAuditLog 模式）
- 验收：跨项目经验检索 demo（按专业/项目过滤）

### W4（09-21 ~ 09-27）功能1 规则引擎
- python-docx 确定性检查：字体/段落/编号 vs 公司模板（模板以 OOXML 样式基准定义）
- 错别字 LLM 辅助通道（SiliconFlow chat 模型）
- 验收：一份真实文档产出结构化审查报告（问题清单+定位）

### W5-W7（09-28 ~ 10-18）功能2 评分表结构化拆解与比对
- 前置（W4 并行启动）：金标集标注启动——20-30 对 docx 评分表，需领域专家（owner 决策 #6，**W5 开始前必须到位**）
- 评分表 chunks → LLM 结构化（评分项/分值/评审标准 → JSON schema）→ schema 校验 → 失败重试/降级
- 比对流水线：招标评分表 vs 投标响应 → 缺失项/偏离建议 → 人审工作台 API
- 周回归：金标集上评分项级召回 ≥95%（监控线，非交付门槛）；P1 一律不做自动结论
- 验收：10 份真实评分表端到端，输出人审界面可用的比对报告

### W8-W9（10-19 ~ 11-01）功能2 打磨 + 平台收口
- 偏离判定建议（正/负偏离提示）、双解析器交叉校验预研（DeepDoc vs Docling 分歧单元格→人审）
- 门户完整集成、租户配额、错误映射统一回归
- （机会性）功能3 模板填充 MVP：仅当 W8 有余量，不承诺

### W10（11-02 ~ 11-08）内测
- 真实用户试用（≥2 个部门）；金标周回归数据定稿；解析吞吐评估（决定 embedding/OCR 是否提前内部部署）

### W11（11-09 ~ 11-15）修复 + 性能
- 内测问题清零；检索 chunking 调优（表格感知分块）；A1000 内部部署 spike（若 owner 决策 #7 选内部）

### W12（11-16 ~ 11-22）预生产冻结
- 功能冻结只修缺陷；全量回归（143 后端测试 + 5 项 RAGFlow 回归 + 金标集）；运维文档（备份/升级/回滚）

### W13（11-23 ~ 11-30）上线缓冲
- 上线演练 + 演示材料；无新功能无重构

## 门槛与熔断

- **金标集 W5 未到位** → 功能2 降级为"纯人审辅助"（仅拆解展示，无比对建议），不阻塞其他功能
- **评分项召回连续两周 <90%** → 上报 owner 调整口径（P1 承诺改为"拆解+人审"）
- **扫描件需求提前涌入** → 触发 P2 OCR 提前，需 owner 重排 W8-W12
- 所有排期声明以本文基线为准，禁止引用"12 个月"类过期假设

## 依赖的 owner 决策截止点

| 决策 | 截止 | 影响 |
|---|---|---|
| #6 金标集标注人力 | W4 末（09-27） | 功能2 W5 启动 |
| #2/#3 租户定义与承诺口径 | W2 末（09-13） | 租户绑定表设计冻结 |
| #7 embedding 生产形态 | W10（11-08） | W11 部署 spike |
| #5 模型合规 | W8（11-01） | 若强制私有化，W11-W12 加部署窗口 |

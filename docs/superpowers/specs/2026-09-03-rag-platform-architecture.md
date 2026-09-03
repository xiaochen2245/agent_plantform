# 多租户 RAG 平台架构（绞杀者重构）

- 基线日期：2026-09-03（所有排期以此为准）
- 上线目标：2026-11-30（P1 降级口径，见 `plans/rag-p1-dev-plan.md`）
- 状态：架构定稿，P1 开发中
- 关系：`2026-08-28-agent-platform-design.md`（网关/鉴权/审计/门户）仍然有效，本文只覆盖其知识引擎侧的替换；与其冲突处以本文为准

## 1. 背景与结论

原方案基于 Dify。实测限制（plans/dify-knowledge-base.md）：App↔知识库绑定仅控制台可操作、dataset key 权限作用域绑定创建成员、workspace≠租户、开源许可限制多租户托管。顾问会裁决（2026-09-03）：**绞杀者式替换引擎，不做大爆炸重构**——保留 React 门户 + FastAPI 网关，Dify 冻结只读并行至迁移完成，新增自研 RAG 服务。

核心认知：**多租户问题在数据层与网关层解决，不在编排框架层**。LangChain/LangGraph 只是库不是平台；"选 LangChain+LangGraph"实际只覆盖功能 2/3 的编排，平台主体（租户、权限、ingestion、评测、UI）全部自建。

## 2. 总体架构

```
React 门户
   │
FastAPI 网关（已有，扩展）── 鉴权/租户注入/审计/越权映射 404
   ├─→ 自研编排服务（薄，backend/app/ragflow/ 起步）
   │     ├─ 解析器路由槽（parsing.py，按后缀选策略，可替换）
   │     ├─ 功能1 规则引擎（确定性 OOXML 检查，纯 Python）
   │     ├─ 功能2 比对流水线（解析→LLM 结构化→schema 校验→人审）
   │     └─ 功能3 生成-校核双智能体（P2，LangGraph）
   │          │
   │          ├─→ RAGFlow v0.27.1（192.168.20.226:9380）── RAG 引擎
   │          │      DeepDoc 解析 / KB / 混合检索 / rerank / 元数据过滤
   │          └─→ Postgres（业务数据：比对结果/模板/提醒规则/审计，tenant_id+RLS）
   │
   └─→ Dify（**已退役 2026-09-03**：容器移除、keys 注释、旧页面 503 降级，数据卷暂留）
```

外部依赖（测试期）：SiliconFlow API（embedding `BAAI/bge-m3` + rerank `Qwen/Qwen3-Reranker-0.6B`，租户 C 已绑定）。

## 3. 决策记录（ADR）

| # | 决策 | 理由与证据 | 日期 |
|---|---|---|---|
| 1 | 绞杀者替换 Dify，保留网关/门户 → **Dify 已于 2026-09-03 提前退役（owner 决策：不依赖）**：容器已移除、生产 keys 已注释；旧对话页/旧 kb 页进入 503 降级，页面替换随 W3；数据卷暂留作保险，删除需 owner 确认 | 已上线资产不弃置；大爆炸在 12.5 周内不可行 | 09-03 顾问会 / 09-03 退役 |
| 2 | RAGFlow 为 RAG 引擎 | spike 实证：API 全生命周期可驱动（未碰 UI）、账号边界隔离 4 路越权全拒、Apache-2.0、DeepDoc 中文表格还原可用（内容保真高/边界保真中） | 09-03 spike |
| 3 | 编排自研但保持薄层 | 四功能的"工作流"主体是确定性规则/批量比对/双智能体循环，需可测试可回归；可视化画布（Dify/RAGFlow Canvas）同一抽象级别，已有教训 |
| 4 | LangGraph 仅功能 2/3，LangChain 全家桶不引入 | 库≠平台；锁版本防 churn | 09-03 顾问会 |
| 5 | 功能 1 格式检查用确定性 OOXML 规则（python-docx），LLM 只做错别字/语义辅助 | LLM 做主逻辑有准确率上限且不可审计 | 09-03 顾问会 |
| 6 | 解析器路由槽（`parsing.py`）| 解析器是可替换策略：DeepDoc 默认，Docling/opendataloader/MinerU/VLM 按文档类型路由；RAGFlow v0.27 亦内建 MinerU/OpenDataLoader provider | 09-03 |
| 7 | 租户模型：per-tenant RAGFlow 账号 + API key，网关唯一入口 | 实测账号边界是硬隔离；RAGFlow 自带 team 概念不采用（与平台用户模型冲突） | 09-03 spike |
| 8 | 文档级 ACL 双通道：`document_ids` 白名单（授权）+ `metadata_condition`（部门/专业维度） | RAGFlow 权限仅 me/team 两档；ACL 主权留在网关 | 09-03 |
| 9 | embedding 三阶段：TEI bge-small（冒烟）→ SiliconFlow bge-m3（测试，当前）→ 内部部署（A1000 8GB：Qwen3-Embedding-2B+Reranker-2B 常驻 int8 + DeepSeek-OCR-2 按需 int4，三者 FP16 装不下） | A/B 实测：只换 embedding 提升有限，rerank 才是质变杠杆 | 09-03 |
| 10 | RAGFlow 版本策略：锁 minor（当前 v0.27.1），升级前对照 release notes 过滤与四功能相关条目，升级后跑 5 项回归（健康/数据存活/provider 绑定/隔离/检索，脚本 `/tmp/regress.py` 待入库） | 官方文档超前于代码（如 Transformer/Indexer 组件未发布），勿被文档驱动升级 | 09-03 |
| 11 | 检索质量瓶颈排序：rerank（已解决）> chunking（表格感知分块，下一个）> embedding | A/B 实验结论 | 09-03 |

## 4. 租户与权限

- **租户开通顺序（硬约束）**：注册 RAGFlow 账号 → 绑默认 embedding → 建 dataset。顺序颠倒 = 库带空 embedding、检索静默失败（已踩坑，脚本已固化顺序）。
- 平台用户不出现在 RAGFlow；所有流量经网关按 `平台租户 → RAGFlow key` 映射转发。
- 越权响应（`You don't own...` / `lacks permission`）在网关统一映射 404，不泄露资源存在性。

## 5. 部署拓扑（192.168.20.226，8C32G 实为 15G RAM + RTX A1000 8GB 空闲）

- compose 项目 `ragflow`（与 Dify 的 `docker-*` 隔离）：ragflow-cpu(4G 帽) / es01(512m 堆) / mysql(2G 帽) / minio / valkey / tei-cpu(1.75G 帽，bge-small-en 模型在 `/opt/ragflow-spike/tei-models`)
- 端口：Web 8380 / API 9380 / TEI 6380 / MySQL 5455（**`MYSQL_PORT` 双用途坑**：应用容器内连接也读它，已用服务级 env 固定 3306）
- 配置：`/opt/ragflow-spike/docker/`（.env 补丁 + docker-compose.override.yml 内存帽/TEI 挂载/HF_ENDPOINT=hf-mirror）
- `/swapfile-spike` 4G 兜底；已停待恢复：`docker start ap-frontend ap-backend ap-postgres node1 node2 node3 slurmctld slurmdbd`
- 密钥：SiliconFlow key 在 RAGFlow 租户 C 配置内（库内加密），**转生产前轮换**；仓库不存任何 LLM key

## 6. 已验证事实（spike 存档）

1. RAGFlow 与 Dify 生产共存（内存帽约束下）✅
2. 租户隔离 4 路越权全拒（v0.26.4 与 v0.27.1 均测）✅
3. 9 份真实政府采购招标 PDF 经自研网关入库解析（DeepDoc CPU 4-10 秒/页）✅
4. 评分表还原：数字/公式保真高（`报价得分＝报价分值×（评标基准价/评审价）`原样保留），合并单元格行列对齐漂移 → 证实"DeepDoc 到 80%，LLM 结构化补 20%"路线
5. rerank 质变：评分表类问题 Top-1 命中率显著提升 ✅
6. v0.26.4→v0.27.1 升级：数据卷/provider 绑定/隔离/检索全存活 ✅

## 7. 开放决策（owner）

1. 团队规模与构成（一切时间线结论的前提）
2. 租户定义：内部部门 vs 外部法人客户（决定隔离强度；三判据见顾问会备忘录）
3. 11-30 对外承诺口径（P1 降级版 vs 全量——全量数学上不可行）
4. 源文档格式范围（docx only vs 含扫描 PDF）
5. 模型合规：政府材料是否强制私有化/国产模型
6. 金标集标注人力（投标领域专家 20-30 对，约 5-6 人周）
7. embedding 生产部署形态（外部 API 长期 vs A1000 内部部署 vs GPU 服务器）

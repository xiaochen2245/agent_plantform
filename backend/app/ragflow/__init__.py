"""RAGFlow 引擎接入（绞杀者替换 Dify 知识库一侧）。

- client：RAGFlow v0.26 RESTful API（Bearer API-Key）瘦客户端，transport 可注入
- parsing：解析器路由槽（策略名 → 实现），当前唯一策略 ragflow-deepdoc
- router：/api/rag/* 网关代理，权限与 kb 路由同立场（读=登录用户，写=PLATFORM_ADMIN）
- 租户映射：暂为单 key（RAGFLOW_API_KEY env，同 DIFY_DATASET_API_KEY 先例）；
  多租户绑定表随 onboarding 切片落地——网关注入租户时换 per-tenant key 查表。
"""

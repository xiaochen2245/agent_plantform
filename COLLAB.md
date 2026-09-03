# 协作开发规范（P1 · 2026-11-30）

任务板 = GitHub Issues（`stream-*` 标签即看板列）+ milestone `P1-2026-11-30`。
计划与进度：`plans/rag-p1-dev-plan.md`（每周更新）。

## 任务认领

1. 从 Issues 认领：自己 assign + 评论 `认领`（避免两人同抓）
2. 分支命名：`<stream>/<issue号>-短描述`，如 `b/30-ooxml-rules`
3. 完成定义（DoD）：验收标准达成 + 测试过 + 小 PR（<400 行优先）
4. PR 标题带 issue 号（`close #30`），Stream D 负责合并共享文件 PR

## 冲突隔离（模块所有权）

| Stream | 独占区 | 共享文件（走小 PR） |
|---|---|---|
| A | `backend/app/ragflow/**` | `app/main.py`、`app/core/config.py`、 |
| B | `backend/app/review/**`、`frontend/src/pages/Review*` | `frontend/src/App.tsx`、 |
| C | `backend/app/compare/**`、`scripts/golden/**` | `backend/pyproject.toml`、 |
| D | `deploy/**`、`scripts/**`、`.github/**`、`COLLAB.md` | `.env.example` |
| E | `frontend/src/components/**` | |

共享文件改动：改前在 issue 里说明意图；由 Stream D 合并。

## AI Agent 接入（GitHub MCP）

双方向各自的 agent 宿主配置官方 GitHub MCP server，即可让 agent 读写 issue/PR/看板：

```json
{
  "mcpServers": {
    "github": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
               "-e", "GITHUB_PERSONAL_ACCESS_TOKEN",
               "ghcr.io/github/github-mcp-server"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "<PAT，scope: repo + project>" }
    }
  }
}
```

- PAT 创建：GitHub → Settings → Developer settings → Personal access tokens（勾 `repo`、`project`、`read:org`）
- Agent 常用动作：`list_issues`（拉实时任务）、`update_issue`（评论进度/改状态）、`create_pull_request`
- 约定：agent 更新进度时评论格式 `进度: <百分比/状态> —— <一句话>`，人来判断是否 close

## 环境与部署

- 生产：192.168.20.226（RAGFlow:9380 / 门户:8180 / RAGFlow面板:8380）
- 部署 = `deploy/scripts/build-ship.sh` + `remote-up.sh`（见 `deploy/DEPLOY.md`）
- 密钥不入库：`.env` 只存在于服务器；本地 `backend/.env` 各自维护（模板 `.env.example`）
- RAGFlow 升级：先跑 `scripts/ragflow-regress.py`（D1 交付），对照 release notes

## 卡点升级路径

任务被卡 → issue 打 `blocked-owner` 标签 + 评论说明需要谁拍板什么 → 每周同步给 owner。
当前 owner 待决：金标集标注人力（#35，09-27 前）、租户定义口径、embedding 生产形态。

# E2E（Playwright）

对话门户的端到端测试，跑 **mock 模式**（`VITE_USE_MOCKS` 默认开启）——自包含，不依赖后端与 Dify。

## 运行

```bash
cd e2e
npm install                        # .npmrc 已设 playwright_skip_browser_download=1
npx playwright test                # 系统需有 Google Chrome（channel:'chrome'）
```

- 自动拉起 `../frontend` 的 vite dev（5173，`reuseExistingServer:true`，已有实例则复用）
- 超时预算：单用例 30s / 全局 120s / 断言 10s（guard 冷启动链路单独放宽到 15s）
- 若 5173 被僵尸进程占用（IPv6-only 监听、webServer 误判复用），先 `fuser -k 5173/tcp`

## 覆盖路径（对应设计文档 §11 E2E 清单）

| 文件 | 用例 |
|---|---|
| auth.spec.ts | 登录成功跳首页 · 错误密码停留+报错 · 非管理员访问 /admin 被守卫拦截 |
| chat.spec.ts | 选 Agent → Enter 发送 → 流式回复 · query 含「失败」→ 错误卡+重试 |
| history.spec.ts | 历史列表 → 回放（契约 v2 messages 端点）→ 继续对话跳回 |

## 选择器策略

- 优先 `getByRole` / `getByLabel` / `getByText`（可访问性语义）
- antd Select 点击 `.ant-select` 容器（placeholder span 会随 options 异步加载重挂）
- mock 种子账号：`admin@company.com / admin123`（管理员）、`user@company.com / user123`（普通员工）

## 已知产品缺口（e2e 已适配并上报，待父会话排期）

**冷启动无会话恢复**：`fetchMe` 无任何调用方，整页刷新后 `RequireAuth` 将 idle 判为未登录弹回 `/login`（真实后端模式同样如此）。守卫用例按现状验证"进不到 /admin 内容"；补上 boot fetchMe 后可把断言收紧为"落回 /"。

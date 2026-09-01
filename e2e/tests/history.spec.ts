import { expect } from "@playwright/test";
import { test, loginToChat } from "../fixtures";

/**
 * 历史会话闭环（契约 v2）：列表 → 只读回放（真实 messages 端点 mock）→ 继续对话跳回工作台。
 */

test("历史列表 → 回放消息 → 继续对话跳回", async ({ page }) => {
  await loginToChat(page);
  // 侧栏进入历史会话
  await page.getByRole("link", { name: "历史会话" }).click();
  await expect(page).toHaveURL(/\/history$/);
  // 种子会话（mock conversations）
  const item = page.getByText("VPN 连接失败怎么办", { exact: false }).first();
  await item.click();
  // 回放内容来自 /api/conversations/{id}/messages mock（契约 v2）
  await expect(page.getByText("排查建议", { exact: false }).first()).toBeVisible({ timeout: 10_000 });
  // 继续对话 → 回到对话工作台
  await page.getByRole("button", { name: "继续对话" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("button", { name: "新对话" })).toBeVisible();
});

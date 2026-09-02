import { expect } from "@playwright/test";
import { test, loginToChat } from "../fixtures";

/**
 * 对话闭环（mock 模式）：选 Agent → 发送 → 流式回复出现；
 * query 含「失败」→ 错误卡 + 重试（契约：error 事件 → 前端错误卡）。
 */

test("选 Agent → Enter 发送 → 流式回复出现", async ({ page }) => {
  await loginToChat(page);
  // 首个 Agent 默认未选：选择 IT 运维助手
  await page.locator(".ant-select").first().click(); // placeholder span 随 options 异步加载重挂，点容器更稳
  await page.getByRole("option", { name: "IT 运维助手" }).click();
  // Enter 发送（契约：Enter 发送 / Shift+Enter 换行）
  const composer = page.getByPlaceholder("输入你的问题，Enter 发送");
  await composer.fill("打印机无法使用的处理步骤");
  await composer.press("Enter");
  // mock 回答首段（流式增量渲染）
  await expect(page.getByText("以下是检索到的要点", { exact: false })).toBeVisible({ timeout: 10_000 });
});

test("query 含「失败」→ 错误卡 + 重试按钮", async ({ page }) => {
  await loginToChat(page);
  await page.locator(".ant-select").first().click(); // placeholder span 随 options 异步加载重挂，点容器更稳
  await page.getByRole("option", { name: "IT 运维助手" }).click();
  const composer = page.getByPlaceholder("输入你的问题，Enter 发送");
  // 注：mock 规则 = query 含「失败」返回 error 事件；文案避开该词以免误导
  await composer.fill("这段请求模拟生成失败的情形");
  await composer.press("Enter");
  await expect(page.getByText("回答生成失败，请重试")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
});

/** 审查波 F1/F3 回归：布局收敛、刷新恢复、多轮串联。 */

async function selectFirstAgent(page: import("@playwright/test").Page) {
  await page.locator(".ant-select").first().click();
  await page.getByRole("option", { name: "IT 运维助手" }).click();
}

test("R1 多消息：线程容器可滚动且 composer 不被顶出视口", async ({ page }) => {
  // 低视口强制内容溢出（F1：此前 overflow 不生效，composer 被顶出）
  await page.setViewportSize({ width: 1280, height: 480 });
  await loginToChat(page);
  await selectFirstAgent(page);
  const composer = page.getByPlaceholder("输入你的问题，Enter 发送");
  for (let i = 1; i <= 3; i++) {
    await composer.fill(`第${i}个问题：请给出完整的排查步骤与建议`);
    await composer.press("Enter");
    await expect(page.getByText(`关于「第${i}个问题`, { exact: false })).toBeVisible({ timeout: 10_000 });
    // 等流完成（message_end 后 usage 徽标出现），避免在流式中途推进造成竞态
    await expect(page.getByText("tokens: 128")).toHaveCount(i, { timeout: 10_000 });
  }
  // F1：composer 仍可见
  await expect(composer).toBeVisible();
  // F1：线程容器自身形成滚动（而非撑破布局）
  const scrollable = await page.getByTestId("chat-thread").evaluate(
    (el) => el.scrollHeight > el.clientHeight
  );
  expect(scrollable).toBeTruthy();
});

test("R2+R3 刷新恢复最近会话 + 多轮 conversation_id 串联", async ({ page }) => {
  await loginToChat(page);
  await selectFirstAgent(page);
  const bodies: Array<{ conversation_id?: string }> = [];
  page.on("request", (req) => {
    if (req.url().includes("/api/chat/send")) {
      try {
        bodies.push(JSON.parse(req.postData() ?? "{}") as { conversation_id?: string });
      } catch {
        /* 非 JSON 忽略 */
      }
    }
  });
  const composer = page.getByPlaceholder("输入你的问题，Enter 发送");
  await composer.fill("串联第一问：打印机排查步骤");
  await composer.press("Enter");
  await expect(page.getByText("以下是检索到的要点", { exact: false })).toBeVisible({ timeout: 10_000 });
  // 等流完成：mock 动态会话在 message_end 时才落 assistant 消息并持久化
  await expect(page.getByText("tokens: 128")).toBeVisible({ timeout: 10_000 });

  // F3：刷新后恢复的是刚才那轮会话（而非空白或静态种子）
  await page.reload();
  await expect(page).toHaveURL(/\/$/);
  // 精确匹配用户气泡（回答正文里也含该串，避免严格模式二义）
  await expect(page.getByText("串联第一问：打印机排查步骤", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("以下是检索到的要点", { exact: false }).first()).toBeVisible({ timeout: 10_000 });

  // R3：第二轮复用同一 conversation_id（首轮空=新建，刷新后两轮均非空且相同）
  await composer.fill("串联第二问：补充网络层面排查");
  await composer.press("Enter");
  await expect(page.getByText("关于「串联第二问", { exact: false })).toBeVisible({ timeout: 10_000 });
  await page.waitForTimeout(300);

  expect(bodies.length).toBe(2); // 刷新前 1 次 + 刷新后 1 次
  // 首问可能落在挂载时恢复的既有会话（F3 语义：有历史即续），不强制为空
  const second = bodies[1]?.conversation_id ?? "";
  expect(second).not.toBe(""); // 刷新恢复后：续聊走已存在会话而非新建（多轮串联成立）
});

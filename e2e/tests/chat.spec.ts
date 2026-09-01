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

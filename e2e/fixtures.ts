import { test as base, expect, type Page } from "@playwright/test";

/** 会话级扩展点（当前无需自定义 fixture，保留以统一导入口）。 */
export const test = base.extend({});
export { expect };

/** 登录并进入对话工作台。 */
export async function loginToChat(page: Page, email = "admin@company.com", password = "admin123") {
  await page.goto("/login");
  await page.getByLabel("企业邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登 录" }).click();
  await expect(page).toHaveURL(/\/$/);
}

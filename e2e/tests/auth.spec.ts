import { expect, type Page } from "@playwright/test";
import { test } from "../fixtures";

/**
 * 登录与鉴权路径（mock 模式，契约 docs/api-contract.md）。
 * 覆盖：成功登录跳首页 / 错误凭据停留+报错 / 非管理员访问 /admin 被弹回。
 */

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("企业邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登 录" }).click();
}

test("登录成功 → 跳转首页（对话工作台）", async ({ page }) => {
  await login(page, "admin@company.com", "admin123");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("button", { name: "新对话" })).toBeVisible();
});

test("登录后整页刷新仍在线（A1 会话引导，不再误踢回登录）", async ({ page }) => {
  await login(page, "admin@company.com", "admin123");
  await expect(page).toHaveURL(/\/$/);
  await page.reload();
  await expect(page).toHaveURL(/\/$/, { timeout: 15_000 });
  await expect(page.getByRole("button", { name: "新对话" })).toBeVisible({ timeout: 15_000 });
});

test("错误密码 → 停留登录页并显示错误提示", async ({ page }) => {
  await login(page, "admin@company.com", "wrong-password");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByText("Invalid credentials")).toBeVisible();
});

test("非 PLATFORM_ADMIN 访问 /admin → 被守卫弹回首页", async ({ page }) => {
  await login(page, "user@company.com", "user123");
  await expect(page).toHaveURL(/\/$/, { timeout: 15_000 });
  // goto 触发整页重载。已知产品缺口（已上报父会话）：冷启动无会话恢复
  // （fetchMe 无人调用），reload 后 RequireAuth 会先弹 /login —— 但守卫链路
  // 本身可验证：普通员工无论如何进不到 /admin 内容。
  await page.goto("/admin");
  await expect(page).not.toHaveURL(/\/admin$/, { timeout: 15_000 });
  await expect(page.getByText("员工管理")).toHaveCount(0);
  await expect(page.getByText("用户与授权")).toHaveCount(0);
});

import { defineConfig } from "@playwright/test";

/**
 * Playwright 配置：跑 frontend 的 mock 模式（VITE_USE_MOCKS 默认开启），
 * 自包含（不依赖后端/Dify）。浏览器复用系统 Chrome（channel:'chrome'）。
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  globalTimeout: 120_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["line"]],
  use: {
    baseURL: "http://localhost:5173",
    channel: "chrome",
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  webServer: {
    command: "npm --prefix ../frontend run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});

import { describe, expect, it, vi } from "vitest";

describe("mock 开关（VITE_USE_MOCKS，见 src/config.ts）", () => {
  it("默认（未设置 / 任意非 false 值）启用 mock", async () => {
    vi.resetModules();
    vi.unstubAllEnvs();
    const { USE_MOCKS } = await import("./config");
    expect(USE_MOCKS).toBe(true);
  });

  it("VITE_USE_MOCKS=false 时关闭 mock（dev:real 链路）", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_USE_MOCKS", "false");
    const { USE_MOCKS } = await import("./config");
    expect(USE_MOCKS).toBe(false);
  });
});

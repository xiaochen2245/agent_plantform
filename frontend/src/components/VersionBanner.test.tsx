import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import VersionBanner from "./VersionBanner";
import * as version from "../version";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("isStale 比对语义", () => {
  it("两端均为真实版本且一致 → 不陈旧", () => {
    expect(version.isStale("abc1234", "abc1234")).toBe(false);
  });
  it("两端均为真实版本且不同 → 陈旧", () => {
    expect(version.isStale("xyz9999", "abc1234")).toBe(true);
  });
  it("任一端为 'dev'（未注入）或取不到 → 不陈旧（本地 dev 不误报）", () => {
    expect(version.isStale("dev", "abc1234")).toBe(false);
    expect(version.isStale("abc1234", "dev")).toBe(false);
    expect(version.isStale(null, "abc1234")).toBe(false);
    expect(version.isStale("", "abc1234")).toBe(false);
  });
});

describe("fetchServerVersion", () => {
  it("正常返回版本字符串", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ version: "abc1234" }), { status: 200 }))
    );
    expect(await version.fetchServerVersion()).toBe("abc1234");
    vi.unstubAllGlobals();
  });
  it("网络失败 → null（静默不干扰）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      })
    );
    expect(await version.fetchServerVersion()).toBeNull();
    vi.unstubAllGlobals();
  });
  it("非 200 或空版本 → null", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 500 })));
    expect(await version.fetchServerVersion()).toBeNull();
    vi.unstubAllGlobals();
  });
});

describe("VersionBanner 组件", () => {
  it("版本不陈旧 → 不渲染提示条", async () => {
    vi.spyOn(version, "fetchServerVersion").mockResolvedValue("same001");
    vi.spyOn(version, "isStale").mockReturnValue(false);
    render(<VersionBanner />);
    await waitFor(() => expect(version.fetchServerVersion).toHaveBeenCalled());
    expect(screen.queryByTestId("version-banner")).toBeNull();
  });

  it("版本陈旧 → 提示条出现，点击「立即刷新」触发 reload（不自动强刷）", async () => {
    vi.spyOn(version, "fetchServerVersion").mockResolvedValue("server99");
    vi.spyOn(version, "isStale").mockReturnValue(true);
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...window.location, reload },
      writable: true,
    });
    render(<VersionBanner />);
    expect(await screen.findByTestId("version-banner")).toBeTruthy();
    expect(screen.getByText("系统已更新")).toBeTruthy();
    fireEvent.click(screen.getByText("立即刷新"));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

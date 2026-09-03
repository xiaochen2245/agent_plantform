import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeAll, afterAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { handlers } from "./mocks/handlers";
import { http } from "./api/http";
import App from "./App";
import { useAuthStore } from "./stores/auth";
import type { MeInfo } from "./types";

const server = setupServer(...handlers);
http.defaults.baseURL = "http://localhost/api";

function setLocation(me: MeInfo) {
  useAuthStore.setState({ me, status: "authenticated" });
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  useAuthStore.setState({ me: null, status: "idle" });
  cleanup();
});
afterAll(() => server.close());

const USER: MeInfo = { id: 2, email: "u@company.com", name: "李霞", roles: ["USER"], dept_id: 1 };
const ADMIN: MeInfo = { id: 1, email: "admin@company.com", name: "张明", roles: ["USER", "PLATFORM_ADMIN"], dept_id: null };

describe("路由守卫与侧栏权限渲染", () => {
  it("非 PLATFORM_ADMIN 访问 /admin 被重定向回 /", async () => {
    setLocation(USER);
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <LocationProbe />
        <App />
      </MemoryRouter>
    );
    // 重定向回首页：应用入口页出现，管理表格不出现
    expect(await screen.findByText("企业知识平台")).toBeTruthy();
    expect(screen.queryByText("权限管理")).toBeNull();
  });

  it("PLATFORM_ADMIN 可见侧栏「权限管理」入口并进入 /admin", () => {
    setLocation(ADMIN);
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <LocationProbe />
        <App />
      </MemoryRouter>
    );
    expect(screen.getByTestId("location").textContent).toBe("/admin");
    // 侧栏入口 + 页面标题都在（同名两处）
    expect(screen.getAllByText("权限管理").length).toBeGreaterThanOrEqual(2);
  });

  it("普通员工侧栏不渲染「管 理」分区", () => {
    setLocation(USER);
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );
    expect(screen.queryByText("管 理")).toBeNull();
    expect(screen.getByText("应 用")).toBeTruthy();
  });

  it("四大应用占位路由渲染 ComingSoon（文档审查）", () => {
    setLocation(USER);
    render(
      <MemoryRouter initialEntries={["/apps/review"]}>
        <App />
      </MemoryRouter>
    );
    expect(screen.getByText("文档审查")).toBeTruthy();
    expect(screen.getByText(/W4 切片/)).toBeTruthy();
  });
});

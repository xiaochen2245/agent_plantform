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
    // 重定向回首页：聊天空态出现（等 apps 加载完），管理表格不出现
    expect(await screen.findByText(/提出你的第一个问题|选择上方 Agent/)).toBeTruthy();
    expect(screen.queryByText("用户与授权")).toBeNull();
  });

  it("PLATFORM_ADMIN 可见侧栏「用户与授权」入口并进入 /admin", () => {
    setLocation(ADMIN);
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <LocationProbe />
        <App />
      </MemoryRouter>
    );
    expect(screen.getByTestId("location").textContent).toBe("/admin");
    // 侧栏入口 + 页面标题都在（同名两处）
    expect(screen.getAllByText("用户与授权").length).toBeGreaterThanOrEqual(2);
  });

  it("普通员工侧栏不渲染「管 理」分区", () => {
    setLocation(USER);
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );
    expect(screen.queryByText("管 理")).toBeNull();
    expect(screen.getByText("编 辑")).toBeTruthy();
    expect(screen.getByText("员 工 端")).toBeTruthy();
  });

  it("编辑端占位路由渲染 ComingSoon", () => {
    setLocation(USER);
    render(
      <MemoryRouter initialEntries={["/workflows"]}>
        <App />
      </MemoryRouter>
    );
    // 侧栏项 + 页面标题同名（两处），描述文案唯一
    expect(screen.getAllByText("工作流编排").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/节点画布/)).toBeTruthy();
  });
});

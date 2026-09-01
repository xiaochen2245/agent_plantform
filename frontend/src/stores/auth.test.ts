import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { http as mswHttp, HttpResponse } from "msw";
import { useAuthStore } from "./auth";
import { http } from "../api/http";
import { handlers } from "../mocks/handlers";
import type { MeInfo } from "../types";

const server = setupServer(...handlers);

// node 环境无页面 origin：给 axios 绝对 baseURL（msw/node 相对路径按 http://localhost 解析）
http.defaults.baseURL = "http://localhost/api";

const ME: MeInfo = {
  id: 1,
  email: "admin@company.com",
  name: "张明",
  roles: ["USER", "PLATFORM_ADMIN"],
  dept_id: null,
};

const meOk = () => server.use(mswHttp.get("/api/auth/me", () => HttpResponse.json(ME)));
const me401 = () =>
  server.use(
    mswHttp.get("/api/auth/me", () => HttpResponse.json({ detail: "Not authenticated" }, { status: 401 }))
  );

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  useAuthStore.setState({ me: null, status: "idle" });
});
afterAll(() => server.close());

describe("auth store（契约：/api/auth/*）", () => {
  it("正确凭据登录成功 → authenticated + me", async () => {
    meOk(); // node 下不模拟 cookie 持久化，直接给定已认证的 /auth/me
    await useAuthStore.getState().login("admin@company.com", "admin123");
    const s = useAuthStore.getState();
    expect(s.status).toBe("authenticated");
    expect(s.me?.email).toBe("admin@company.com");
    expect(s.me?.roles).toContain("PLATFORM_ADMIN");
  });

  it("错误凭据 → 抛错且状态为 anonymous", async () => {
    await expect(useAuthStore.getState().login("admin@company.com", "wrong")).rejects.toThrow();
    expect(useAuthStore.getState().status).toBe("anonymous");
  });

  it("fetchMe：200 返回用户并置 authenticated", async () => {
    meOk();
    const me = await useAuthStore.getState().fetchMe();
    expect(me?.name).toBe("张明");
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("fetchMe：401 返回 null 并置 anonymous", async () => {
    me401();
    const me = await useAuthStore.getState().fetchMe();
    expect(me).toBeNull();
    expect(useAuthStore.getState().me).toBeNull();
    expect(useAuthStore.getState().status).toBe("anonymous");
  });

  it("logout 后状态复位", async () => {
    meOk();
    server.use(mswHttp.post("/api/auth/logout", () => new HttpResponse(null, { status: 200 })));
    await useAuthStore.getState().login("admin@company.com", "admin123");
    await useAuthStore.getState().logout();
    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(useAuthStore.getState().me).toBeNull();
  });
});

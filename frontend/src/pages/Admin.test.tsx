import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import { setupServer } from "msw/node";
import { http as mswHttp, HttpResponse } from "msw";
import { handlers } from "../mocks/handlers";
import { http } from "../api/http";
import { useChatStore } from "../stores/chat";
import Admin from "./Admin";

/**
 * admin 端点（契约 v2）已由真实后端提供，公共 mock 不再覆盖；
 * 本测试内自建 handlers 模拟后端行为验证交互链路。
 */

interface TestUser {
  id: number;
  name: string;
  email: string;
  dept: string | null;
  roles: string[];
  status: number;
  created_at: string;
}

const NOW = "2026-09-01T00:00:00Z";
let USERS: TestUser[] = [
  { id: 1, name: "张明", email: "admin@company.com", dept: "信息技术部", roles: ["USER", "PLATFORM_ADMIN"], status: 1, created_at: NOW },
  { id: 2, name: "李霞", email: "lixia@company.com", dept: "财务部", roles: ["USER"], status: 1, created_at: NOW },
  { id: 3, name: "陈晨", email: "chenchen@company.com", dept: null, roles: ["USER"], status: 0, created_at: NOW },
];
let USER_APPS: Record<number, number[]> = { 1: [1, 2, 3], 2: [2] };

function resetFixtures() {
  USERS = [
    { id: 1, name: "张明", email: "admin@company.com", dept: "信息技术部", roles: ["USER", "PLATFORM_ADMIN"], status: 1, created_at: NOW },
    { id: 2, name: "李霞", email: "lixia@company.com", dept: "财务部", roles: ["USER"], status: 1, created_at: NOW },
    { id: 3, name: "陈晨", email: "chenchen@company.com", dept: null, roles: ["USER"], status: 0, created_at: NOW },
  ];
  USER_APPS = { 1: [1, 2, 3], 2: [2] };
}

const listCalls: Array<{ query: string | null; status: string | null; page: string | null }> = [];
const patchBodies: Array<{ id: string; body: Record<string, unknown> }> = [];
const createBodies: Array<Record<string, unknown>> = [];
const putAppBodies: Array<{ id: string; appIds: number[] }> = [];

const server = setupServer(
  ...handlers,
  mswHttp.get("http://localhost/api/admin/users", ({ request }) => {
    const url = new URL(request.url);
    const q = url.searchParams.get("query");
    const st = url.searchParams.get("status");
    const page = url.searchParams.get("page") ?? "1";
    listCalls.push({ query: q, status: st, page });
    let items = USERS;
    if (q) items = items.filter((u) => u.name.includes(q) || u.email.includes(q));
    if (st) items = items.filter((u) => String(u.status) === st);
    return HttpResponse.json({ total: items.length, items });
  }),
  mswHttp.patch("http://localhost/api/admin/users/:id", async ({ request, params }) => {
    const body = (await request.json()) as Record<string, unknown>;
    patchBodies.push({ id: String(params.id), body });
    const user = USERS.find((u) => u.id === Number(params.id));
    if (!user) return HttpResponse.json({ detail: "Not found" }, { status: 404 });
    Object.assign(user, body);
    return HttpResponse.json(user);
  }),
  mswHttp.post("http://localhost/api/admin/users", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    createBodies.push(body);
    const created: TestUser = {
      id: USERS.length + 1,
      name: String(body.name),
      email: String(body.email),
      dept: null,
      roles: (body.roles as string[]) ?? ["USER"],
      status: 1,
      created_at: NOW,
    };
    USERS.push(created);
    return HttpResponse.json(created, { status: 201 });
  }),
  mswHttp.post("http://localhost/api/admin/users/:id/reset_password", ({ params }) => {
    const user = USERS.find((u) => u.id === Number(params.id));
    if (!user) return HttpResponse.json({ detail: "Not found" }, { status: 404 });
    return HttpResponse.json({ password: "Rand0mPx8" });
  }),
  mswHttp.get("http://localhost/api/admin/users/:id/apps", ({ params }) => {
    const ids = USER_APPS[Number(params.id)] ?? [];
    return HttpResponse.json({ app_ids: ids });
  }),
  mswHttp.put("http://localhost/api/admin/users/:id/apps", async ({ request, params }) => {
    const body = (await request.json()) as { app_ids?: number[] };
    putAppBodies.push({ id: String(params.id), appIds: body.app_ids ?? [] });
    USER_APPS[Number(params.id)] = body.app_ids ?? [];
    return HttpResponse.json({ ok: true });
  })
);

http.defaults.baseURL = "http://localhost/api";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  resetFixtures();
  listCalls.length = 0;
  patchBodies.length = 0;
  createBodies.length = 0;
  putAppBodies.length = 0;
});
afterEach(() => {
  server.resetHandlers();
  cleanup();
  vi.restoreAllMocks();
});
afterAll(() => server.close());

function seedApps() {
  useChatStore.setState({
    apps: [
      { id: 1, name: "IT 运维助手", description: "解答服务器、网络与账号问题", mode: "chat" },
      { id: 2, name: "报销政策问答", description: "差旅与报销规则查询", mode: "chat" },
    ],
  });
}

describe("管理后台（契约 v2 真实端点交互）", () => {
  it("列表分页加载：携带 page/page_size，渲染用户与总数字段，空部门显示 —", async () => {
    seedApps();
    render(
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    );

    await waitFor(() => screen.getByText("张明"));
    expect(screen.getByText("lixia@company.com")).toBeTruthy();
    expect(screen.getByText("员工总数 3")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy(); // 陈晨 dept=null
    expect(listCalls[0]).toMatchObject({ query: null, status: null, page: "1" });

    // 状态列：禁用用户显示「○ 禁用」
    expect(screen.getByText("○ 禁用")).toBeTruthy();
  });

  it("搜索与状态筛选：query/status 透传给列表端点", async () => {
    seedApps();
    render(
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    );
    await screen.findByText("张明");

    fireEvent.change(screen.getByPlaceholderText("搜索姓名或邮箱"), { target: { value: "李霞" } });
    fireEvent.keyDown(screen.getByPlaceholderText("搜索姓名或邮箱"), { key: "Enter" });
    await waitFor(() => {
      expect(listCalls.some((c) => c.query === "李霞")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("启用"));
    await waitFor(() => {
      expect(listCalls.some((c) => c.status === "1")).toBeTruthy();
    });
  });

  it("状态开关：点击禁用触发 PATCH {status:0} 并用响应更新行", async () => {
    seedApps();
    render(
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    );
    await screen.findByText("张明");

    // 两行启用（张明/李霞），点击首行（张明）
    fireEvent.click(screen.getAllByText("● 启用")[0]);
    await waitFor(() => {
      expect(patchBodies).toHaveLength(1);
    });
    expect(patchBodies[0]).toEqual({ id: "1", body: { status: 0 } });
    // 张明变禁用 → 全表共两行禁用（张明 + 陈晨）
    await waitFor(() => {
      expect(screen.getAllByText("○ 禁用")).toHaveLength(2);
    });
  });

  it("新建用户：表单提交 POST，成功后列表刷新", async () => {
    seedApps();
    render(
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    );
    await screen.findByText("张明");

    fireEvent.click(screen.getByText("添加用户"));
    fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "王强" } });
    fireEvent.change(screen.getByLabelText("企业邮箱"), { target: { value: "wangqiang@company.com" } });
    fireEvent.change(screen.getByLabelText("初始密码"), { target: { value: "init123456" } });
    fireEvent.click(screen.getByText("创 建"));

    await waitFor(() => {
      expect(createBodies).toHaveLength(1);
    });
    expect(createBodies[0]).toMatchObject({ name: "王强", email: "wangqiang@company.com", password: "init123456" });
    await waitFor(() => screen.getByText("wangqiang@company.com"));
    expect(screen.getByText("员工总数 4")).toBeTruthy();
  });

  it("重置密码：展示后端返回的新密码与复制入口", async () => {
    seedApps();
    render(
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    );
    await screen.findByText("张明");

    fireEvent.click(screen.getByLabelText("操作-张明"));
    fireEvent.click(await screen.findByText("重置密码"));
    fireEvent.click(await screen.findByText("确认重置"));

    await waitFor(() => screen.getByText("Rand0mPx8"));
    expect(screen.getByText(/新密码已生成/)).toBeTruthy();
  });

  it("授权抽屉：GET 当前授权 → 勾选变更 → PUT 全量替换", async () => {
    seedApps();
    render(
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    );
    await screen.findByText("李霞");

    fireEvent.click(screen.getByLabelText("操作-李霞"));
    fireEvent.click(await screen.findByText("授权 Agent"));

    // 抽屉打开，GET /admin/users/2/apps → [2]（报销政策问答选中）
    await waitFor(() => screen.getByText("授权 Agent · 李霞"));
    await waitFor(() => {
      const checkbox = screen.getByRole("checkbox", { name: /IT 运维助手/ }) as HTMLInputElement;
      expect(checkbox.checked).toBe(false);
    });
    const baoxiao = screen.getByRole("checkbox", { name: /报销政策问答/ }) as HTMLInputElement;
    expect(baoxiao.checked).toBe(true);

    // 勾选 IT 运维助手 → 保存 → PUT {app_ids:[1,2]}
    fireEvent.click(screen.getByRole("checkbox", { name: /IT 运维助手/ }));
    fireEvent.click(screen.getByText("保 存"));

    await waitFor(() => {
      expect(putAppBodies).toHaveLength(1);
    });
    expect(putAppBodies[0]).toEqual({ id: "2", appIds: [1, 2] });
  });
});

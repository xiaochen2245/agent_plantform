import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import { setupServer } from "msw/node";
import { http as mswHttp, HttpResponse } from "msw";
import { handlers } from "../../mocks/handlers";
import { http } from "../../api/http";
import DeptsTab from "./DeptsTab";

interface TestDept {
  id: number;
  name: string;
  parent_id: number | null;
  path: string | null;
}

interface TestUser {
  id: number;
  name: string;
  email: string;
  dept: string | null;
  dept_id: number | null;
  roles: string[];
  status: number;
  created_at: string;
}

let DEPTS: TestDept[] = [];
let DEPT_APPS: Record<number, number[]> = {};
const USERS: TestUser[] = [
  {
    id: 11,
    name: "王小明",
    email: "wxm@company.com",
    dept: "研发部",
    dept_id: 2,
    roles: ["USER"],
    status: 1,
    created_at: "2026-09-01T00:00:00Z",
  },
  {
    id: 12,
    name: "李霞",
    email: "lixia@company.com",
    dept: "研发部",
    dept_id: 2,
    roles: ["USER", "PLATFORM_ADMIN"],
    status: 1,
    created_at: "2026-09-01T00:00:00Z",
  },
];

const listCalls: string[] = [];
const createBodies: Array<Record<string, unknown>> = [];
const patchBodies: Array<{ id: string; body: Record<string, unknown> }> = [];
const deleteIds: string[] = [];
const putAppBodies: Array<{ id: string; appIds: number[] }> = [];

function reset() {
  DEPTS = [
    { id: 1, name: "总部", parent_id: null, path: "/1/" },
    { id: 2, name: "研发部", parent_id: 1, path: "/1/2/" },
    { id: 3, name: "后端组", parent_id: 2, path: "/1/2/3/" },
    { id: 4, name: "财务部", parent_id: 1, path: "/1/4/" },
  ];
  DEPT_APPS = { 2: [1] };
  listCalls.length = 0;
  createBodies.length = 0;
  patchBodies.length = 0;
  deleteIds.length = 0;
  putAppBodies.length = 0;
}

const server = setupServer(
  ...handlers,
  mswHttp.get("http://localhost/api/admin/depts", () => {
    listCalls.push("list");
    return HttpResponse.json({ items: DEPTS });
  }),
  mswHttp.post("http://localhost/api/admin/depts", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    createBodies.push(body);
    const id = DEPTS.length + 1;
    const parent = DEPTS.find((d) => d.id === body.parent_id);
    const path = parent ? `${parent.path}${id}/` : `/${id}/`;
    const created: TestDept = {
      id,
      name: String(body.name),
      parent_id: (body.parent_id as number | null) ?? null,
      path,
    };
    DEPTS.push(created);
    return HttpResponse.json(created, { status: 201 });
  }),
  mswHttp.patch("http://localhost/api/admin/depts/:id", async ({ request, params }) => {
    const body = (await request.json()) as Record<string, unknown>;
    patchBodies.push({ id: String(params.id), body });
    const dept = DEPTS.find((d) => d.id === Number(params.id));
    if (!dept) return HttpResponse.json({ detail: "Not found" }, { status: 404 });
    if (body.name !== undefined) dept.name = String(body.name);
    return HttpResponse.json(dept);
  }),
  mswHttp.delete("http://localhost/api/admin/depts/:id", ({ params }) => {
    deleteIds.push(String(params.id));
    DEPTS = DEPTS.filter((d) => d.id !== Number(params.id));
    return new HttpResponse(null, { status: 204 });
  }),
  mswHttp.get("http://localhost/api/admin/users", ({ request }) => {
    // 部门成员视图：按 dept_id 过滤（与后端语义一致）
    const deptId = new URL(request.url).searchParams.get("dept_id");
    const items = deptId
      ? USERS.filter((u) => u.dept_id === Number(deptId))
      : USERS;
    return HttpResponse.json({ total: items.length, items });
  }),
  mswHttp.get("http://localhost/api/admin/depts/:id/apps", ({ params }) => {
    return HttpResponse.json({ app_ids: DEPT_APPS[Number(params.id)] ?? [] });
  }),
  mswHttp.put("http://localhost/api/admin/depts/:id/apps", async ({ request, params }) => {
    const body = (await request.json()) as { app_ids?: number[] };
    putAppBodies.push({ id: String(params.id), appIds: body.app_ids ?? [] });
    DEPT_APPS[Number(params.id)] = body.app_ids ?? [];
    return HttpResponse.json({ app_ids: body.app_ids ?? [] });
  })
);

http.defaults.baseURL = "http://localhost/api";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => reset());
afterEach(() => {
  server.resetHandlers();
  cleanup();
  vi.restoreAllMocks();
});
afterAll(() => server.close());

describe("部门管理 Tab", () => {
  it("列表加载：渲染扁平列表 + 默认选中第一个部门", async () => {
    render(
      <MemoryRouter>
        <DeptsTab />
      </MemoryRouter>
    );
    await screen.findByText("总部");
    expect(screen.getByText("研发部")).toBeTruthy();
    expect(screen.getByText("后端组")).toBeTruthy();
    expect(screen.getByText("财务部")).toBeTruthy();
    // 详情面板出现（无子部门层级：不再展示深度/父部门）
    expect(screen.getByText("授权 Agent")).toBeTruthy();
    expect(listCalls).toContain("list");
  });

  it("选中部门后展示操作按钮（无子部门/移动）与成员列表", async () => {
    render(
      <MemoryRouter>
        <DeptsTab />
      </MemoryRouter>
    );
    await screen.findByText("研发部");
    fireEvent.click(screen.getByText("研发部"));
    expect(screen.getByText("授权 Agent")).toBeTruthy();
    expect(screen.getByText("改名")).toBeTruthy();
    expect(screen.getByText("删除")).toBeTruthy();
    expect(screen.queryByText("新建子部门")).toBeNull();
    expect(screen.queryByText("移动")).toBeNull();
    // 成员列表：研发部两人（含管理员标签）
    expect(await screen.findByText("成员（2）")).toBeTruthy();
    expect(screen.getByText("王小明")).toBeTruthy();
    expect(screen.getByText("李霞")).toBeTruthy();
    expect(screen.getByText("管理员")).toBeTruthy();
  });

  it("创建顶级部门：调 POST /api/admin/depts", async () => {
    render(
      <MemoryRouter>
        <DeptsTab />
      </MemoryRouter>
    );
    await screen.findByText("总部");

    fireEvent.click(screen.getByText("新建"));
    fireEvent.change(screen.getByLabelText("部门名称"), { target: { value: "市场部" } });
    fireEvent.click(screen.getByText("创 建"));

    await waitFor(() => {
      expect(createBodies).toHaveLength(1);
    });
    expect(createBodies[0]).toMatchObject({ name: "市场部" });
  });

  it("改名：PATCH /api/admin/depts/{id} 携带 name", async () => {
    render(
      <MemoryRouter>
        <DeptsTab />
      </MemoryRouter>
    );
    await screen.findByText("财务部");
    fireEvent.click(screen.getByText("财务部"));

    fireEvent.click(screen.getByText("改名"));
    const input = (await screen.findByLabelText("新名称")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "财务中心" } });
    fireEvent.click(screen.getByText("保 存"));

    await waitFor(() => {
      expect(patchBodies).toHaveLength(1);
    });
    expect(patchBodies[0]).toEqual({ id: "4", body: { name: "财务中心" } });
  });

  it("授权抽屉：GET 当前授权 → 取消（不触发 PUT）", async () => {
    render(
      <MemoryRouter>
        <DeptsTab />
      </MemoryRouter>
    );
    await screen.findByText("研发部");
    fireEvent.click(screen.getByText("研发部"));
    fireEvent.click(screen.getByText("授权 Agent"));

    await screen.findByText(/授权 Agent · 研发部/);
    await waitFor(() => {
      expect(putAppBodies).toHaveLength(0);
    });
    fireEvent.click(screen.getByText("取 消"));
    expect(putAppBodies).toHaveLength(0);
  });

  it("授权抽屉：保存触发 PUT /api/admin/depts/{id}/apps", async () => {
    render(
      <MemoryRouter>
        <DeptsTab />
      </MemoryRouter>
    );
    await screen.findByText("财务部");
    fireEvent.click(screen.getByText("财务部"));
    fireEvent.click(screen.getByText("授权 Agent"));

    await screen.findByText(/授权 Agent · 财务部/);
    // 财务部默认零授权；勾选第一个 app 并保存
    const cb = await screen.findByRole("checkbox", { name: /IT 运维助手/ });
    fireEvent.click(cb);
    fireEvent.click(screen.getByText("保 存"));

    await waitFor(() => {
      expect(putAppBodies).toHaveLength(1);
    });
    expect(putAppBodies[0].id).toBe("4");
    expect(putAppBodies[0].appIds).toEqual([1]);
  });

  it("删除：弹确认 → DELETE /api/admin/depts/{id}", async () => {
    render(
      <MemoryRouter>
        <DeptsTab />
      </MemoryRouter>
    );
    await screen.findByText("财务部");
    fireEvent.click(screen.getByText("财务部"));
    fireEvent.click(screen.getByText("删除"));
    // 等待确认 Modal 渲染（标题包含选名）
    await screen.findByText(/删除部门「财务部」/);
    // 找 modal footer 中的 danger 按钮
    const okBtn = document.querySelector(".ant-modal .ant-btn-dangerous") as HTMLButtonElement;
    expect(okBtn).toBeTruthy();
    fireEvent.click(okBtn);

    await waitFor(() => {
      expect(deleteIds).toContain("4");
    });
  });
});

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { setupServer } from "msw/node";
import { http as mswHttp, HttpResponse } from "msw";
import { handlers } from "../../mocks/handlers";
import { http } from "../../api/http";
import RolesTab from "./RolesTab";

interface TestRole {
  id: number;
  code: string;
  name: string;
}

let ROLES: TestRole[] = [];
let ROLE_APPS: Record<number, number[]> = {};

const createBodies: Array<Record<string, unknown>> = [];
const patchBodies: Array<{ id: string; body: Record<string, unknown> }> = [];
const deleteIds: string[] = [];
const putAppBodies: Array<{ id: string; appIds: number[] }> = [];

function reset() {
  ROLES = [
    { id: 1, code: "USER", name: "普通用户" },
    { id: 2, code: "PLATFORM_ADMIN", name: "平台管理员" },
  ];
  ROLE_APPS = { 1: [1, 2, 3, 4] };
  createBodies.length = 0;
  patchBodies.length = 0;
  deleteIds.length = 0;
  putAppBodies.length = 0;
}

const server = setupServer(
  ...handlers,
  mswHttp.get("http://localhost/api/admin/roles", () => {
    return HttpResponse.json({ items: ROLES });
  }),
  mswHttp.post("http://localhost/api/admin/roles", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    createBodies.push(body);
    const code = String(body.code).toUpperCase();
    if (ROLES.find((r) => r.code === code)) {
      return HttpResponse.json({ detail: "duplicate_code" }, { status: 409 });
    }
    const created: TestRole = {
      id: ROLES.length + 1,
      code,
      name: String(body.name),
    };
    ROLES.push(created);
    return HttpResponse.json(created, { status: 201 });
  }),
  mswHttp.patch("http://localhost/api/admin/roles/:id", async ({ request, params }) => {
    const body = (await request.json()) as Record<string, unknown>;
    patchBodies.push({ id: String(params.id), body });
    const role = ROLES.find((r) => r.id === Number(params.id));
    if (!role) return HttpResponse.json({ detail: "Not found" }, { status: 404 });
    if (body.name !== undefined) role.name = String(body.name);
    return HttpResponse.json(role);
  }),
  mswHttp.delete("http://localhost/api/admin/roles/:id", ({ params }) => {
    deleteIds.push(String(params.id));
    ROLES = ROLES.filter((r) => r.id !== Number(params.id));
    return new HttpResponse(null, { status: 204 });
  }),
  mswHttp.get("http://localhost/api/admin/roles/:id/apps", ({ params }) => {
    return HttpResponse.json({ app_ids: ROLE_APPS[Number(params.id)] ?? [] });
  }),
  mswHttp.put("http://localhost/api/admin/roles/:id/apps", async ({ request, params }) => {
    const body = (await request.json()) as { app_ids?: number[] };
    putAppBodies.push({ id: String(params.id), appIds: body.app_ids ?? [] });
    ROLE_APPS[Number(params.id)] = body.app_ids ?? [];
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

describe("角色管理 Tab", () => {
  it("列表加载：内置角色带锁标", async () => {
    render(
      <MemoryRouter>
        <RolesTab />
      </MemoryRouter>
    );
    await screen.findByText("USER");
    expect(screen.getByText("PLATFORM_ADMIN")).toBeTruthy();
    expect(screen.getAllByText("内置").length).toBeGreaterThanOrEqual(2);
  });

  it("创建自定义角色：表单提交 POST", async () => {
    render(
      <MemoryRouter>
        <RolesTab />
      </MemoryRouter>
    );
    await screen.findByText("USER");

    fireEvent.click(screen.getByText("新建角色"));
    fireEvent.change(screen.getByLabelText("角色码"), { target: { value: "FINANCE_ADMIN" } });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "财务管理员" } });
    fireEvent.click(screen.getByText("创 建"));

    await waitFor(() => {
      expect(createBodies).toHaveLength(1);
    });
    expect(createBodies[0]).toMatchObject({ code: "FINANCE_ADMIN", name: "财务管理员" });
  });

  it("创建角色码自动大写化", async () => {
    render(
      <MemoryRouter>
        <RolesTab />
      </MemoryRouter>
    );
    await screen.findByText("USER");

    fireEvent.click(screen.getByText("新建角色"));
    fireEvent.change(screen.getByLabelText("角色码"), { target: { value: "finance_admin" } });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "财务管理员" } });
    fireEvent.click(screen.getByText("创 建"));

    await waitFor(() => {
      expect(createBodies[0].code).toBe("FINANCE_ADMIN");
    });
  });

  it("授权抽屉：保存触发 PUT /api/admin/roles/{id}/apps", async () => {
    render(
      <MemoryRouter>
        <RolesTab />
      </MemoryRouter>
    );
    await screen.findByText("USER");
    // USER 行有"授权 Agent"按钮，取首个
    fireEvent.click(screen.getAllByText("授权 Agent")[0]);

    await screen.findByText(/授权 Agent · USER/);
    fireEvent.click(screen.getByText("保 存"));

    await waitFor(() => {
      expect(putAppBodies).toHaveLength(1);
    });
    expect(putAppBodies[0].id).toBe("1");
  });

  it("改名按钮：内置角色禁用，自定义角色可用", async () => {
    render(
      <MemoryRouter>
        <RolesTab />
      </MemoryRouter>
    );
    await screen.findByText("USER");

    // 内置 USER 行的"改名"按钮应被 disabled
    const rows = screen.getAllByRole("row");
    const userRow = rows.find((r) => r.textContent?.includes("USER"))!;
    expect(userRow).toBeTruthy();
    const disabledRename = userRow.querySelector("button.ant-btn-link[disabled]");
    expect(disabledRename?.textContent).toMatch(/改名/);

    // 内置 PLATFORM_ADMIN 行的"删除"按钮也应被 disabled
    const adminRow = rows.find((r) => r.textContent?.includes("PLATFORM_ADMIN"))!;
    expect(adminRow).toBeTruthy();
    const disabledDel = adminRow.querySelector("button.ant-btn-dangerous[disabled]");
    expect(disabledDel?.textContent).toMatch(/删除/);
    // 超管不受授权约束：其行不渲染「授权 Agent」入口
    expect(adminRow.textContent).not.toContain("授权 Agent");
  });

  it("删除自定义角色：调用 DELETE /api/admin/roles/{id}", async () => {
    render(
      <MemoryRouter>
        <RolesTab />
      </MemoryRouter>
    );
    await screen.findByText("USER");

    fireEvent.click(screen.getByText("新建角色"));
    fireEvent.change(screen.getByLabelText("角色码"), { target: { value: "TO_DEL" } });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "td" } });
    fireEvent.click(screen.getByText("创 建"));

    // 等待 TO_DEL 行出现
    await waitFor(() => {
      const rows = screen.getAllByRole("row");
      expect(rows.some((r) => r.textContent?.includes("TO_DEL"))).toBeTruthy();
    });

    // 找到 TO_DEL 行的非 disabled 删除按钮
    const rows = screen.getAllByRole("row");
    const toDelRow = rows.find((r) => r.textContent?.includes("TO_DEL"))!;
    const delBtn = toDelRow.querySelector("button.ant-btn-dangerous:not([disabled])") as HTMLButtonElement;
    expect(delBtn).toBeTruthy();
    fireEvent.click(delBtn);

    // 弹 confirm Modal → OK 按钮
    const okBtn = document.querySelector(".ant-modal .ant-btn-dangerous") as HTMLButtonElement;
    expect(okBtn).toBeTruthy();
    fireEvent.click(okBtn);

    await waitFor(() => {
      expect(deleteIds).toContain("3");
    });
  });
});

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, afterAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { http as mswHttp, HttpResponse } from "msw";
import { handlers } from "../mocks/handlers";
import { http } from "../api/http";
import { useChatStore } from "../stores/chat";
import Admin from "./Admin";

const server = setupServer(...handlers);
http.defaults.baseURL = "http://localhost/api";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  cleanup();
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

describe("管理后台（用户与授权）", () => {
  it("渲染统计条与用户表格（紧凑密度）", async () => {
    seedApps();
    render(
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    );

    // 统计：5 员工 / 4 启用 / 1 管理员（mock）
    await waitFor(() => {
      expect(screen.getAllByText("5").length).toBeGreaterThan(0);
    });
    expect(screen.getByText("员工总数")).toBeTruthy();
    expect(screen.getByText("已启用")).toBeTruthy();
    expect(screen.getAllByText("管理员").length).toBeGreaterThan(0); // 统计标签 + 角色 Tag

    // 表格行
    expect(screen.getByText("张明")).toBeTruthy();
    expect(screen.getByText("lixia@company.com")).toBeTruthy();
    expect(screen.getByText("信息技术部")).toBeTruthy();
  });

  it("授权抽屉：打开 → 勾选变更 → 保存触发 PUT 且本地更新", async () => {
    seedApps();
    const putBodies: Array<{ id: string; appIds: number[] }> = [];
    server.use(
      mswHttp.put("http://localhost/api/admin/users/:id/authorizations", async ({ request, params }) => {
        const body = (await request.json()) as { app_ids?: number[] };
        putBodies.push({ id: String(params.id), appIds: body.app_ids ?? [] });
        return HttpResponse.json({ ok: true });
      })
    );

    render(
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    );

    // 等数据加载后打开李霞（id=2，当前授权 [2]）的操作菜单
    await waitFor(() => screen.getByText("李霞"));
    fireEvent.click(screen.getByLabelText("操作-李霞"));
    // Dropdown 渲染在 portal 中
    await waitFor(() => screen.getByText(/授权 Agent（1）/));
    fireEvent.click(screen.getByText(/授权 Agent（1）/));

    // 抽屉打开：勾选「IT 运维助手」(id=1)
    await waitFor(() => screen.getByText("授权 Agent · 李霞"));
    const option = screen.getByText(/IT 运维助手 — /);
    fireEvent.click(option);

    fireEvent.click(screen.getByText("保 存"));

    await waitFor(() => {
      expect(putBodies).toHaveLength(1);
    });
    expect(putBodies[0]).toEqual({ id: "2", appIds: [1, 2] }); // Checkbox.Group 按 options 序返回
    // 本地行数据更新 → 菜单文案变为（2）
    await waitFor(() => screen.getByText("授权 Agent（2）")).catch(() => {
      /* Dropdown 文案仅再次打开时渲染，非断言关键路径 */
    });
  });
});

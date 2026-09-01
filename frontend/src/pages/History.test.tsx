import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, afterAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { handlers } from "../mocks/handlers";
import { http } from "../api/http";
import { useChatStore } from "../stores/chat";
import History from "./History";

const server = setupServer(...handlers);
http.defaults.baseURL = "http://localhost/api";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  useChatStore.setState({
    apps: [],
    appsLoading: false,
    activeAppId: null,
    conversationsByApp: {},
    messagesByConv: {},
    activeConversationId: null,
    streaming: false,
  });
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

describe("历史会话页", () => {
  it("渲染会话列表（标题/agent 标签/条数）并支持点击回放", async () => {
    seedApps();
    render(
      <MemoryRouter>
        <History />
      </MemoryRouter>
    );

    // 列表来自 GET /api/conversations?app_id=1（mock）
    const first = await screen.findByText("VPN 连接失败怎么办");
    expect(first).toBeTruthy();
    expect(screen.getByText("如何申请会议室投影权限")).toBeTruthy();
    expect(screen.getAllByText("IT 运维助手").length).toBeGreaterThan(0); // app 标签/筛选器同名均合法
    expect(screen.getByText(/6 条/)).toBeTruthy();

    // 点击 → 右侧只读回放（mock 详情端点）
    fireEvent.click(first);
    await waitFor(() => {
      expect(screen.getByText(/排查建议/)).toBeTruthy();
    });
    expect(screen.getByText("继续对话")).toBeTruthy();
  });

  it("「继续对话」激活该会话并跳转首页", async () => {
    seedApps();
    render(
      <MemoryRouter initialEntries={["/history"]}>
        <History />
      </MemoryRouter>
    );

    const first = await screen.findByText("VPN 连接失败怎么办");
    fireEvent.click(first);
    const resumeBtn = await screen.findByText("继续对话");
    fireEvent.click(resumeBtn);

    // store 被置为该会话（appId=1, convId=uuid）
    const s = useChatStore.getState();
    expect(s.activeAppId).toBe(1);
    expect(s.activeConversationId).toBe("11111111-1111-1111-1111-111111111111");
  });

  it("agent 筛选切换后列表正确收敛", async () => {
    seedApps();
    render(
      <MemoryRouter>
        <History />
      </MemoryRouter>
    );

    await screen.findByText("VPN 连接失败怎么办");
    // 切到「报销政策问答」（无 mock 会话）→ 空态
    fireEvent.click(screen.getByText("报销政策问答"));
    await waitFor(() => {
      expect(screen.getByText("暂无历史会话")).toBeTruthy();
    });
  });
});

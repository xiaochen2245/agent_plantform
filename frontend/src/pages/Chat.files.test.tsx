/** 附件全链路（契约 v4）：composer 上传 → 发送请求体携带 files → user 消息渲染附件。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, afterAll, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { handlers, lastChatSendBody } from "../mocks/handlers";
import { http } from "../api/http";
import { useChatStore } from "../stores/chat";
import { installSignalSafeFetch } from "../test-setup";
import MessageItem from "../components/MessageItem";
import type { AppInfo } from "../types";
import Chat from "./Chat";

const server = setupServer(...handlers);
http.defaults.baseURL = "http://localhost/api";

const CHAT_APP: AppInfo = {
  id: 1,
  name: "IT 运维助手",
  description: "解答服务器、网络与账号问题",
  mode: "chat",
  inputs_schema: null,
};

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  installSignalSafeFetch();
});
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

function seedChatApp() {
  useChatStore.setState({ apps: [CHAT_APP], activeAppId: 1 });
}

function renderChat() {
  return render(
    <MemoryRouter>
      <Chat />
    </MemoryRouter>
  );
}

describe("附件端到端（mock）", () => {
  it("上传 → 发送 → chat/send 请求体含 files，user 消息展示附件", async () => {
    seedChatApp();
    renderChat();

    // 1) 选文件 → 上传 → chip
    const file = new File(["vpn error log"], "vpn日志.txt", { type: "text/plain" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [file] });
    fireEvent.change(input);
    await waitFor(() => expect(screen.getByText("vpn日志.txt")).toBeTruthy());

    // 2) 输入并发送
    fireEvent.change(screen.getByPlaceholderText("输入你的问题，Enter 发送"), {
      target: { value: "VPN 连不上，日志见附件" },
    });
    fireEvent.click(screen.getByText("发送"));

    // 3) 请求体携带 files（mock handler 捕获）
    await waitFor(() => expect(lastChatSendBody?.files).toBeTruthy());
    expect(lastChatSendBody?.files).toHaveLength(1);
    expect(lastChatSendBody?.files?.[0]).toMatch(/^f_mock_/);

    // 4) 发送后 composer 清空附件；线程里 user 消息仍展示附件名
    await waitFor(() => expect(screen.queryByLabelText("待发送附件")).toBeNull());
    expect(screen.getAllByText("vpn日志.txt").length).toBeGreaterThanOrEqual(1);
  });

  it("无附件时请求体不含 files 数组（向后兼容）", async () => {
    seedChatApp();
    renderChat();
    fireEvent.change(screen.getByPlaceholderText("输入你的问题，Enter 发送"), {
      target: { value: "普通问题" },
    });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(lastChatSendBody?.query).toBe("普通问题"));
    expect(lastChatSendBody?.files).toBeUndefined();
  });

  it("消息渲染：user 消息带 files → 附件 chip（名称+大小），History 回放同组件路径", () => {
    render(
      <MessageItem
        message={{
          id: "m1",
          conversationId: "c1",
          role: "user",
          content: "VPN 连接失败怎么办",
          status: "done",
          files: [{ file_id: "f1", name: "vpn-错误日志.txt", size: 2048, mime: "text/plain" }],
          createdAt: Date.now(),
        }}
        onRetry={() => undefined}
        streaming={false}
      />
    );
    expect(screen.getByText("vpn-错误日志.txt")).toBeTruthy();
    expect(screen.getByText("2.0 KB")).toBeTruthy();
  });
});

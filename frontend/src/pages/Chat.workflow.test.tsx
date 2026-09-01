/** 工作流模式（契约 v3）：表单输入区、必填校验、inputs 分支发送。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, afterAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { http as mswHttp, HttpResponse } from "msw";
import { handlers } from "../mocks/handlers";
import { http } from "../api/http";
import { useChatStore } from "../stores/chat";
import { installSignalSafeFetch } from "../test-setup";
import type { AppInfo } from "../types";
import Chat from "./Chat";

const server = setupServer(...handlers);
http.defaults.baseURL = "http://localhost/api";

const WORKFLOW_APP: AppInfo = {
  id: 4,
  name: "名片生成助手",
  description: "输入名片信息，生成排版名片",
  mode: "workflow",
  inputs_schema: [{ name: "business_card", label: "名片内容", type: "paragraph", required: true }],
};

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  installSignalSafeFetch(); // 包在 MSW 拦截器外层，兜底 jsdom AbortSignal 跨 realm 报错
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

function seedWorkflowApp() {
  useChatStore.setState({ apps: [WORKFLOW_APP], activeAppId: 4 });
}

function renderChat() {
  return render(
    <MemoryRouter>
      <Chat />
    </MemoryRouter>
  );
}

describe("工作流应用对话页", () => {
  it("渲染表单式输入区而非普通 composer", () => {
    seedWorkflowApp();
    renderChat();
    expect(screen.getByText("填写并生成")).toBeTruthy();
    expect(screen.queryByPlaceholderText("输入你的问题，Enter 发送")).toBeNull();
  });

  it("弹窗按 schema 渲染字段并做必填校验（label 优先于 name）", async () => {
    seedWorkflowApp();
    renderChat();
    fireEvent.click(screen.getByText("填写并生成"));

    await waitFor(() => expect(screen.getByLabelText("名片内容")).toBeTruthy());

    // 空提交 → 必填校验文案
    fireEvent.click(screen.getByText("生 成"));
    await waitFor(() => expect(screen.getByText("请输入名片内容")).toBeTruthy());
  });

  it("提交表单后以 inputs 走 chat/send 流式分支", async () => {
    const captured: Array<{ inputs?: Record<string, string>; query?: string }> = [];
    server.use(
      // 路径正则匹配任意 origin（jsdom location=3000，浏览器=实际域名）
      mswHttp.post(/\/api\/chat\/send$/, async ({ request }) => {
        const body = (await request.json()) as { inputs?: Record<string, string>; query?: string };
        captured.push(body);
        const encoder = new TextEncoder();
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            const send = (event: string, data: unknown) =>
              controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
            send("message", { answer: "名片已生成" });
            send("message_end", { metadata: { usage: { total: 42 } } });
            send("agent_done", {});
            controller.close();
          },
        });
        return new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } });
      })
    );

    seedWorkflowApp();
    renderChat();
    fireEvent.click(screen.getByText("填写并生成"));
    await waitFor(() => expect(screen.getByLabelText("名片内容")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("名片内容"), {
      target: { value: "张三 产品经理 13800000000" },
    });
    fireEvent.click(screen.getByText("生 成"));

    await waitFor(() => expect(captured.length).toBe(1));
    expect(captured[0].inputs).toEqual({ business_card: "张三 产品经理 13800000000" });
    expect(captured[0].query).toContain("张三");

    // 流式回答渲染到线程
    await waitFor(() => expect(screen.getByText("名片已生成")).toBeTruthy());
  });
});

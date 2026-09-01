import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import MessageItem from "./MessageItem";
import type { ChatMessage } from "../types";

function msg(patch: Partial<ChatMessage>): ChatMessage {
  return {
    id: "m1",
    conversationId: "c1",
    role: "assistant",
    content: "",
    status: "error",
    createdAt: Date.now(),
    ...patch,
  };
}

afterEach(cleanup);

describe("思考过程面板（契约 v6）", () => {
  const reasoningMsg = msg({
    reasoning: "先分析问题，再检索。",
    content: "",
    status: "streaming",
  });

  it("无 reasoning 不渲染面板", () => {
    render(
      <MessageItem
        message={msg({ content: "普通回答", status: "done" })}
        onRetry={() => undefined}
        streaming={false}
      />
    );
    expect(screen.queryByText("思考过程")).toBeNull();
  });

  it("生成中且无正文：默认展开思考面板", () => {
    render(<MessageItem message={reasoningMsg} onRetry={() => undefined} streaming />);
    expect(screen.getByText("思考过程")).toBeTruthy();
    expect(screen.getByText("先分析问题，再检索。")).toBeTruthy();
  });

  it("历史回放（有正文，非流中）：默认收起，点击展开", async () => {
    render(
      <MessageItem
        message={msg({ reasoning: "旧思考", content: "旧答案", status: "done" })}
        onRetry={() => undefined}
        streaming={false}
      />
    );
    expect(screen.queryByText("旧思考")).toBeNull(); // 收起：内容不可见
    fireEvent.click(screen.getByText("思考过程"));
    expect(screen.getByText("旧思考")).toBeTruthy();
  });

  it("正文出现后自动收起（未手动干预时）", () => {
    const { rerender } = render(
      <MessageItem message={reasoningMsg} onRetry={() => undefined} streaming />
    );
    expect(screen.getByText("先分析问题，再检索。")).toBeTruthy(); // 初始展开
    rerender(
      <MessageItem
        message={msg({ reasoning: "先分析问题，再检索。", content: "答案来了", status: "streaming" })}
        onRetry={() => undefined}
        streaming
      />
    );
    expect(screen.queryByText("先分析问题，再检索。")).toBeNull(); // 自动收起
  });
});

describe("消息错误卡片分类", () => {
  it("unauthorized：锁形图标 + 未授权文案 + 后端 detail 透传，无重试按钮", () => {
    render(
      <MessageItem
        message={msg({ errorKind: "unauthorized", content: "Not authorized for this app" })}
        onRetry={() => undefined}
        streaming={false}
      />
    );
    expect(screen.getByText(/未授权：Not authorized for this app/)).toBeTruthy();
    expect(screen.queryByText("重试")).toBeNull();
  });

  it("generic：警告图标 + 重试按钮", () => {
    render(
      <MessageItem
        message={msg({ errorKind: "generic", content: "回答生成失败，请重试" })}
        onRetry={() => undefined}
        streaming={false}
      />
    );
    expect(screen.getByText("回答生成失败，请重试")).toBeTruthy();
    expect(screen.getByText("重试")).toBeTruthy();
  });

  it("无 errorKind 的历史错误消息保持旧行为（可重试）", () => {
    render(<MessageItem message={msg({ content: "" })} onRetry={() => undefined} streaming={false} />);
    expect(screen.getByText("回答生成失败，请重试")).toBeTruthy();
    expect(screen.getByText("重试")).toBeTruthy();
  });
});

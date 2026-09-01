import { cleanup, render, screen } from "@testing-library/react";
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

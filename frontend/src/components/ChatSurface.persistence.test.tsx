import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatSurface, { type ChatPersistence, type ChatTurn } from "./ChatSurface";

afterEach(cleanup);

function makePersistence(initial: ChatTurn[] = []): ChatPersistence & {
  saved: ChatTurn[][];
} {
  const saved: ChatTurn[][] = [];
  return {
    saved,
    load: vi.fn(async () => initial),
    save: vi.fn(async (turns: ChatTurn[]) => {
      saved.push(turns);
    }),
  };
}

describe("ChatSurface 会话持久化（#38）", () => {
  it("挂载时 load 恢复历史轮次", async () => {
    render(
      <ChatSurface
        title="t"
        placeholder="p"
        streamAnswer={vi.fn()}
        persistence={makePersistence([
          { role: "user", content: "历史问题" },
          { role: "assistant", content: "历史回答" },
        ])}
      />,
    );
    await waitFor(() => expect(screen.getByText("历史问题")).toBeTruthy());
    expect(screen.getByText("历史回答")).toBeTruthy();
  });

  it("一轮完成后 save 收到完整轮次（含助手回答）", async () => {
    const persistence = makePersistence();
    render(
      <ChatSurface
        title="t"
        placeholder="p"
        streamAnswer={async (query, _history, handlers) => {
          handlers.onDelta(`答:${query}`);
        }}
        persistence={persistence}
      />,
    );
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "新问题" } });
    fireEvent.keyDown(screen.getByRole("textbox"), {
      key: "Enter",
      nativeEvent: { isComposing: false } as unknown as KeyboardEvent,
    });
    await waitFor(() => expect(persistence.saved.length).toBe(1));
    expect(persistence.saved[0]).toEqual([
      { role: "user", content: "新问题" },
      { role: "assistant", content: "答:新问题" },
    ]);
  });

  it("无 persistence 时行为不变（不调 save/load）", async () => {
    render(<ChatSurface title="t" placeholder="p" streamAnswer={vi.fn()} />);
    expect(screen.getByText("p")).toBeTruthy();
  });
});

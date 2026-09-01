/** Composer 附件链路（契约 v4）：预校验拒绝 → 上传成功挂 chip → 移除 → 发送携带 files。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { setupServer } from "msw/node";
import { handlers } from "../mocks/handlers";
import Composer from "./Composer";
import type { UploadedFile } from "../types";

const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  cleanup();
});
afterAll(() => server.close());

function pickFiles(file: File) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [file] });
  fireEvent.change(input);
}

function renderComposer(onSend = vi.fn()) {
  const utils = render(<Composer disabled={false} streaming={false} onSend={onSend} onStop={() => undefined} />);
  return { onSend, ...utils };
}

describe("附件预校验", () => {
  it("非白名单类型：提示错误、不上传、无 chip", async () => {
    renderComposer();
    pickFiles(new File(["data"], "程序.zip", { type: "application/zip" }));
    await waitFor(() => expect(screen.getByText(/程序.zip」类型不支持/)).toBeTruthy());
    expect(screen.queryByLabelText("待发送附件")).toBeNull();
  });

  it("超大文件：提示超限、不上传", async () => {
    renderComposer();
    const big = new File([new Uint8Array(20 * 1024 * 1024 + 1)], "大文件.pdf", { type: "application/pdf" });
    pickFiles(big);
    await waitFor(() => expect(screen.getByText(/大文件.pdf」超过 20MB 上限/)).toBeTruthy());
    expect(screen.queryByLabelText("待发送附件")).toBeNull();
  });
});

describe("上传成功与发送", () => {
  it("上传成功挂 chip（名称+大小），可移除", async () => {
    renderComposer();
    pickFiles(new File(["hello pdf"], "季度报告.pdf", { type: "application/pdf" }));

    const chip = await screen.findByText("季度报告.pdf");
    expect(screen.getByText("9 B")).toBeTruthy(); // "hello pdf" = 9 字节
    expect(screen.queryByLabelText("待发送附件")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("移除 季度报告.pdf"));
    await waitFor(() => expect(screen.queryByText("季度报告.pdf")).toBeNull());
    void chip;
  });

  it("发送时 onSend 携带 (query, files)", async () => {
    const onSend = vi.fn();
    renderComposer(onSend);
    pickFiles(new File(["x"], "日志.txt", { type: "text/plain" }));
    await screen.findByText("日志.txt");

    fireEvent.change(screen.getByPlaceholderText("输入你的问题，Enter 发送"), {
      target: { value: "帮我看看日志" },
    });
    fireEvent.click(screen.getByText("发送"));

    expect(onSend).toHaveBeenCalledTimes(1);
    const [query, files] = onSend.mock.calls[0] as [string, UploadedFile[]];
    expect(query).toBe("帮我看看日志");
    expect(files).toHaveLength(1);
    expect(files[0]).toMatchObject({ name: "日志.txt", mime: "text/plain" });
    expect(files[0].file_id).toMatch(/^f_mock_/);

    // 发送后清空附件
    await waitFor(() => expect(screen.queryByText("日志.txt")).toBeNull());
  });

  it("上传中（spinner）不可发送", async () => {
    const { http: rawHttp, HttpResponse: RawResp } = await import("msw");
    server.use(
      // 只观察 UI 状态：拖慢响应即可，无需解析 body（解析保真由默认 handler 覆盖）
      rawHttp.post("/api/chat/files", async () => {
        await new Promise((r) => setTimeout(r, 300));
        return RawResp.json({ file_id: "f_slow", name: "慢文件.pdf", size: 4, mime: "application/pdf" }, { status: 201 });
      })
    );
    renderComposer();
    const sendBtn = () => screen.getByText("发送").closest("button") as HTMLButtonElement;
    // 先有正文（隔离 uploading 变量的影响），再触发上传
    fireEvent.change(screen.getByPlaceholderText("输入你的问题，Enter 发送"), {
      target: { value: "慢上传测试" },
    });
    expect(sendBtn().disabled).toBe(false);
    pickFiles(new File(["slow"], "慢文件.pdf", { type: "application/pdf" }));
    // 上传在途：发送按钮禁用
    await waitFor(() => expect(sendBtn().disabled).toBe(true));
    // 上传完成：恢复可用并挂上 chip
    await waitFor(() => expect(sendBtn().disabled).toBe(false));
    expect(screen.getByText("慢文件.pdf")).toBeTruthy();
  });
});

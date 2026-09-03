import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { http as mswHttp, HttpResponse } from "msw";
import { handlers } from "../mocks/handlers";
import Review from "./Review";

const server = setupServer(
  ...handlers,
  mswHttp.post("http://localhost/api/review/docx", () =>
    HttpResponse.json({
      summary: { total_issues: 1, by_type: { font: 1 } },
      issues: [{
        type: "font", severity: "warn", paragraph: 3, text: "微软雅黑段落",
        expected: "宋体", actual: "微软雅黑", message: "中文字体与模板基准不符（样式 Normal）",
      }],
    })),
  mswHttp.post("http://localhost/api/review/typos", () =>
    HttpResponse.json({
      model: "llm-assisted",
      typos: [{ orig: "按装", suggestion: "安装", confidence: 0.92, paragraph: 2, context: "设备按装完毕" }],
    })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { cleanup(); server.resetHandlers(); });
afterAll(() => server.close());

function pick(input: HTMLInputElement, file: File): void {
  fireEvent.change(input, { target: { files: [file] } });
}

describe("Review 页面（#32）", () => {
  it("双上传后出报告：规则问题+错别字候选", async () => {
    render(<Review />);
    const inputs = document.querySelectorAll<HTMLInputElement>('input[type="file"]');
    expect(inputs.length).toBeGreaterThanOrEqual(2);
    pick(inputs[0], new File(["doc"], "待审.docx"));
    pick(inputs[1], new File(["tpl"], "模板.docx"));
    fireEvent.click(screen.getByRole("button", { name: "开始审查" }));

    await waitFor(() => {
      expect(screen.getByText(/中文字体与模板基准不符/)).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByText("按装 → 安装")).toBeTruthy();
      expect(screen.getByText(/第 3 段/)).toBeTruthy();
      expect(screen.getByText("置信 92%")).toBeTruthy();
    });
  });
});

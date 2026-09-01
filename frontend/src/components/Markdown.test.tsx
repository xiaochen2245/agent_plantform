import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Markdown from "./Markdown";

describe("Markdown 渲染", () => {
  it("渲染标题/加粗/列表", () => {
    render(<Markdown content={"## 标题\n\n**重点**内容\n\n- 甲\n- 乙"} />);
    expect(screen.getByText("标题").tagName).toBe("H2");
    expect(screen.getByText("重点").tagName).toBe("STRONG");
    expect(screen.getByText("甲").closest("ul")).toBeTruthy();
  });

  it("代码块与行内代码分别渲染", () => {
    render(<Markdown content={"行内 `code` 与块：\n\n```python\nprint(1)\n```"} />);
    expect(screen.getByText("code").tagName).toBe("CODE");
    expect(screen.getByText("print(1)").tagName).toBe("CODE");
    expect(screen.getByText("print(1)").closest("pre")).toBeTruthy();
  });

  it("GFM 表格渲染", () => {
    render(<Markdown content={"| A | B |\n|---|---|\n| 1 | 2 |"} />);
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText("A").tagName).toBe("TH");
  });
});

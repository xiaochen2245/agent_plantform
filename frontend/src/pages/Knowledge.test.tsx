import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, afterAll, beforeEach, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { handlers } from "../mocks/handlers";
import { useAuthStore } from "../stores/auth";
import Knowledge from "./Knowledge";

/** 知识库页（契约 v7）：公共 mock 已含 kb 种子（General Mode-ECO 1 + 两文档）。 */

const server = setupServer(...handlers);

beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
  cleanup();
});
afterAll(() => server.close());

const ME_ADMIN = {
  id: 1,
  email: "admin@company.com",
  name: "张明",
  roles: ["USER", "PLATFORM_ADMIN"],
  dept_id: null,
};
const ME_USER = { ...ME_ADMIN, id: 2, roles: ["USER"] };

beforeEach(() => {
  useAuthStore.setState({ me: ME_ADMIN, status: "authenticated" });
});

describe("知识库页", () => {
  it("管理员：见知识库列表、文档与索引状态，且有上传/添加文本入口", async () => {
    render(<Knowledge />);
    expect((await screen.findAllByText("General Mode-ECO 1")).length).toBeGreaterThan(0);
    // 默认选中首库 → 文档表渲染
    expect(await screen.findByText("报销政策.pdf")).toBeTruthy();
    expect(screen.getByText("已完成")).toBeTruthy();
    expect(screen.getByText("索引中")).toBeTruthy();
    expect(screen.getByText("上传文档")).toBeTruthy();
    expect(screen.getByText("添加文本")).toBeTruthy();
    // 契约 v9：库级管理入口与审计区仅管理员可见
    expect(screen.getByText("新建知识库")).toBeTruthy();
    expect(screen.getByText("授权")).toBeTruthy();
    expect(await screen.findByText("操作审计")).toBeTruthy();
    expect(await screen.findByText("dataset_create")).toBeTruthy();
  });

  it("普通员工：可读（列表/文档/命中测试），无写入口", async () => {
    useAuthStore.setState({ me: ME_USER, status: "authenticated" });
    render(<Knowledge />);
    expect(await screen.findByText("报销政策.pdf")).toBeTruthy();
    expect(screen.queryByText("上传文档")).toBeNull();
    expect(screen.queryByText("添加文本")).toBeNull();
    expect(screen.queryByText("新建知识库")).toBeNull();
    expect(screen.queryByText("授权")).toBeNull();
    expect(screen.queryByText("操作审计")).toBeNull();
    expect(screen.getByPlaceholderText("输入问题，测试该知识库的检索命中")).toBeTruthy();
  });

  it("命中测试：输入问题 → 返回分段与得分", async () => {
    render(<Knowledge />);
    await screen.findByText("报销政策.pdf");
    const input = screen.getByPlaceholderText("输入问题，测试该知识库的检索命中");
    fireEvent.change(input, { target: { value: "报销时限" } });
    fireEvent.click(screen.getByRole("button", { name: /检索/ }));
    await waitFor(() =>
      expect(screen.getByText(/差旅报销需在出差结束后 7 天内提交/)).toBeTruthy()
    );
    expect(screen.getByText("score 0.912")).toBeTruthy();
  });
});

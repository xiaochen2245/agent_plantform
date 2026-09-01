import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import Login from "./Login";

afterEach(cleanup);

describe("登录页渲染（视觉规范 v3）", () => {
  it("包含品牌区要素：标题、能力项、眉标", () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );
    expect(screen.getAllByText("Agent 平台").length).toBeGreaterThan(0);
    expect(screen.getByText(/让每位员工/)).toBeTruthy();
    expect(screen.getByText("IT 运维问答")).toBeTruthy();
    expect(screen.getByText("报销政策查询")).toBeTruthy();
    expect(screen.getByText("代码评审助手")).toBeTruthy();
    expect(screen.getByText("一个入口 · 问所有事")).toBeTruthy();
  });

  it("登录卡包含邮箱/密码/记住我/登录按钮/SSO 占位", () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );
    expect(screen.getByLabelText("企业邮箱")).toBeTruthy();
    expect(screen.getByLabelText("密码")).toBeTruthy();
    expect(screen.getAllByText("记住我").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /^登\s*录$/ })).toBeTruthy();
    expect(screen.getByText(/企业统一认证登录/)).toBeTruthy();
  });
});

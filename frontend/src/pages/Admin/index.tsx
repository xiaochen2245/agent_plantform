import { Tabs } from "antd";
import { useState } from "react";
import DeptsTab from "./DeptsTab";
import RolesTab from "./RolesTab";
import UsersTab from "./UsersTab";

type TabKey = "users" | "depts" | "roles";

/** 管理后台（仅 PLATFORM_ADMIN，路由守卫见 App.tsx）。
 *  Tabs：用户 / 部门 / 角色；三态授权统一由 AuthorizationsDrawer 复用。 */
export default function Admin() {
  const [tab, setTab] = useState<TabKey>("users");

  return (
    <div className="admin-page">
      <div className="page-header">
        <h2 className="font-display">权限管理</h2>
        <span className="page-header-sub">
          企业员工账号、部门组织、角色与 Agent 三态授权
        </span>
      </div>
      <Tabs
        activeKey={tab}
        onChange={(k) => setTab(k as TabKey)}
        items={[
          { key: "users", label: "用户", children: <UsersTab /> },
          { key: "depts", label: "部门", children: <DeptsTab /> },
          { key: "roles", label: "角色", children: <RolesTab /> },
        ]}
      />
    </div>
  );
}

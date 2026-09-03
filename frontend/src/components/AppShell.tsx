import {
  ApartmentOutlined,
  AppstoreOutlined,
  FolderOutlined,
  HistoryOutlined,
  LogoutOutlined,
  MessageOutlined,
  UserOutlined,
  UserSwitchOutlined,
} from "@ant-design/icons";
import { Avatar, Button, Tooltip } from "antd";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuthStore } from "../stores/auth";
import Logomark from "./Logomark";

/** 侧栏三区导航（视觉规范 .stitch/designs/agent-workbench.html）：编 辑 / 员 工 端 / 管 理。 */
export default function AppShell() {
  const navigate = useNavigate();
  const me = useAuthStore((s) => s.me);
  const logout = useAuthStore((s) => s.logout);
  const isAdmin = me?.roles?.includes("PLATFORM_ADMIN") ?? false;

  const navItems = [
    {
      section: "应 用",
      items: [
        { to: "/", icon: <AppstoreOutlined />, label: "首页" },
        { to: "/kb", icon: <FolderOutlined />, label: "知识库" },
        { to: "/history", icon: <HistoryOutlined />, label: "历史会话" },
        { to: "/workbench", icon: <ApartmentOutlined />, label: "Agent 工作台" },
      ],
    },
    ...(isAdmin
      ? [{ section: "管 理", items: [{ to: "/admin", icon: <UserSwitchOutlined />, label: "权限管理" }] }]
      : []),
  ];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <Logomark size={32} />
          <div>
            <div className="sidebar-brand-name font-display">Agent 平台</div>
            <div className="sidebar-brand-sub">AI&nbsp;WORKSPACE</div>
          </div>
        </div>

        <nav className="sidebar-nav">
          {navItems.map((group) => (
            <div key={group.section} className="sidebar-group">
              <div className="sidebar-section">{group.section}</div>
              {group.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
                  {item.icon}
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-user">
          <Avatar size={32} icon={<UserOutlined />} style={{ background: "var(--teal-soft)", color: "var(--teal)" }}>
            {me?.name?.slice(0, 1)}
          </Avatar>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.2 }}>{me?.name ?? "员工"}</div>
            <div style={{ fontSize: 10.5, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis" }}>{me?.email}</div>
          </div>
          <Tooltip title="退出登录">
            <Button
              type="text"
              size="small"
              icon={<LogoutOutlined />}
              onClick={async () => {
                await logout();
                navigate("/login");
              }}
            />
          </Tooltip>
        </div>
      </aside>

      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}

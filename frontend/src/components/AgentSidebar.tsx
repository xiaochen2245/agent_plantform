import { LogoutOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Button, Tooltip } from "antd";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "../stores/auth";
import { useChatStore } from "../stores/chat";
import Logomark from "./Logomark";

/** 左侧导航：产品标 + 我的 Agent 列表 + 用户区。 */
export default function AgentSidebar() {
  const navigate = useNavigate();
  const me = useAuthStore((s) => s.me);
  const logout = useAuthStore((s) => s.logout);
  const apps = useChatStore((s) => s.apps);
  const activeAppId = useChatStore((s) => s.activeAppId);
  const setActiveApp = useChatStore((s) => s.setActiveApp);

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <Logomark size={32} />
        <div>
          <div className="sidebar-brand-name font-display">Agent 平台</div>
          <div className="sidebar-brand-sub">AI&nbsp;WORKSPACE</div>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", paddingBottom: 8 }}>
        <div className="sidebar-section">我的 AGENT</div>
        {apps.map((app) => (
          <div
            key={app.id}
            className={`agent-item${app.id === activeAppId ? " active" : ""}`}
            onClick={() => setActiveApp(app.id)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") setActiveApp(app.id);
            }}
          >
            <div>
              <div className="name">{app.name}</div>
              <div className="desc">{app.description}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="sidebar-user">
        <Avatar size={32} icon={<UserOutlined />} style={{ background: "var(--teal-soft)", color: "var(--teal)" }}>
          {me?.name?.slice(0, 1)}
        </Avatar>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.2 }}>{me?.name ?? "员工"}</div>
          <div style={{ fontSize: 10.5, color: "var(--text-muted)" }}>{me?.email}</div>
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
  );
}

import { MoreOutlined, TeamOutlined, UserAddOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Button, Checkbox, Drawer, Dropdown, Table, Tag, Tooltip, message } from "antd";
import type { MenuProps } from "antd";
import { useEffect, useState } from "react";
import { getAdminStats, getAdminUsers, putUserAuthorizations, type AdminStats, type AdminUser } from "../api/admin";
import { useChatStore } from "../stores/chat";

function roleTags(roles: string[]): React.ReactNode[] {
  return roles.map((r) => (
    <Tag key={r} color={r === "PLATFORM_ADMIN" ? "teal" : "default"} style={{ marginInlineEnd: 4 }}>
      {r === "PLATFORM_ADMIN" ? "管理员" : r === "APP_ADMIN" ? "应用管理员" : "员工"}
    </Tag>
  ));
}

/** 用户与授权工作台（仅 PLATFORM_ADMIN，路由守卫见 App.tsx）。数据当前来自 mock（api/admin.ts 封装，后端就绪后换实现即可）。 */
export default function Admin() {
  const apps = useChatStore((s) => s.apps);
  const loadApps = useChatStore((s) => s.loadApps);

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [drawerUser, setDrawerUser] = useState<AdminUser | null>(null);
  const [checkedApps, setCheckedApps] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      const [u, s] = await Promise.all([getAdminUsers(), getAdminStats()]);
      setUsers(u);
      setStats(s);
    } catch {
      message.error("管理数据加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    if (apps.length === 0) void loadApps();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function openDrawer(user: AdminUser) {
    setDrawerUser(user);
    setCheckedApps(user.authorized_app_ids);
  }

  async function saveAuthorizations() {
    if (!drawerUser) return;
    setSaving(true);
    try {
      await putUserAuthorizations(drawerUser.id, checkedApps);
      setUsers((prev) => prev.map((u) => (u.id === drawerUser.id ? { ...u, authorized_app_ids: checkedApps } : u)));
      message.success(`已保存 ${drawerUser.name} 的 Agent 授权`);
      setDrawerUser(null);
    } catch {
      message.error("授权保存失败");
    } finally {
      setSaving(false);
    }
  }

  const columns = [
    {
      title: "姓名",
      dataIndex: "name",
      width: 160,
      render: (name: string) => (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <Avatar size={26} icon={<UserOutlined />} style={{ background: "var(--teal-soft)", color: "var(--teal)" }}>
            {name.slice(0, 1)}
          </Avatar>
          <span style={{ fontWeight: 600 }}>{name}</span>
        </span>
      ),
    },
    { title: "邮箱", dataIndex: "email", width: 220 },
    { title: "部门", dataIndex: "dept", width: 120 },
    { title: "角色", dataIndex: "roles", width: 200, render: roleTags },
    {
      title: "状态",
      dataIndex: "enabled",
      width: 90,
      render: (enabled: boolean, record: AdminUser) => (
        <Tooltip title={enabled ? "点击禁用（mock）" : "点击启用（mock）"}>
          <Button
            type="text"
            size="small"
            style={{ color: enabled ? "var(--teal)" : "var(--text-muted)", fontWeight: 600, paddingInline: 4 }}
            onClick={() => {
              setUsers((prev) => prev.map((u) => (u.id === record.id ? { ...u, enabled: !enabled } : u)));
              message.info(`${record.name} 已${enabled ? "禁用" : "启用"}（mock，未落库）`);
            }}
          >
            {enabled ? "● 启用" : "○ 禁用"}
          </Button>
        </Tooltip>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 80,
      render: (_: unknown, record: AdminUser) => {
        const items: MenuProps["items"] = [
          { key: "auth", icon: <TeamOutlined />, label: `授权 Agent（${record.authorized_app_ids.length}）` },
        ];
        return (
          <Dropdown
            menu={{ items, onClick: ({ key }) => key === "auth" && openDrawer(record) }}
            trigger={["click"]}
          >
            <Button type="text" size="small" icon={<MoreOutlined />} aria-label={`操作-${record.name}`} />
          </Dropdown>
        );
      },
    },
  ];

  return (
    <div className="admin-page">
      <div className="page-header">
        <h2 className="font-display">用户与授权</h2>
        <span className="page-header-sub">企业员工账号与 Agent 访问授权</span>
        <div style={{ flex: 1 }} />
        <Button icon={<UserAddOutlined />} disabled title="后端端点未建（mock 阶段）">
          添加用户
        </Button>
      </div>

      <div className="admin-stats">
        <div className="stat-chip">
          <div className="label">员工总数</div>
          <div className="value">{stats?.employees ?? "—"}</div>
        </div>
        <div className="stat-chip">
          <div className="label">已启用</div>
          <div className="value">{stats?.enabled ?? "—"}</div>
        </div>
        <div className="stat-chip">
          <div className="label">管理员</div>
          <div className="value">{stats?.admins ?? "—"}</div>
        </div>
      </div>

      <div className="admin-table">
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={users}
          pagination={{ pageSize: 8, showSizeChanger: false }}
        />
      </div>

      <Drawer
        title={drawerUser ? `授权 Agent · ${drawerUser.name}` : "授权 Agent"}
        width={420}
        open={drawerUser !== null}
        onClose={() => setDrawerUser(null)}
        footer={
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Button onClick={() => setDrawerUser(null)}>取消</Button>
            <Button type="primary" loading={saving} onClick={() => void saveAuthorizations()}>
              保存
            </Button>
          </div>
        }
      >
        <p style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 0 }}>
          勾选该员工可使用的 Agent（mock 数据；后端授权端点就绪后自动走真实存储）
        </p>
        <Checkbox.Group
          style={{ display: "flex", flexDirection: "column", gap: 14 }}
          value={checkedApps}
          onChange={(vals) => setCheckedApps(vals as number[])}
          options={apps.map((a) => ({ label: `${a.name} — ${a.description}`, value: a.id }))}
        />
      </Drawer>
    </div>
  );
}

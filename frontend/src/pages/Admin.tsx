import { KeyOutlined, MoreOutlined, ReloadOutlined, TeamOutlined, UserAddOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Button, Checkbox, Drawer, Dropdown, Form, Input, Modal, Segmented, Table, Tag, Tooltip, Typography, message } from "antd";
import type { MenuProps } from "antd";
import { useCallback, useEffect, useState } from "react";
import {
  createUser,
  getUserApps,
  getUserDatasets,
  listUsers,
  patchUser,
  putUserApps,
  putUserDatasets,
  resetPassword,
  type AdminUser,
} from "../api/admin";
import { listDatasets } from "../api/kb";
import { extractDetail } from "../api/http";
import { useChatStore } from "../stores/chat";

const PAGE_SIZE = 20;

function roleTags(roles: string[]): React.ReactNode[] {
  return roles.map((r) => (
    <Tag key={r} color={r === "PLATFORM_ADMIN" ? "teal" : "default"} style={{ marginInlineEnd: 4 }}>
      {r === "PLATFORM_ADMIN" ? "管理员" : r === "APP_ADMIN" ? "应用管理员" : "员工"}
    </Tag>
  ));
}

interface CreateUserForm {
  name: string;
  email: string;
  password: string;
}

/** 用户与授权工作台（仅 PLATFORM_ADMIN，路由守卫见 App.tsx）。
 * 契约 v2 真实端点：列表分页/搜索、PATCH 状态、新建、重置密码、用户级 Agent 授权。 */
export default function Admin() {
  const apps = useChatStore((s) => s.apps);
  const loadApps = useChatStore((s) => s.loadApps);

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | 0 | 1>("all");
  const [loading, setLoading] = useState(false);

  const [drawerUser, setDrawerUser] = useState<AdminUser | null>(null);
  const [drawerApps, setDrawerApps] = useState<number[]>([]);
  const [drawerDatasets, setDrawerDatasets] = useState<string[]>([]);
  const [datasetCatalog, setDatasetCatalog] = useState<{ id: string; name: string }[]>([]);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm<CreateUserForm>();
  const [creating, setCreating] = useState(false);

  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
  const [resetPassword_, setResetPassword_] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);

  const refresh = useCallback(
    async (opts?: { page?: number; query?: string; status?: "all" | 0 | 1 }) => {
      const p = opts?.page ?? page;
      const q = opts?.query ?? query;
      const st = opts?.status ?? statusFilter;
      setLoading(true);
      try {
        const data = await listUsers({
          page: p,
          page_size: PAGE_SIZE,
          query: q || undefined,
          status: st === "all" ? undefined : st,
        });
        setUsers(data.items);
        setTotal(data.total);
      } catch {
        message.error("用户列表加载失败");
      } finally {
        setLoading(false);
      }
    },
    [page, query, statusFilter]
  );

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, statusFilter]);

  useEffect(() => {
    if (apps.length === 0) void loadApps();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function patchRow(updated: AdminUser) {
    setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
  }

  async function toggleStatus(user: AdminUser) {
    const next = user.status === 1 ? 0 : 1;
    try {
      const updated = await patchUser(user.id, { status: next });
      patchRow(updated);
      message.success(`${user.name} 已${next === 1 ? "启用" : "禁用"}`);
    } catch (e) {
      message.error(extractDetail(e, "状态修改失败"));
    }
  }

  async function openDrawer(user: AdminUser) {
    setDrawerUser(user);
    setDrawerApps([]);
    setDrawerDatasets([]);
    setDrawerLoading(true);
    try {
      const [{ app_ids }, { dataset_ids }, kbPage] = await Promise.all([
        getUserApps(user.id),
        getUserDatasets(user.id),
        listDatasets(), // 管理员视角全量目录（名称来自 Dify）
      ]);
      setDrawerApps(app_ids);
      setDrawerDatasets(dataset_ids);
      setDatasetCatalog(kbPage.data?.map((d) => ({ id: d.id, name: d.name })) ?? []);
    } catch {
      message.error("该用户授权信息加载失败");
      setDrawerUser(null);
    } finally {
      setDrawerLoading(false);
    }
  }

  async function saveAuthorizations() {
    if (!drawerUser) return;
    setSaving(true);
    try {
      await Promise.all([
        putUserApps(drawerUser.id, drawerApps),
        putUserDatasets(drawerUser.id, drawerDatasets),
      ]);
      message.success(`已保存 ${drawerUser.name} 的授权`);
      setDrawerUser(null);
    } catch (e) {
      message.error(extractDetail(e, "授权保存失败"));
    } finally {
      setSaving(false);
    }
  }

  async function submitCreate() {
    let values: CreateUserForm;
    try {
      values = await createForm.validateFields();
    } catch {
      return; // 校验错误已由 Form 展示
    }
    setCreating(true);
    try {
      const created = await createUser(values);
      message.success(`已创建用户 ${created.name}（初始密码即表单所填）`);
      setCreateOpen(false);
      createForm.resetFields();
      setPage(1);
      void refresh({ page: 1 });
    } catch (e) {
      message.error(extractDetail(e, "创建失败"));
    } finally {
      setCreating(false);
    }
  }

  async function doResetPassword() {
    if (!resetTarget) return;
    setResetting(true);
    try {
      const { password } = await resetPassword(resetTarget.id);
      setResetPassword_(password);
    } catch (e) {
      message.error(extractDetail(e, "重置失败"));
    } finally {
      setResetting(false);
    }
  }

  const columns = [
    {
      title: "姓名",
      dataIndex: "name",
      width: 150,
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
    {
      title: "部门",
      dataIndex: "dept",
      width: 120,
      render: (dept: string | null) => dept ?? <span style={{ color: "var(--text-muted)" }}>—</span>,
    },
    { title: "角色", dataIndex: "roles", width: 180, render: roleTags },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (_: unknown, record: AdminUser) => {
        const enabled = record.status === 1;
        return (
          <Tooltip title={enabled ? "点击禁用" : "点击启用"}>
            <Button
              type="text"
              size="small"
              style={{ color: enabled ? "var(--teal)" : "var(--text-muted)", fontWeight: 600, paddingInline: 4 }}
              onClick={() => void toggleStatus(record)}
            >
              {enabled ? "● 启用" : "○ 禁用"}
            </Button>
          </Tooltip>
        );
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 80,
      render: (_: unknown, record: AdminUser) => {
        const items: MenuProps["items"] = [
          { key: "auth", icon: <TeamOutlined />, label: "授权 Agent" },
          { key: "reset", icon: <KeyOutlined />, label: "重置密码" },
        ];
        return (
          <Dropdown
            menu={{
              items,
              onClick: ({ key }) => {
                if (key === "auth") void openDrawer(record);
                if (key === "reset") {
                  setResetTarget(record);
                  setResetPassword_(null);
                }
              },
            }}
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
        <Button icon={<UserAddOutlined />} type="primary" onClick={() => setCreateOpen(true)}>
          添加用户
        </Button>
      </div>

      <div className="admin-toolbar">
        <Input.Search
          placeholder="搜索姓名或邮箱"
          allowClear
          style={{ width: 260 }}
          onSearch={(v) => {
            setPage(1);
            setQuery(v);
            void refresh({ page: 1, query: v });
          }}
        />
        <Segmented
          value={statusFilter === "all" ? "全部" : statusFilter === 1 ? "启用" : "禁用"}
          onChange={(v) => {
            const next = v === "全部" ? "all" : v === "启用" ? 1 : 0;
            setStatusFilter(next);
            setPage(1);
          }}
          options={["全部", "启用", "禁用"]}
        />
        <div style={{ flex: 1 }} />
        <span style={{ color: "var(--text-muted)", fontSize: 12.5 }}>员工总数 {total}</span>
      </div>

      <div className="admin-table">
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={users}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            showSizeChanger: false,
            onChange: (p) => setPage(p),
          }}
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
            <Button type="primary" loading={saving} disabled={drawerLoading} onClick={() => void saveAuthorizations()}>
              保存
            </Button>
          </div>
        }
      >
        <p style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 0 }}>
          勾选该员工可直接使用的 Agent 与知识库（用户级授权；部门/角色级授权后续开放）
        </p>
        {drawerLoading ? (
          <span style={{ color: "var(--text-muted)", fontSize: 12.5 }}>加载当前授权…</span>
        ) : (
          <>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10 }}>Agent 授权</div>
            <Checkbox.Group
              style={{ display: "flex", flexDirection: "column", gap: 14 }}
              value={drawerApps}
              onChange={(vals) => setDrawerApps(vals as number[])}
              options={apps.map((a) => ({ label: `${a.name} — ${a.description}`, value: a.id }))}
            />
            <div style={{ fontWeight: 600, fontSize: 13, margin: "20px 0 10px" }}>知识库授权（租户隔离）</div>
            {datasetCatalog.length === 0 ? (
              <span style={{ color: "var(--text-muted)", fontSize: 12.5 }}>
                暂无可授权的知识库（先在 Dify 控制台创建并共享给全员）
              </span>
            ) : (
              <Checkbox.Group
                style={{ display: "flex", flexDirection: "column", gap: 14 }}
                value={drawerDatasets}
                onChange={(vals) => setDrawerDatasets(vals as string[])}
                options={datasetCatalog.map((d) => ({ label: d.name, value: d.id }))}
              />
            )}
          </>
        )}
      </Drawer>

      <Modal
        title="添加用户"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        okText="创建"
        confirmLoading={creating}
        destroyOnClose
      >
        <Form form={createForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="姓名" rules={[{ required: true, message: "请输入姓名" }]}>
            <Input placeholder="张三" />
          </Form.Item>
          <Form.Item
            name="email"
            label="企业邮箱"
            rules={[
              { required: true, message: "请输入邮箱" },
              { type: "email", message: "邮箱格式不正确" },
            ]}
          >
            <Input placeholder="zhangsan@company.com" />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始密码"
            rules={[
              { required: true, message: "请输入初始密码" },
              { min: 6, message: "密码至少 6 位" },
            ]}
          >
            <Input.Password placeholder="≥ 6 位" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={resetTarget ? `重置密码 · ${resetTarget.name}` : "重置密码"}
        open={resetTarget !== null}
        onCancel={() => setResetTarget(null)}
        footer={
          resetPassword_ ? (
            <Button type="primary" onClick={() => setResetTarget(null)}>
              完成
            </Button>
          ) : (
            [
              <Button key="cancel" onClick={() => setResetTarget(null)}>
                取消
              </Button>,
              <Button key="ok" type="primary" loading={resetting} icon={<ReloadOutlined />} onClick={() => void doResetPassword()}>
                确认重置
              </Button>,
            ]
          )
        }
        destroyOnClose
      >
        {resetPassword_ ? (
          <div style={{ fontSize: 13, lineHeight: 1.8 }}>
            <p style={{ margin: 0 }}>新密码已生成（该用户的全部登录态已失效）：</p>
            <Typography.Paragraph copyable={{ text: resetPassword_ }} style={{ marginTop: 8 }}>
              <code style={{ fontSize: 15, fontWeight: 700 }}>{resetPassword_}</code>
            </Typography.Paragraph>
            <p style={{ color: "var(--text-muted)", margin: 0 }}>点击密码旁的复制图标，安全送达用户后再关闭。</p>
          </div>
        ) : (
          <p style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.8 }}>
            将为 <b>{resetTarget?.name}</b> 生成随机新密码，并使其所有已登录设备下线。确认继续？
          </p>
        )}
      </Modal>
    </div>
  );
}

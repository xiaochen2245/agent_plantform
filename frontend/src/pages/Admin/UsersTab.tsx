import {
  ApartmentOutlined,
  KeyOutlined,
  MoreOutlined,
  ReloadOutlined,
  SafetyOutlined,
  TeamOutlined,
  UserAddOutlined,
  UserOutlined,
} from "@ant-design/icons";
import {
  Avatar,
  Button,
  Form,
  Input,
  Modal,
  Segmented,
  Select,
  Table,
  Tag,
  Tooltip,
  Typography,
  Dropdown,
  message,
} from "antd";
import type { MenuProps } from "antd";
import { useCallback, useEffect, useState } from "react";
import {
  createUser,
  getUserApps,
  listDepts,
  listRoles,
  listUsers,
  patchUser,
  putUserApps,
  resetPassword,
  type AdminUser,
} from "../../api/admin";
import { extractDetail } from "../../api/http";
import AuthorizationsDrawer from "./AuthorizationsDrawer";

const PAGE_SIZE = 20;

function roleTags(roles: string[]): React.ReactNode[] {
  return roles.map((r) => (
    <Tag
      key={r}
      color={r === "PLATFORM_ADMIN" ? "teal" : "default"}
      style={{ marginInlineEnd: 4 }}
    >
      {r === "PLATFORM_ADMIN" ? "管理员" : r === "APP_ADMIN" ? "应用管理员" : "员工"}
    </Tag>
  ));
}

interface CreateUserForm {
  name: string;
  email: string;
  password: string;
  dept_id?: number | null;
  roles?: string[];
}

export default function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | 0 | 1>("all");
  const [loading, setLoading] = useState(false);

  const [drawerUser, setDrawerUser] = useState<AdminUser | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm<CreateUserForm>();
  const [creating, setCreating] = useState(false);

  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);

  // 设置部门（行菜单）：目标用户 + 部门目录（懒加载一次）
  const [deptTarget, setDeptTarget] = useState<AdminUser | null>(null);
  const [deptValue, setDeptValue] = useState<number | null>(null);
  const [deptOptions, setDeptOptions] = useState<{ label: string; value: number }[]>([]);
  const [savingDept, setSavingDept] = useState(false);

  // 部门目录懒加载：行菜单「设置部门」或新建用户弹窗首次需要时拉一次
  useEffect(() => {
    if ((!deptTarget && !createOpen) || deptOptions.length > 0) return;
    listDepts()
      .then(({ items }) =>
        setDeptOptions(items.map((d) => ({ label: d.name, value: d.id })))
      )
      .catch(() => setDeptOptions([]));
  }, [deptTarget, createOpen, deptOptions.length]);

  async function saveDept() {
    if (!deptTarget) return;
    setSavingDept(true);
    try {
      const updated = await patchUser(deptTarget.id, { dept_id: deptValue });
      patchRow(updated);
      message.success(`已将 ${deptTarget.name} 归属${updated.dept ? `「${updated.dept}」` : "移出部门"}`);
      setDeptTarget(null);
    } catch (e) {
      message.error(extractDetail(e, "部门设置失败"));
    } finally {
      setSavingDept(false);
    }
  }
  const [resetPasswordValue, setResetPasswordValue] = useState<string | null>(null);
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

  async function submitCreate() {
    let values: CreateUserForm;
    try {
      values = await createForm.validateFields();
    } catch {
      return;
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

  // 设置角色（行菜单）：目标用户 + 角色目录（懒加载；value=角色码，PATCH 全量替换）
  const [roleTarget, setRoleTarget] = useState<AdminUser | null>(null);
  const [roleValue, setRoleValue] = useState<string[]>([]);
  const [roleOptions, setRoleOptions] = useState<{ label: string; value: string }[]>([]);
  const [savingRole, setSavingRole] = useState(false);

  useEffect(() => {
    if ((!roleTarget && !createOpen) || roleOptions.length > 0) return;
    listRoles()
      .then(({ items }) =>
        setRoleOptions(items.map((r) => ({ label: `${r.name}（${r.code}）`, value: r.code })))
      )
      .catch(() => setRoleOptions([]));
  }, [roleTarget, createOpen, roleOptions.length]);

  async function saveRole() {
    if (!roleTarget) return;
    setSavingRole(true);
    try {
      const updated = await patchUser(roleTarget.id, {
        roles: roleValue.length > 0 ? roleValue : ["USER"], // 全量替换；清空=回基础角色
      });
      patchRow(updated);
      message.success(`已保存 ${roleTarget.name} 的角色`);
      setRoleTarget(null);
    } catch (e) {
      message.error(extractDetail(e, "角色设置失败"));
    } finally {
      setSavingRole(false);
    }
  }

  async function doResetPassword() {
    if (!resetTarget) return;
    setResetting(true);
    try {
      const { password } = await resetPassword(resetTarget.id);
      setResetPasswordValue(password);
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
      render: (dept: string | null) =>
        dept ?? <span style={{ color: "var(--text-muted)" }}>—</span>,
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
              style={{
                color: enabled ? "var(--teal)" : "var(--text-muted)",
                fontWeight: 600,
                paddingInline: 4,
              }}
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
        // 超管不受授权约束：不展示「授权 Agent」入口（展示即误导）
        const isSuperAdmin = record.roles?.includes("PLATFORM_ADMIN") ?? false;
        const items: MenuProps["items"] = [
          ...(isSuperAdmin
            ? []
            : [{ key: "auth", icon: <TeamOutlined />, label: "授权 Agent" }]),
          { key: "dept", icon: <ApartmentOutlined />, label: "设置部门" },
          { key: "roles", icon: <SafetyOutlined />, label: "设置角色" },
          { key: "reset", icon: <KeyOutlined />, label: "重置密码" },
        ];
        return (
          <Dropdown
            menu={{
              items,
              onClick: ({ key }) => {
                if (key === "auth") setDrawerUser(record);
                if (key === "dept") {
                  setDeptTarget(record);
                  setDeptValue(record.dept_id ?? null);
                }
                if (key === "roles") {
                  setRoleTarget(record);
                  setRoleValue(record.roles ?? []);
                }
                if (key === "reset") {
                  setResetTarget(record);
                  setResetPasswordValue(null);
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
    <div>
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
        <Button icon={<UserAddOutlined />} type="primary" onClick={() => setCreateOpen(true)}>
          添加用户
        </Button>
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

      <AuthorizationsDrawer
        open={drawerUser !== null}
        title={drawerUser ? `授权 Agent · ${drawerUser.name}` : "授权 Agent"}
        hint="勾选该员工可直接使用的 Agent（用户级授权；部门/角色级授权见对应标签）"
        load={async () => {
          if (!drawerUser) return [];
          const { app_ids } = await getUserApps(drawerUser.id);
          return app_ids;
        }}
        save={async (ids) => {
          if (!drawerUser) return;
          await putUserApps(drawerUser.id, ids);
        }}
        onClose={() => setDrawerUser(null)}
      />

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
          <Form.Item name="dept_id" label="归属部门（可选）">
            <Select
              allowClear
              placeholder="暂不归属"
              options={deptOptions}
            />
          </Form.Item>
          <Form.Item name="roles" label="角色（可选，缺省普通员工）">
            <Select mode="multiple" allowClear placeholder="USER" options={roleOptions} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={roleTarget ? `设置角色 · ${roleTarget.name}` : "设置角色"}
        open={roleTarget !== null}
        onCancel={() => setRoleTarget(null)}
        onOk={() => void saveRole()}
        okText="保存"
        confirmLoading={savingRole}
        destroyOnClose
      >
        <p style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 0 }}>
          角色是授权的载体：给角色授权 Agent（角色页）或知识库（知识库页授权抽屉）后，拥有该角色的员工全体生效。USER 为基础角色不可移除意义；全不选 = 仅 USER。角色在「角色」页维护。
        </p>
        <Select
          style={{ width: "100%" }}
          mode="multiple"
          allowClear
          placeholder="选择角色"
          value={roleValue}
          onChange={setRoleValue}
          options={roleOptions}
        />
      </Modal>

      <Modal
        title={deptTarget ? `设置部门 · ${deptTarget.name}` : "设置部门"}
        open={deptTarget !== null}
        onCancel={() => setDeptTarget(null)}
        onOk={() => void saveDept()}
        okText="保存"
        confirmLoading={savingDept}
        destroyOnClose
      >
        <p style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 0 }}>
          部门级授权对该部门所有员工生效（与用户/角色授权取并集）；部门在「部门」页维护。
        </p>
        <Select
          style={{ width: "100%" }}
          allowClear
          placeholder="选择部门（清空 = 移出部门）"
          value={deptValue}
          onChange={setDeptValue}
          options={deptOptions}
        />
      </Modal>

      <Modal
        title={resetTarget ? `重置密码 · ${resetTarget.name}` : "重置密码"}
        open={resetTarget !== null}
        onCancel={() => setResetTarget(null)}
        footer={
          resetPasswordValue ? (
            <Button type="primary" onClick={() => setResetTarget(null)}>
              完成
            </Button>
          ) : (
            [
              <Button key="cancel" onClick={() => setResetTarget(null)}>
                取消
              </Button>,
              <Button
                key="ok"
                type="primary"
                loading={resetting}
                icon={<ReloadOutlined />}
                onClick={() => void doResetPassword()}
              >
                确认重置
              </Button>,
            ]
          )
        }
        destroyOnClose
      >
        {resetPasswordValue ? (
          <div style={{ fontSize: 13, lineHeight: 1.8 }}>
            <p style={{ margin: 0 }}>新密码已生成（该用户的全部登录态已失效）：</p>
            <Typography.Paragraph copyable={{ text: resetPasswordValue }} style={{ marginTop: 8 }}>
              <code style={{ fontSize: 15, fontWeight: 700 }}>{resetPasswordValue}</code>
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

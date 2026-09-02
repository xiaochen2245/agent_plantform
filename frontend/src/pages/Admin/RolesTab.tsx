import {
  DeleteOutlined,
  LockOutlined,
  PlusOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  Modal,
  Table,
  Tag,
  Tooltip,
  message,
} from "antd";
import { useCallback, useEffect, useState } from "react";
import {
  createRole,
  deleteRole,
  getRoleApps,
  listRoles,
  putRoleApps,
  updateRole,
  type AdminRole,
} from "../../api/admin";
import { extractDetail } from "../../api/http";
import AuthorizationsDrawer from "./AuthorizationsDrawer";

interface CreateForm {
  code: string;
  name: string;
}

interface RenameForm {
  name: string;
}

const BUILTIN_CODES = new Set(["USER", "PLATFORM_ADMIN"]);

export default function RolesTab() {
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm<CreateForm>();
  const [creating, setCreating] = useState(false);

  const [renameTarget, setRenameTarget] = useState<AdminRole | null>(null);
  const [renameForm] = Form.useForm<RenameForm>();
  const [renaming, setRenaming] = useState(false);

  const [authTarget, setAuthTarget] = useState<AdminRole | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AdminRole | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const { items } = await listRoles();
      setRoles(items);
    } catch {
      message.error("角色列表加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function submitCreate() {
    let values: CreateForm;
    try {
      values = await createForm.validateFields();
    } catch {
      return;
    }
    setCreating(true);
    try {
      const r = await createRole({ code: values.code.toUpperCase(), name: values.name });
      message.success(`已创建角色 ${r.code}`);
      setCreateOpen(false);
      createForm.resetFields();
      void refresh();
    } catch (e) {
      message.error(extractDetail(e, "创建失败"));
    } finally {
      setCreating(false);
    }
  }

  async function submitRename() {
    if (!renameTarget) return;
    let values: RenameForm;
    try {
      values = await renameForm.validateFields();
    } catch {
      return;
    }
    setRenaming(true);
    try {
      await updateRole(renameTarget.id, { name: values.name });
      message.success("已改名");
      setRenameTarget(null);
      void refresh();
    } catch (e) {
      message.error(extractDetail(e, "改名失败"));
    } finally {
      setRenaming(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    const role = deleteTarget;
    setDeleteTarget(null);
    try {
      await deleteRole(role.id);
      message.success("已删除");
      void refresh();
    } catch (e) {
      message.error(extractDetail(e, "删除失败"));
    }
  }

  return (
    <div>
      <div className="admin-toolbar">
        <span style={{ color: "var(--text-muted)", fontSize: 12.5 }}>
          角色定义与 Agent 授权；USER / PLATFORM_ADMIN 为内置角色不可删除。
        </span>
        <div style={{ flex: 1 }} />
        <Button icon={<PlusOutlined />} type="primary" onClick={() => setCreateOpen(true)}>
          新建角色
        </Button>
      </div>

      <div className="admin-table">
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={roles}
          pagination={false}
          columns={[
            {
              title: "角色码",
              dataIndex: "code",
              width: 200,
              render: (code: string) =>
                BUILTIN_CODES.has(code) ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <code style={{ fontWeight: 600 }}>{code}</code>
                    <Tooltip title="内置角色">
                      <LockOutlined style={{ color: "var(--text-muted)", fontSize: 12 }} />
                    </Tooltip>
                  </span>
                ) : (
                  <code>{code}</code>
                ),
            },
            { title: "名称", dataIndex: "name", width: 220 },
            {
              title: "类型",
              width: 120,
              render: (_, r) =>
                BUILTIN_CODES.has(r.code) ? (
                  <Tag color="teal">内置</Tag>
                ) : (
                  <Tag>自定义</Tag>
                ),
            },
            {
              title: "操作",
              key: "actions",
              render: (_, r) => (
                <div style={{ display: "flex", gap: 8 }}>
                  <Button
                    type="link"
                    size="small"
                    icon={<TeamOutlined />}
                    onClick={() => setAuthTarget(r)}
                  >
                    授权 Agent
                  </Button>
                  <Button
                    type="link"
                    size="small"
                    disabled={BUILTIN_CODES.has(r.code)}
                    onClick={() => {
                      setRenameTarget(r);
                      renameForm.setFieldsValue({ name: r.name });
                    }}
                  >
                    改名
                  </Button>
                  <Button
                    type="link"
                    size="small"
                    danger
                    disabled={BUILTIN_CODES.has(r.code)}
                    icon={<DeleteOutlined />}
                    onClick={() => setDeleteTarget(r)}
                  >
                    删除
                  </Button>
                </div>
              ),
            },
          ]}
        />
      </div>

      <AuthorizationsDrawer
        open={authTarget !== null}
        title={authTarget ? `授权 Agent · ${authTarget.code}` : "授权 Agent"}
        hint="拥有该角色的所有用户的可见 Agent（角色级授权，与用户/部门授权取并集）"
        load={async () => {
          if (!authTarget) return [];
          const { app_ids } = await getRoleApps(authTarget.id);
          return app_ids;
        }}
        save={async (ids) => {
          if (!authTarget) return;
          await putRoleApps(authTarget.id, ids);
        }}
        onClose={() => setAuthTarget(null)}
      />

      <Modal
        title="新建角色"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        okText="创建"
        confirmLoading={creating}
        destroyOnClose
      >
        <Form form={createForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="code"
            label="角色码"
            tooltip="SNAKE_CASE；后端自动大写化"
            rules={[
              { required: true, message: "请输入角色码" },
              { pattern: /^[A-Za-z][A-Za-z0-9_]*$/, message: "仅字母数字下划线，且以字母开头" },
            ]}
          >
            <Input placeholder="FINANCE_ADMIN" />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="财务管理员" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={deleteTarget ? `删除角色「${deleteTarget.code}」？` : "删除角色"}
        open={deleteTarget !== null}
        onCancel={() => setDeleteTarget(null)}
        onOk={() => void confirmDelete()}
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
        destroyOnClose
      >
        <p style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.8 }}>
          关联该角色的 user_roles 与 role 类型 app_authorizations 会被一并清理。
        </p>
      </Modal>

      <Modal
        title={renameTarget ? `改名 · ${renameTarget.code}` : "改名"}
        open={renameTarget !== null}
        onCancel={() => setRenameTarget(null)}
        onOk={() => void submitRename()}
        okText="保存"
        confirmLoading={renaming}
        destroyOnClose
      >
        <Form form={renameForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="新名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

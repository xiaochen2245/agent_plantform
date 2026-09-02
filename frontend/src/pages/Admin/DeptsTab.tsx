import {
  ApartmentOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { Button, Empty, Form, Input, List, Modal, message } from "antd";
import { useCallback, useEffect, useState } from "react";
import {
  createDept,
  deleteDept,
  getDeptApps,
  listDepts,
  putDeptApps,
  updateDept,
  type AdminDept,
} from "../../api/admin";
import { extractDetail } from "../../api/http";
import AuthorizationsDrawer from "./AuthorizationsDrawer";

interface CreateForm {
  name: string;
}

interface RenameForm {
  name: string;
}

/** 部门管理（扁平模型：无子部门层级；后端 parent_id 列保留恒空）。
 * 部门的作用：员工归属（UsersTab 设置）+ 部门级 Agent/知识库授权。 */
export default function DeptsTab() {
  const [depts, setDepts] = useState<AdminDept[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    if (selectedId === null && depts.length > 0) setSelectedId(depts[0].id);
  }, [depts, selectedId]);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm<CreateForm>();
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameForm] = Form.useForm<RenameForm>();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const { items } = await listDepts();
      setDepts(items);
      if (selectedId && !items.find((d) => d.id === selectedId)) setSelectedId(null);
    } catch {
      message.error("部门列表加载失败");
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = depts.find((d) => d.id === selectedId) ?? null;

  async function submitCreate() {
    let values: CreateForm;
    try {
      values = await createForm.validateFields();
    } catch {
      return;
    }
    try {
      const created = await createDept({ name: values.name, parent_id: null });
      message.success(`已创建部门 ${created.name}`);
      setCreateOpen(false);
      createForm.resetFields();
      setSelectedId(created.id);
      void refresh();
    } catch (e) {
      message.error(extractDetail(e, "创建失败"));
    }
  }

  async function submitRename() {
    if (!selected) return;
    let values: RenameForm;
    try {
      values = await renameForm.validateFields();
    } catch {
      return;
    }
    try {
      await updateDept(selected.id, { name: values.name });
      message.success("已改名");
      setRenameOpen(false);
      void refresh();
    } catch (e) {
      message.error(extractDetail(e, "改名失败"));
    }
  }

  async function confirmDelete() {
    if (!selected) return;
    setConfirmDeleteOpen(false);
    try {
      await deleteDept(selected.id);
      message.success("已删除");
      setSelectedId(null);
      void refresh();
    } catch (e) {
      message.error(extractDetail(e, "删除失败"));
    }
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 16 }}>
      <div
        style={{
          background: "var(--surface-1)",
          borderRadius: 10,
          padding: 12,
          minHeight: 360,
          border: "1px solid var(--border)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 8,
          }}
        >
          <span style={{ fontWeight: 600, fontSize: 13 }}>部门</span>
          <Button
            size="small"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              createForm.resetFields();
              setCreateOpen(true);
            }}
          >
            新建
          </Button>
        </div>
        {loading && depts.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="加载中…" />
        ) : depts.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无部门" />
        ) : (
          <List
            size="small"
            dataSource={depts}
            renderItem={(d) => (
              <List.Item
                onClick={() => setSelectedId(d.id)}
                style={{
                  cursor: "pointer",
                  padding: "8px 10px",
                  borderRadius: 8,
                  background: d.id === selectedId ? "var(--teal-soft)" : undefined,
                }}
              >
                <ApartmentOutlined style={{ color: "var(--teal)", marginRight: 8 }} />
                {d.name}
              </List.Item>
            )}
          />
        )}
      </div>

      <div
        style={{
          background: "var(--surface-1)",
          borderRadius: 10,
          padding: 16,
          minHeight: 360,
          border: "1px solid var(--border)",
        }}
      >
        {!selected ? (
          <Empty description="在左侧选择一个部门以查看和编辑" />
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <ApartmentOutlined style={{ color: "var(--teal)", fontSize: 18 }} />
              <span style={{ fontSize: 18, fontWeight: 700 }}>{selected.name}</span>
              <div style={{ flex: 1 }} />
              <Button icon={<TeamOutlined />} onClick={() => setDrawerOpen(true)}>
                授权 Agent
              </Button>
              <Button
                icon={<EditOutlined />}
                onClick={() => {
                  renameForm.setFieldsValue({ name: selected.name });
                  setRenameOpen(true);
                }}
              >
                改名
              </Button>
              <Button danger icon={<DeleteOutlined />} onClick={() => setConfirmDeleteOpen(true)}>
                删除
              </Button>
            </div>
            <div style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 8 }}>
              员工归属在「用户」页的行菜单「设置部门」中配置；部门级授权对该部门所有员工生效（与用户/角色授权取并集）。
            </div>
          </>
        )}
      </div>

      <AuthorizationsDrawer
        open={drawerOpen}
        title={selected ? `授权 Agent · ${selected.name}` : "授权 Agent"}
        hint="该部门下所有员工的可见 Agent（部门级授权，与用户/角色授权取并集）"
        load={async () => {
          if (!selected) return [];
          const { app_ids } = await getDeptApps(selected.id);
          return app_ids;
        }}
        save={async (ids) => {
          if (!selected) return;
          await putDeptApps(selected.id, ids);
        }}
        onClose={() => setDrawerOpen(false)}
      />

      <Modal
        title="新建部门"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        okText="创建"
        destroyOnClose
      >
        <Form form={createForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="部门名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="如：研发部" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="改名"
        open={renameOpen}
        onCancel={() => setRenameOpen(false)}
        onOk={() => void submitRename()}
        okText="保存"
        destroyOnClose
      >
        <Form form={renameForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="新名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={selected ? `删除部门「${selected.name}」？` : "删除部门"}
        open={confirmDeleteOpen}
        onCancel={() => setConfirmDeleteOpen(false)}
        onOk={() => void confirmDelete()}
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
        destroyOnClose
      >
        <p style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.8 }}>
          仍有员工归属的部门会被后端拒绝。
        </p>
      </Modal>
    </div>
  );
}

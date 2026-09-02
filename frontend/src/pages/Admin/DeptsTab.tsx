import {
  ApartmentOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import {
   
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Select as AntSelect,
  Tree,
  message,
} from "antd";
import type { DataNode } from "antd/es/tree";
import { useCallback, useEffect, useMemo, useState } from "react";
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
  parent_id?: number | null;
}

interface RenameForm {
  name: string;
}

interface MoveForm {
  parent_id: number | null;
}

/** 递归把 AdminDept[] 拼成 AntD Tree 节点（path 排序保证父先于子）。 */
function buildTree(depts: AdminDept[]): DataNode[] {
  const map = new Map<number, DataNode>();
  for (const d of depts) {
    map.set(d.id, { key: d.id, title: d.name, children: [] });
  }
  const roots: DataNode[] = [];
  for (const d of depts) {
    const node = map.get(d.id)!;
    if (d.parent_id && map.has(d.parent_id)) {
      (map.get(d.parent_id)!.children as DataNode[]).push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

/** 从 path `/1/3/7/` 推出深度（顶级 = 1）。 */
function depthOf(d: AdminDept): number {
  if (!d.path) return 1;
  return d.path.split("/").filter(Boolean).length;
}

/** candidate 是否为 ancestor 的后代（含自身则返回 false）。 */
function isDescendant(candidate: AdminDept, ancestor: AdminDept): boolean {
  if (!ancestor.path) return false;
  return candidate.id !== ancestor.id && candidate.path?.startsWith(ancestor.path) === true;
}

export default function DeptsTab() {

  const [depts, setDepts] = useState<AdminDept[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // 列表加载后默认选中第一个顶级部门
  useEffect(() => {
    if (selectedId === null && depts.length > 0) {
      const top = depts.find((d) => d.parent_id === null) ?? depts[0];
      setSelectedId(top.id);
    }
  }, [depts, selectedId]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [createInitial, setCreateInitial] = useState<number | null>(null);
  const [createForm] = Form.useForm<CreateForm>();

  const [renameOpen, setRenameOpen] = useState(false);
  const [renameForm] = Form.useForm<RenameForm>();

  const [moveOpen, setMoveOpen] = useState(false);
  const [moveForm] = Form.useForm<MoveForm>();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const { items } = await listDepts();
      setDepts(items);
      if (selectedId && !items.find((d) => d.id === selectedId)) {
        setSelectedId(null);
      }
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

  const tree = useMemo(() => buildTree(depts), [depts]);
  const selected = depts.find((d) => d.id === selectedId) ?? null;
  // 移动时可选父：排除自己与后代（含自身）的环检测
  const parentOptions = useMemo(() => {
    if (!selected) return depts;
    return depts.filter((d) => !isDescendant(d, selected));
  }, [depts, selected]);

  async function submitCreate() {
    let values: CreateForm;
    try {
      values = await createForm.validateFields();
    } catch {
      return;
    }
    try {
      const created = await createDept({
        name: values.name,
        parent_id: values.parent_id ?? createInitial,
      });
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

  async function submitMove() {
    if (!selected) return;
    let values: MoveForm;
    try {
      values = await moveForm.validateFields();
    } catch {
      return;
    }
    try {
      await updateDept(selected.id, { parent_id: values.parent_id });
      message.success("已移动");
      setMoveOpen(false);
      void refresh();
    } catch (e) {
      message.error(extractDetail(e, "移动失败"));
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
          <span style={{ fontWeight: 600, fontSize: 13 }}>部门树</span>
          <Button
            size="small"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setCreateInitial(null);
              createForm.resetFields();
              setCreateOpen(true);
            }}
          >
            新建
          </Button>
        </div>
        {loading && depts.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="加载中…" />
        ) : tree.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无部门" />
        ) : (
          <Tree
            treeData={tree}
            selectedKeys={selectedId != null ? [selectedId] : []}
            onSelect={(keys) => {
              const k = keys[0];
              setSelectedId(typeof k === "number" ? k : k ? Number(k) : null);
            }}
            showLine={{ showLeafIcon: false }}
            blockNode
            defaultExpandedKeys={depts.map((d) => d.id)}
            defaultExpandAll
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
              <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                · 深度 {depthOf(selected)} · path <code>{selected.path}</code>
              </span>
              <div style={{ flex: 1 }} />
              <Button icon={<TeamOutlined />} onClick={() => setDrawerOpen(true)}>
                授权 Agent
              </Button>
              <Button
                icon={<PlusOutlined />}
                onClick={() => {
                  setCreateInitial(selected.id);
                  createForm.resetFields();
                  setCreateOpen(true);
                }}
              >
                新建子部门
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
              <Button
                icon={<ApartmentOutlined />}
                onClick={() => {
                  moveForm.setFieldsValue({ parent_id: selected.parent_id });
                  setMoveOpen(true);
                }}
              >
                移动
              </Button>
              <Button danger icon={<DeleteOutlined />} onClick={() => setConfirmDeleteOpen(true)}>
                删除
              </Button>
            </div>
            <div style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 8 }}>
              父部门：{selected.parent_id ? `#${selected.parent_id}` : "顶级"}
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
          <Form.Item
            name="parent_id"
            label="父部门"
            tooltip="留空则为顶级；选中左侧节点后点击「新建子部门」自动填入"
          >
            <AntSelect
              allowClear
              placeholder="顶级"
              options={depts.map((d) => ({ value: d.id, label: d.name }))}
            />
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
        title="移动到其他部门"
        open={moveOpen}
        onCancel={() => setMoveOpen(false)}
        onOk={() => void submitMove()}
        okText="移动"
        destroyOnClose
      >
        <Form form={moveForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="parent_id"
            label="新父部门"
            tooltip="不能选自己或自己的后代；置空则移到顶级"
            rules={[{ required: true, message: "请选择新父（含顶级）" }]}
          >
            <AntSelect
              allowClear
              placeholder="顶级"
              options={[
                { value: null, label: "（顶级）" },
                ...parentOptions.map((d) => ({ value: d.id, label: d.name })),
              ]}
            />
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
          有子部门或用户的部门会被后端拒绝。
        </p>
      </Modal>
    </div>
  );
}

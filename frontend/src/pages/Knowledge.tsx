import {
  DeleteOutlined,
  FileAddOutlined,
  ReloadOutlined,
  SearchOutlined,
  TeamOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import {
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Table,
  Tag,
  Upload,
  message,
} from "antd";
import type { UploadRequestOption } from "rc-upload/lib/interface";
import { useCallback, useEffect, useState } from "react";
import {
  addDatasetGrant,
  createDataset,
  createDocByFile,
  createDocByText,
  deleteDataset,
  deleteDocument,
  listDatasetGrants,
  listDatasets,
  listDocuments,
  listKbAudit,
  removeDatasetGrant,
  retrieveChunks,
  type DifyPage,
  type KbAuditItem,
  type KbDataset,
  type KbDocument,
  type KbGrant,
  type RetrieveRecord,
} from "../api/kb";
import { extractDetail } from "../api/http";
import { listDepts, listRoles, listUsers } from "../api/admin";
import { useAuthStore } from "../stores/auth";

/** 索引状态 → 中文文案与颜色；未终态（非 completed/error/paused）触发 5s 轮询。 */
const STATUS_META: Record<string, { label: string; color: string }> = {
  completed: { label: "已完成", color: "green" },
  error: { label: "失败", color: "red" },
  paused: { label: "已暂停", color: "orange" },
  waiting: { label: "排队中", color: "blue" },
  parsing: { label: "解析中", color: "blue" },
  splitting: { label: "切分中", color: "blue" },
  indexing: { label: "索引中", color: "blue" },
  cleaning: { label: "清洗中", color: "blue" },
};
const TERMINAL = ["completed", "error", "paused"];

function statusTag(doc: KbDocument) {
  const meta = STATUS_META[doc.indexing_status] ?? { label: doc.indexing_status, color: "default" };
  return (
    <Tag color={meta.color}>
      {meta.label}
      {doc.error ? `（${doc.error.slice(0, 24)}）` : ""}
    </Tag>
  );
}

function fmtTime(ts?: number | string): string {
  if (!ts) return "—";
  const d = typeof ts === "number" ? new Date(ts * (ts > 1e12 ? 1 : 1000)) : new Date(ts);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("zh-CN", { hour12: false });
}

interface TextForm {
  name: string;
  text: string;
}

interface DatasetForm {
  name: string;
  indexing_technique: "high_quality" | "economy";
}

type PrincipalType = KbGrant["principal_type"];
const PRINCIPAL_LABEL: Record<PrincipalType, string> = {
  user: "用户",
  dept: "部门",
  role: "角色",
};

/** 知识库工作台（契约 v7）：文档管理 + 检索测试。
 * 权限：读全员；上传/删除仅 PLATFORM_ADMIN（后端 403 兜底，此处隐藏入口）。
 * 边界：App↔知识库绑定在 Dify 控制台完成，Service API 无此能力。 */
export default function Knowledge() {
  const isAdmin = useAuthStore((s) => s.me?.roles?.includes("PLATFORM_ADMIN") ?? false);

  const [datasets, setDatasets] = useState<KbDataset[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(false);
  const [selected, setSelected] = useState<KbDataset | null>(null);
  const [docs, setDocs] = useState<KbDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);

  const [textOpen, setTextOpen] = useState(false);
  const [textForm] = Form.useForm<TextForm>();
  const [creating, setCreating] = useState(false);

  const [createDsOpen, setCreateDsOpen] = useState(false);
  const [createDsForm] = Form.useForm<DatasetForm>();
  const [creatingDs, setCreatingDs] = useState(false);

  const [grantsOpen, setGrantsOpen] = useState(false);
  const [grants, setGrants] = useState<KbGrant[]>([]);
  const [grantsLoading, setGrantsLoading] = useState(false);
  const [grantType, setGrantType] = useState<PrincipalType>("dept");
  const [grantTarget, setGrantTarget] = useState<number | null>(null);
  const [grantOptions, setGrantOptions] = useState<{ label: string; value: number }[]>([]);
  const [grantAdding, setGrantAdding] = useState(false);

  const [audit, setAudit] = useState<{ total: number; items: KbAuditItem[] }>({ total: 0, items: [] });
  const [auditLoading, setAuditLoading] = useState(false);

  const [hitQuery, setHitQuery] = useState("");
  const [hitLoading, setHitLoading] = useState(false);
  const [hitRecords, setHitRecords] = useState<RetrieveRecord[] | null>(null);

  const loadDatasets = useCallback(async () => {
    setDatasetsLoading(true);
    try {
      const page: DifyPage<KbDataset> = await listDatasets();
      setDatasets(page.data ?? []);
      // 当前选中库被删/不可见时清选择
      setSelected((cur) => (cur && page.data?.some((d) => d.id === cur.id) ? cur : page.data?.[0] ?? null));
    } catch (e) {
      message.error(extractDetail(e, "知识库列表加载失败"));
    } finally {
      setDatasetsLoading(false);
    }
  }, []);

  const loadDocs = useCallback(
    async (quiet = false) => {
      if (!selected) return;
      if (!quiet) setDocsLoading(true);
      try {
        const page = await listDocuments(selected.id, { page_size: 100 });
        setDocs(page.data ?? []);
      } catch (e) {
        if (!quiet) message.error(extractDetail(e, "文档列表加载失败"));
      } finally {
        if (!quiet) setDocsLoading(false);
      }
    },
    [selected]
  );

  useEffect(() => {
    void loadDatasets();
  }, [loadDatasets]);

  useEffect(() => {
    void loadDocs();
  }, [loadDocs]);

  // 存在未终态文档 → 5s 轮询（静默，不打 loading 态）
  useEffect(() => {
    if (!selected || !docs.some((d) => !TERMINAL.includes(d.indexing_status))) return;
    const t = setInterval(() => void loadDocs(true), 5000);
    return () => clearInterval(t);
  }, [selected, docs, loadDocs]);

  const afterWrite = () => {
    void loadDocs();
    void loadDatasets(); // document_count 同步刷新
    if (isAdmin) void loadAudit(); // 契约 v9：写操作后刷新审计区
  };

  const loadAudit = useCallback(async () => {
    setAuditLoading(true);
    try {
      setAudit(await listKbAudit());
    } catch {
      /* 审计区静默失败：不阻断主页面 */
    } finally {
      setAuditLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isAdmin) void loadAudit();
  }, [isAdmin, loadAudit]);

  const submitCreateDataset = async () => {
    const values = await createDsForm.validateFields();
    setCreatingDs(true);
    try {
      const created = await createDataset(values);
      message.success(`已创建「${created.name}」`);
      setCreateDsOpen(false);
      createDsForm.resetFields();
      await loadDatasets();
      setSelected(created);
    } catch (e) {
      message.error(extractDetail(e, "创建失败"));
    } finally {
      setCreatingDs(false);
    }
  };

  const confirmDeleteDataset = async (ds: KbDataset) => {
    try {
      await deleteDataset(ds.id);
      message.success(`已删除「${ds.name}」`);
      if (selected?.id === ds.id) setSelected(null);
      await loadDatasets();
      if (isAdmin) void loadAudit();
    } catch (e) {
      message.error(extractDetail(e, "删除失败"));
    }
  };

  const loadGrants = useCallback(async () => {
    if (!selected) return;
    setGrantsLoading(true);
    try {
      setGrants(await listDatasetGrants(selected.id));
    } catch (e) {
      message.error(extractDetail(e, "授权信息加载失败"));
    } finally {
      setGrantsLoading(false);
    }
  }, [selected]);

  useEffect(() => {
    if (grantsOpen) {
      setGrantTarget(null);
      void loadGrants();
    }
  }, [grantsOpen, loadGrants]);

  // 抽屉打开时才拉对应目录（避免页面加载即发无谓请求）；用户走搜索接口取首页
  useEffect(() => {
    if (!grantsOpen) return;
    let cancelled = false;
    (async () => {
      try {
        let opts: { label: string; value: number }[] = [];
        if (grantType === "dept") {
          const { items } = await listDepts();
          opts = items.map((d) => ({ label: d.name, value: d.id }));
        } else if (grantType === "role") {
          const { items } = await listRoles();
          opts = items.map((r) => ({ label: `${r.name}（${r.code}）`, value: r.id }));
        } else {
          const page = await listUsers({ page: 1, page_size: 100 });
          opts = page.items.map((u) => ({ label: `${u.name}（${u.email}）`, value: u.id }));
        }
        if (!cancelled) setGrantOptions(opts);
      } catch {
        if (!cancelled) setGrantOptions([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [grantsOpen, grantType]);

  const submitAddGrant = async () => {
    if (!selected || grantTarget == null) return;
    setGrantAdding(true);
    try {
      await addDatasetGrant(selected.id, grantType, grantTarget);
      setGrantTarget(null);
      await loadGrants();
      void loadAudit();
    } catch (e) {
      message.error(extractDetail(e, "授权失败"));
    } finally {
      setGrantAdding(false);
    }
  };

  const confirmRemoveGrant = async (g: KbGrant) => {
    if (!selected) return;
    try {
      await removeDatasetGrant(selected.id, g.principal_type, g.principal_id);
      await loadGrants();
      void loadAudit();
    } catch (e) {
      message.error(extractDetail(e, "移除失败"));
    }
  };

  const uploadProps = {
    showUploadList: false,
    accept: ".pdf,.docx,.pptx,.xlsx,.txt,.md,.csv,.html,.json",
    customRequest: async (opt: UploadRequestOption) => {
      const file = opt.file as File;
      try {
        await createDocByFile(selected!.id, file, selected!.indexing_technique);
        message.success(`已上传「${file.name}」，正在建立索引`);
        afterWrite();
        opt.onSuccess?.({});
      } catch (e) {
        message.error(extractDetail(e, "上传失败"));
        opt.onError?.(e as Error);
      }
    },
  };

  const submitText = async () => {
    const values = await textForm.validateFields();
    setCreating(true);
    try {
      await createDocByText(selected!.id, {
        name: values.name,
        text: values.text,
        indexing_technique: selected!.indexing_technique,
      });
      message.success("文本已加入索引队列");
      setTextOpen(false);
      textForm.resetFields();
      afterWrite();
    } catch (e) {
      message.error(extractDetail(e, "添加失败"));
    } finally {
      setCreating(false);
    }
  };

  const runHitTest = async () => {
    if (!selected || !hitQuery.trim()) return;
    setHitLoading(true);
    try {
      setHitRecords(await retrieveChunks(selected.id, hitQuery.trim()));
    } catch (e) {
      message.error(extractDetail(e, "检索失败"));
    } finally {
      setHitLoading(false);
    }
  };

  return (
    <div className="admin-page">
      <div className="page-header">
        <span className="page-header-title">知识库</span>
        <span className="page-header-sub">企业文档接入与检索测试（App 绑定知识库请在 Dify 控制台编排页配置）</span>
      </div>

      <div className="admin-toolbar">
        <Button icon={<ReloadOutlined />} onClick={() => void loadDatasets()} loading={datasetsLoading}>
          刷新
        </Button>
        {isAdmin && (
          <Button type="primary" icon={<FileAddOutlined />} onClick={() => setCreateDsOpen(true)}>
            新建知识库
          </Button>
        )}
      </div>

      <div className="admin-table">
        <Table<KbDataset>
          rowKey="id"
          size="middle"
          loading={datasetsLoading}
          dataSource={datasets}
          pagination={false}
          rowClassName={(r) => (r.id === selected?.id ? "ant-table-row-selected" : "")}
          onRow={(r) => ({ onClick: () => setSelected(r), style: { cursor: "pointer" } })}
          locale={{ emptyText: <Empty description="暂无可见知识库（在 Dify 控制台创建并授权全员访问）" /> }}
          columns={[
            { title: "知识库", dataIndex: "name" },
            { title: "文档数", dataIndex: "document_count", width: 90, align: "right" },
            { title: "字数", dataIndex: "word_count", width: 110, align: "right" },
            {
              title: "索引模式",
              dataIndex: "indexing_technique",
              width: 110,
              render: (v: string) => (
                <Tag color={v === "high_quality" ? "teal" : "default"}>
                  {v === "high_quality" ? "高质量" : "经济"}
                </Tag>
              ),
            },
            { title: "创建时间", dataIndex: "created_at", width: 180, render: fmtTime },
            ...(isAdmin
              ? [
                  {
                    title: "",
                    key: "op",
                    width: 60,
                    render: (_: unknown, ds: KbDataset) => (
                      <Popconfirm
                        title={`删除整个知识库「${ds.name}」？文档与向量将一并从 Dify 删除`}
                        onConfirm={() => void confirmDeleteDataset(ds)}
                      >
                        <Button danger size="small" type="text" icon={<DeleteOutlined />} />
                      </Popconfirm>
                    ),
                  },
                ]
              : []),
          ]}
        />
      </div>

      {selected && (
        <>
          <div className="page-header" style={{ marginTop: 12 }}>
            <span className="page-header-title" style={{ fontSize: 15 }}>
              {selected.name} · 文档
            </span>
            <span className="page-header-sub">未完成索引的文档每 5 秒自动刷新</span>
          </div>
          <div className="admin-toolbar">
            {isAdmin && (
              <>
                <Upload {...uploadProps}>
                  <Button type="primary" icon={<UploadOutlined />}>
                    上传文档
                  </Button>
                </Upload>
                <Button icon={<FileAddOutlined />} onClick={() => setTextOpen(true)}>
                  添加文本
                </Button>
              </>
            )}
            {isAdmin && (
              <Button icon={<TeamOutlined />} onClick={() => setGrantsOpen(true)}>
                授权
              </Button>
            )}
            <Button icon={<ReloadOutlined />} onClick={() => void loadDocs()} loading={docsLoading}>
              刷新
            </Button>
          </div>
          <div className="admin-table">
            <Table<KbDocument>
              rowKey="id"
              size="middle"
              loading={docsLoading}
              dataSource={docs}
              pagination={false}
              locale={{ emptyText: <Empty description="暂无文档" /> }}
              columns={[
                { title: "文档", dataIndex: "name", ellipsis: true },
                { title: "字数", dataIndex: "word_count", width: 90, align: "right" },
                { title: "命中", dataIndex: "hit_count", width: 80, align: "right" },
                { title: "索引状态", key: "status", width: 220, render: (_, d) => statusTag(d) },
                { title: "创建时间", dataIndex: "created_at", width: 180, render: fmtTime },
                ...(isAdmin
                  ? [
                      {
                        title: "",
                        key: "op",
                        width: 60,
                        render: (_: unknown, d: KbDocument) => (
                          <Popconfirm
                            title={`删除「${d.name}」？`}
                            onConfirm={async () => {
                              try {
                                await deleteDocument(selected.id, d.id);
                                message.success("已删除");
                                afterWrite();
                              } catch (e) {
                                message.error(extractDetail(e, "删除失败"));
                              }
                            }}
                          >
                            <Button danger size="small" type="text" icon={<DeleteOutlined />} />
                          </Popconfirm>
                        ),
                      },
                    ]
                  : []),
              ]}
            />
          </div>

          <div className="page-header" style={{ marginTop: 12 }}>
            <span className="page-header-title" style={{ fontSize: 15 }}>
              命中测试
            </span>
            <span className="page-header-sub">验证检索质量：返回最相似的分段与得分</span>
          </div>
          <div className="admin-toolbar">
            <Input.Search
              style={{ maxWidth: 480 }}
              placeholder="输入问题，测试该知识库的检索命中"
              value={hitQuery}
              onChange={(e) => setHitQuery(e.target.value)}
              onSearch={() => void runHitTest()}
              enterButton={
                hitLoading ? (
                  "检索中…"
                ) : (
                  <>
                    <SearchOutlined /> 检索
                  </>
                )
              }
            />
          </div>
          {hitRecords !== null && (
            <div className="admin-table">
              {hitRecords.length === 0 ? (
                <Empty description="无命中分段" />
              ) : (
                hitRecords.map((r, i) => (
                  <div
                    key={i}
                    style={{
                      border: "1px solid var(--border, #e2e8f0)",
                      borderRadius: 8,
                      padding: "10px 12px",
                      marginBottom: 8,
                    }}
                  >
                    <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                      <Tag color="teal">score {r.score.toFixed(3)}</Tag>
                      {r.segment.document?.name && (
                        <span style={{ fontSize: 12, color: "#64748B" }}>
                          {r.segment.document.name}
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: 13, marginTop: 6, whiteSpace: "pre-wrap" }}>
                      {r.segment.content}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </>
      )}

      <Modal
        title={`添加文本到「${selected?.name ?? ""}」`}
        open={textOpen}
        onOk={() => void submitText()}
        onCancel={() => setTextOpen(false)}
        confirmLoading={creating}
        destroyOnHidden
      >
        <Form form={textForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="文档名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="如：报销政策摘要" maxLength={200} />
          </Form.Item>
          <Form.Item name="text" label="正文内容" rules={[{ required: true, message: "请输入正文" }]}>
            <Input.TextArea rows={8} placeholder="将按知识库配置自动切分并向量化" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="新建知识库"
        open={createDsOpen}
        onOk={() => void submitCreateDataset()}
        onCancel={() => setCreateDsOpen(false)}
        confirmLoading={creatingDs}
        destroyOnHidden
      >
        <Form
          form={createDsForm}
          layout="vertical"
          style={{ marginTop: 12 }}
          initialValues={{ indexing_technique: "high_quality" }}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="如：产品手册" maxLength={100} />
          </Form.Item>
          <Form.Item name="indexing_technique" label="索引模式" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "high_quality", label: "高质量（向量检索，需 embedding 模型）" },
                { value: "economy", label: "经济（关键词检索，占用小）" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={`授权「${selected?.name ?? ""}」`}
        width={480}
        open={grantsOpen}
        onClose={() => setGrantsOpen(false)}
      >
        <p style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 0 }}>
          租户隔离：未授权的成员看不到此库。可见 = 用户直授 ∪ 部门 ∪ 角色；管理员不受限。
        </p>
        <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          <Select
            style={{ width: 90 }}
            value={grantType}
            onChange={(v) => {
              setGrantType(v as PrincipalType);
              setGrantTarget(null);
            }}
            options={(Object.keys(PRINCIPAL_LABEL) as PrincipalType[]).map((t) => ({
              value: t,
              label: PRINCIPAL_LABEL[t],
            }))}
          />
          <Select
            style={{ flex: 1 }}
            showSearch
            optionFilterProp="label"
            placeholder="选择授权对象"
            value={grantTarget}
            onChange={setGrantTarget}
            options={grantOptions}
          />
          <Button
            type="primary"
            disabled={grantTarget == null}
            loading={grantAdding}
            onClick={() => void submitAddGrant()}
          >
            添加
          </Button>
        </div>
        <Table<KbGrant>
          rowKey={(g) => `${g.principal_type}-${g.principal_id}`}
          size="small"
          loading={grantsLoading}
          dataSource={grants}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无授权（仅管理员可见此库）" /> }}
          columns={[
            {
              title: "类型",
              dataIndex: "principal_type",
              width: 70,
              render: (t: PrincipalType) => <Tag>{PRINCIPAL_LABEL[t] ?? t}</Tag>,
            },
            { title: "名称", key: "name", render: (_, g) => g.name ?? `#${g.principal_id}` },
            {
              title: "",
              key: "op",
              width: 60,
              render: (_, g) => (
                <Popconfirm title="移除该授权？" onConfirm={() => void confirmRemoveGrant(g)}>
                  <Button danger size="small" type="text" icon={<DeleteOutlined />} />
                </Popconfirm>
              ),
            },
          ]}
        />
      </Drawer>

      {isAdmin && (
        <>
          <div className="page-header" style={{ marginTop: 12 }}>
            <span className="page-header-title" style={{ fontSize: 15 }}>
              操作审计
            </span>
            <span className="page-header-sub">建库/删库/文档增删/授权变更全部落库（契约 v9）</span>
          </div>
          <div className="admin-toolbar">
            <Button icon={<ReloadOutlined />} onClick={() => void loadAudit()} loading={auditLoading}>
              刷新
            </Button>
          </div>
          <div className="admin-table">
            <Table<KbAuditItem>
              rowKey="id"
              size="small"
              loading={auditLoading}
              dataSource={audit.items}
              pagination={{ total: audit.total, pageSize: 20, size: "small" }}
              locale={{ emptyText: <Empty description="暂无操作记录" /> }}
              columns={[
                { title: "时间", dataIndex: "created_at", width: 170, render: fmtTime },
                { title: "操作人", key: "user", width: 100, render: (_, a) => a.user ?? "—" },
                { title: "动作", dataIndex: "action", width: 150 },
                {
                  title: "知识库",
                  dataIndex: "dataset_id",
                  width: 150,
                  ellipsis: true,
                  render: (v: string | null) => {
                    const ds = datasets.find((d) => d.id === v);
                    return ds?.name ?? v ?? "—";
                  },
                },
                {
                  title: "详情",
                  dataIndex: "detail",
                  ellipsis: true,
                  render: (v: string | null) => {
                    if (!v) return "—";
                    try {
                      return Object.entries(JSON.parse(v))
                        .map(([k, val]) => `${k}=${String(val)}`)
                        .join(" · ");
                    } catch {
                      return v;
                    }
                  },
                },
              ]}
            />
          </div>
        </>
      )}
    </div>
  );
}

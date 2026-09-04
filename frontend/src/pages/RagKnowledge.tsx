import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button, Card, Drawer, Empty, Input, InputNumber, List, message, Modal, Pagination, Popconfirm,
  Select, Space, Spin, Switch, Tabs, Tag, Typography, Upload,
} from "antd";
import {
  DeleteOutlined, EditOutlined, InboxOutlined, PlusOutlined, RedoOutlined, ReloadOutlined,
  SearchOutlined, TagsOutlined,
} from "@ant-design/icons";
import { extractDetail } from "../api/http";
import ChatSurface, { type ChatTurn } from "../components/ChatSurface";
import Markdown from "../components/Markdown";
import { useAuthStore } from "../stores/auth";
import { useRagStore } from "../stores/rag";
import {
  ragApi, ragSessions, streamRagChat, type RagChatSession, type RagChunk, type RagDocument,
} from "../api/rag";

const RUN_TAG: Record<string, { color: string; text: string }> = {
  UNSTART: { color: "default", text: "待解析" },
  RUNNING: { color: "processing", text: "解析中" },
  DONE: { color: "success", text: "已完成" },
  FAIL: { color: "error", text: "失败" },
  CANCEL: { color: "warning", text: "已取消" },
};

/** 引擎 highlight 的 <em> 转 markdown 强调（Markdown 组件不渲染裸 HTML，XSS 安全）。 */
function highlightToMarkdown(html: string): string {
  return html.replace(/<\/?em>/g, "*").replace(/<[^>]+>/g, "");
}

/** 知识库应用：问答（全高对话 + 引用卡片溯源）+ 管理（store 收敛 + 切片抽屉）。 */
export default function RagKnowledge() {
  const [sessions, setSessions] = useState<RagChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);  // null = 新会话（首条消息时创建）
  const sidRef = useRef<string | null>(null);
  sidRef.current = activeId;
  const openDrawer = useRagStore((s) => s.openDrawer);

  const refreshSessions = useCallback(async () => {
    try {
      const { data } = await ragSessions.list();
      setSessions(data.sessions ?? []);
    } catch { /* 会话列表失败降级：聊天仍可用 */ }
  }, []);

  useEffect(() => { void refreshSessions(); }, [refreshSessions]);

  const persistence = {
    load: async (): Promise<ChatTurn[]> => {
      const sid = sidRef.current;
      if (!sid) return [];
      const { data } = await ragSessions.messages(sid);
      return (data.messages ?? []).map(({ role, content }) => ({ role, content }));
    },
    save: async (turns: ChatTurn[]) => {
      let sid = sidRef.current;
      if (!sid) {
        const first = turns.find((t) => t.role === "user");
        const { data } = await ragSessions.create(first ? first.content.slice(0, 30) : "新会话");
        sid = data.id;
        sidRef.current = sid;
        setActiveId(sid);
      }
      await ragSessions.sync(
        sid,
        turns.map(({ role, content }) => ({ role: role as "user" | "assistant", content })),
      );
      void refreshSessions();
    },
  };

  return (
    <>
      <Tabs
        defaultActiveKey="chat"
        items={[
        {
          key: "chat",
          label: "问答",
          children: (
            <div className="chat-main" style={{ display: "block" }}>
              <Space style={{ padding: "8px 16px 0", width: "100%", justifyContent: "flex-end" }}>
                <Select
                  size="small"
                  style={{ minWidth: 200 }}
                  placeholder="新会话"
                  value={activeId ?? undefined}
                  onChange={(v) => setActiveId(v)}
                  options={sessions.map((s) => ({
                    value: s.id,
                    label: `${s.title}（${s.message_count}条）`,
                  }))}
                />
                <Button size="small" icon={<PlusOutlined />} onClick={() => setActiveId(null)}>新会话</Button>
              </Space>
              <ChatSurface
                key={activeId ?? "new"}
                title="知识库问答"
                description="基于本部门知识库，回答附引用来源（点击来源可查看切片）"
                placeholder="向本部门知识库提问，例如：管道埋深有哪些历史审查问题？"
                persistence={persistence}
                streamAnswer={async (query, history: ChatTurn[], handlers) => {
                  const msgs = [...history, { role: "user", content: query }].map(({ role, content }) => ({ role, content }));
                  await streamRagChat(msgs, handlers);
                }}
                onOpenRef={(ref) => {
                  if (!ref.document_id) return;
                  // 引用卡片 → 切片抽屉（引用来源通常是已解析文档）
                  const doc: RagDocument = { id: ref.document_id, name: ref.document_name ?? ref.document_id, run: "DONE" };
                  void openDrawer(doc, ref.dataset_id).catch((e) =>
                    message.error(extractDetail(e, "切片加载失败")),
                  );
                }}
              />
            </div>
          ),
        },
        { key: "manage", label: "管理", children: <RagManage /> },
        ]}
      />
      <DocDrawer />
    </>
  );
}

/* ---------------- 管理 ---------------- */

function RagManage() {
  const {
    datasets, dsId, docs, loading, searching, results, params,
    loadDatasets, selectDataset, refreshDocs, setParams, search,
  } = useRagStore();
  const openDrawer = useRagStore((s) => s.openDrawer);
  const [uploading, setUploading] = useState(false);
  const [question, setQuestion] = useState("");
  const [discipline, setDiscipline] = useState("");
  const [meta, setMeta] = useState<Record<string, Record<string, string>>>({});
  const [newName, setNewName] = useState("");
  const attemptRef = useRef(0);

  useEffect(() => {
    void loadDatasets().catch((e) => message.error(extractDetail(e, "知识库服务不可用（未开通本部门绑定）")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (dsId) void refreshDocs().catch((e) => message.error(extractDetail(e, "文档列表加载失败")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dsId]);

  /** 条件化退避轮询：仅存在 RUNNING/UNSTART 时；8→15→30s 封顶；页面隐藏时暂停请求。 */
  const pending = docs.some((d) => d.run === "RUNNING" || d.run === "UNSTART");
  useEffect(() => {
    if (!dsId || !pending) { attemptRef.current = 0; return; }
    let alive = true;
    let timer: number | null = null;
    const BACKOFF = [8000, 15000, 30000];
    const schedule = () => {
      if (!alive) return;
      const delay = BACKOFF[Math.min(attemptRef.current, BACKOFF.length - 1)];
      timer = window.setTimeout(() => {
        if (!alive) return;
        if (document.visibilityState !== "visible") { schedule(); return; }
        attemptRef.current += 1;
        void refreshDocs().catch(() => { /* 轮询失败静默，下轮重试 */ });
        schedule();
      }, delay);
    };
    schedule();
    return () => { alive = false; if (timer) window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dsId, pending]);

  const doUpload = async (files: File[]) => {
    setUploading(true);
    try {
      const { data } = await ragApi.upload(dsId, files);
      message.success(`已上传 ${data.accepted?.length ?? 0} 个文件，解析已触发`);
      attemptRef.current = 0;
      void refreshDocs().catch(() => {});
    } catch (e) { message.error(extractDetail(e, "上传失败")); }
    finally { setUploading(false); }
  };

  const doTag = async (docId: string) => {
    try {
      const { data } = await ragApi.tag(dsId, docId);
      setMeta((m) => ({ ...m, [docId]: data.meta_fields }));
      message.success("打标完成");
    } catch (e) { message.error(extractDetail(e, "打标失败（需先完成解析）")); }
  };

  const doDeleteDoc = async (docId: string) => {
    try {
      await ragApi.deleteDocuments(dsId, [docId]);
      message.success("已删除");
      void refreshDocs().catch(() => {});
    } catch (e) { message.error(extractDetail(e, "删除失败")); }
  };

  const doReparse = async (doc: RagDocument) => {
    try {
      await ragApi.parseDocument(dsId, doc.id);
      message.success("已重新触发解析");
      attemptRef.current = 0;
      void refreshDocs().catch(() => {});
    } catch (e) { message.error(extractDetail(e, "重试解析失败")); }
  };

  const doCreateDs = async () => {
    if (!newName.trim()) return;
    try {
      await ragApi.createDataset(newName.trim());
      setNewName(""); message.success("已创建");
      void loadDatasets().catch(() => {});
    } catch (e) { message.error(extractDetail(e, "创建失败")); }
  };

  const doDeleteDs = async () => {
    try {
      await ragApi.deleteDataset(dsId);
      message.success("知识库已删除");
      void loadDatasets("").catch(() => {});
    } catch (e) { message.error(extractDetail(e, "删除失败")); }
  };

  const doSearch = async () => {
    try {
      await search(question, discipline || undefined);
    } catch (e) { message.error(extractDetail(e, "检索失败")); }
  };

  return (
    <div style={{ padding: 16, maxWidth: 1080, margin: "0 auto" }}>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Card size="small" title="知识库（当前部门）" extra={
          <Button size="small" icon={<ReloadOutlined />} onClick={() => (dsId ? void refreshDocs().catch(() => {}) : void loadDatasets().catch(() => {}))}>刷新</Button>
        }>
          <Space wrap>
            <Select style={{ minWidth: 220 }} placeholder="选择知识库" value={dsId || undefined}
              onChange={(v) => void selectDataset(v).catch(() => {})} options={datasets.map((d) => ({ value: d.id, label: d.name }))} />
            <Upload accept=".docx,.pdf,.txt,.md,.csv,.xlsx" multiple showUploadList={false}
              beforeUpload={(_f, files) => {
                if (_f === files[files.length - 1] && files.length) void doUpload(files as unknown as File[]);
                return false;
              }}>
              <Button icon={<InboxOutlined />} loading={uploading} disabled={!dsId}>上传文档</Button>
            </Upload>
            <Popconfirm title="删除整个知识库及其文档？" onConfirm={() => void doDeleteDs()} disabled={!dsId}>
              <Button danger icon={<DeleteOutlined />} disabled={!dsId}>删除库</Button>
            </Popconfirm>
          </Space>
          <div style={{ marginTop: 12 }}>
            <Space>
              <Input size="small" style={{ width: 220 }} placeholder="新建知识库名称" value={newName} onChange={(e) => setNewName(e.target.value)} onPressEnter={() => void doCreateDs()} />
              <Button size="small" icon={<PlusOutlined />} onClick={() => void doCreateDs()}>新建库</Button>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                高级设置（分块/解析策略）在 RAGFlow 后台
              </Typography.Text>
            </Space>
          </div>
        </Card>

        <Card size="small" title={`文档（${docs.length}）`} loading={loading}>
          <List size="small" dataSource={docs} locale={{ emptyText: "暂无文档" }}
            renderItem={(d) => {
              const t = RUN_TAG[d.run] ?? { color: "default", text: d.run };
              return (
                <List.Item actions={[
                  <Button key="open" size="small" type="link" onClick={() =>
                    void openDrawer(d).catch((e) => message.error(extractDetail(e, "切片加载失败")))
                  }>切片</Button>,
                  d.run === "DONE" ? <Button key="t" size="small" icon={<TagsOutlined />} onClick={() => void doTag(d.id)}>打标</Button> : null,
                  (d.run === "FAIL" || d.run === "CANCEL") ? <Button key="r" size="small" icon={<RedoOutlined />} onClick={() => void doReparse(d)}>重试解析</Button> : null,
                  <Popconfirm key="d" title="删除该文档？" onConfirm={() => void doDeleteDoc(d.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                  </Popconfirm>,
                ].filter(Boolean)}>
                  <List.Item.Meta
                    title={
                      <Space>
                        <span style={{ cursor: "pointer" }} onClick={() =>
                          void openDrawer(d).catch((e) => message.error(extractDetail(e, "切片加载失败")))
                        }>{d.name}</span>
                        <Tag color={t.color}>
                          {t.text}
                          {d.run === "RUNNING" && d.progress != null ? ` ${Math.round(d.progress)}%` : ""}
                        </Tag>
                      </Space>
                    }
                    description={d.run === "FAIL" && d.error
                      ? <Typography.Text type="danger" style={{ fontSize: 12 }}>{d.error}</Typography.Text>
                      : meta[d.id]
                        ? Object.entries(meta[d.id]).filter(([, v]) => v).map(([k, v]) => <Tag key={k} color="blue">{k}: {v}</Tag>)
                        : undefined}
                  />
                </List.Item>
              );
            }} />
        </Card>

        <Card size="small" title="检索测试台（参数可调，结果含得分与高亮）">
          <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
            <Input placeholder="例如：管道埋深" value={question} onChange={(e) => setQuestion(e.target.value)} onPressEnter={() => void doSearch()} prefix={<SearchOutlined />} />
            <Select style={{ minWidth: 120 }} allowClear placeholder="专业过滤" value={discipline || undefined}
              onChange={(v) => setDiscipline(v ?? "")}
              options={["给排水", "电气", "暖通", "结构", "市政", "信息化"].map((s) => ({ value: s, label: s }))} />
            <Button type="primary" loading={searching} onClick={() => void doSearch()}>检索</Button>
          </Space.Compact>
          <Space wrap style={{ marginBottom: 12 }}>
            <span style={{ fontSize: 12, color: "var(--text-muted, #888)" }}>返回条数</span>
            <InputNumber size="small" min={1} max={100} value={params.top_n}
              onChange={(v) => setParams({ top_n: v ?? 10 })} />
            <span style={{ fontSize: 12, color: "var(--text-muted, #888)" }}>相似度阈值</span>
            <InputNumber size="small" min={0} max={1} step={0.05} placeholder="默认" value={params.similarity_threshold ?? undefined}
              onChange={(v) => setParams({ similarity_threshold: v ?? undefined })} />
            <span style={{ fontSize: 12, color: "var(--text-muted, #888)" }}>向量权重</span>
            <InputNumber size="small" min={0} max={1} step={0.05} placeholder="默认" value={params.vector_similarity_weight ?? undefined}
              onChange={(v) => setParams({ vector_similarity_weight: v ?? undefined })} />
            <span style={{ fontSize: 12, color: "var(--text-muted, #888)" }}>关键词增强</span>
            <Switch size="small" checked={params.keyword} onChange={(v) => setParams({ keyword: v })} />
            <span style={{ fontSize: 12, color: "var(--text-muted, #888)" }}>高亮</span>
            <Switch size="small" checked={params.highlight} onChange={(v) => setParams({ highlight: v })} />
          </Space>
          {searching ? <Spin /> : (
            <List size="small" dataSource={results} locale={{ emptyText: question ? "无匹配" : "输入问题开始检索" }}
              renderItem={(r: RagChunk) => (
                <List.Item>
                  <div style={{ width: "100%" }}>
                    <Space size={4} wrap>
                      {r.similarity != null && <Tag color="geekblue">{(r.similarity * 100).toFixed(0)}%</Tag>}
                      {r.term_similarity != null && <Tag>语义 {(r.term_similarity * 100).toFixed(0)}%</Tag>}
                      {r.vector_similarity != null && <Tag>向量 {(r.vector_similarity * 100).toFixed(0)}%</Tag>}
                      {r.document_keyword && <Tag color="blue">{r.document_keyword}</Tag>}
                    </Space>
                    <div className="markdown-body" style={{ fontSize: 13 }}>
                      <Markdown content={highlightToMarkdown(r.highlight ?? r.content ?? "")} />
                    </div>
                  </div>
                </List.Item>
              )} />
          )}
        </Card>
      </Space>
    </div>
  );
}

/* ---------------- 切片抽屉（管理行点击 / 问答引用卡片共用） ---------------- */

function DocDrawer() {
  const { drawer, loadChunksPage, closeDrawer } = useRagStore();
  const isAdmin = useAuthStore((s) => s.me?.roles?.includes("PLATFORM_ADMIN") ?? false);
  const [editing, setEditing] = useState<{ id: string; content: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const doc = drawer.doc;

  const doSaveChunk = async () => {
    if (!editing || !drawer.dsId || !doc) return;
    setSaving(true);
    try {
      await ragApi.updateChunk(drawer.dsId, doc.id, editing.id, { content: editing.content });
      message.success("切片已更新");
      setEditing(null);
      void loadChunksPage(drawer.page).catch(() => {});
    } catch (e) {
      message.error(extractDetail(e, "切片更新失败（需管理员权限）"));
    } finally { setSaving(false); }
  };

  const doDeleteChunk = async (chunkId: string) => {
    if (!drawer.dsId || !doc) return;
    try {
      await ragApi.deleteChunk(drawer.dsId, doc.id, chunkId);
      message.success("切片已删除");
      void loadChunksPage(drawer.page).catch(() => {});
    } catch (e) { message.error(extractDetail(e, "切片删除失败（需管理员权限）")); }
  };

  const t = doc ? (RUN_TAG[doc.run] ?? { color: "default", text: doc.run }) : null;
  return (
    <Drawer
      open={!!doc}
      width={560}
      title={doc ? (
        <Space>
          <span>{doc.name}</span>
          {t && (
            <Tag color={t.color}>
              {t.text}
              {doc.run === "RUNNING" && doc.progress != null ? ` ${Math.round(doc.progress)}%` : ""}
            </Tag>
          )}
        </Space>
      ) : null}
      onClose={() => { setEditing(null); closeDrawer(); }}
      destroyOnClose
    >
      {doc?.run === "FAIL" && doc.error && (
        <Typography.Paragraph type="danger" style={{ fontSize: 12 }}>{doc.error}</Typography.Paragraph>
      )}
      {drawer.loading ? (
        <div style={{ textAlign: "center", padding: 48 }}><Spin /></div>
      ) : drawer.chunks.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无切片（需先完成解析）" />
      ) : (
        <>
          <List
            size="small"
            dataSource={drawer.chunks}
            renderItem={(c) => (
              <List.Item actions={isAdmin && c.id ? [
                <Button key="e" size="small" type="text" icon={<EditOutlined />}
                  onClick={() => setEditing({ id: c.id as string, content: c.content ?? "" })} />,
                <Popconfirm key="d" title="删除该切片？" onConfirm={() => void doDeleteChunk(c.id as string)}>
                  <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                </Popconfirm>,
              ] : undefined}>
                <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }} ellipsis={{ rows: 6, expandable: true, symbol: "展开全文" }}>
                  {c.content ?? ""}
                </Typography.Paragraph>
              </List.Item>
            )}
          />
          {drawer.total > 20 && (
            <Pagination
              size="small"
              style={{ textAlign: "center", marginTop: 12 }}
              current={drawer.page}
              total={drawer.total}
              pageSize={20}
              onChange={(p) => void loadChunksPage(p).catch(() => {})}
            />
          )}
        </>
      )}
      <Modal
        open={!!editing}
        title="编辑切片"
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        onOk={() => void doSaveChunk()}
        onCancel={() => setEditing(null)}
      >
        <Input.TextArea rows={10} value={editing?.content ?? ""} onChange={(e) =>
          setEditing((cur) => (cur ? { ...cur, content: e.target.value } : cur))
        } />
      </Modal>
    </Drawer>
  );
}

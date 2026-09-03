import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button, Card, Input, List, message, Popconfirm, Select, Space, Spin, Tabs, Tag, Typography, Upload,
} from "antd";
import {
  DeleteOutlined, InboxOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, TagsOutlined,
} from "@ant-design/icons";
import { extractDetail } from "../api/http";
import { ragApi, ragChatUrl, type RagDataset, type RagDocument } from "../api/rag";

const RUN_TAG: Record<string, { color: string; text: string }> = {
  UNSTART: { color: "default", text: "待解析" },
  RUNNING: { color: "processing", text: "解析中" },
  DONE: { color: "success", text: "已完成" },
  FAIL: { color: "error", text: "失败" },
  CANCEL: { color: "warning", text: "已取消" },
};

interface ChatTurn { role: "user" | "assistant"; content: string; refs?: string[] }

/** 知识库应用：问答（核心）+ 管理。 */
export default function RagKnowledge() {
  return (
    <div style={{ padding: 24, maxWidth: 1080, margin: "0 auto" }}>
      <Typography.Title level={3}>知识库</Typography.Title>
      <Tabs
        defaultActiveKey="chat"
        items={[
          { key: "chat", label: "问答", children: <RagChat /> },
          { key: "manage", label: "管理", children: <RagManage /> },
        ]}
      />
    </div>
  );
}

/* ---------------- 问答 ---------------- */

function RagChat() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const chatIdRef = useRef<string>("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await ragApi.ensureChat();
        chatIdRef.current = data.chat_id;
      } catch (e) {
        message.error(extractDetail(e, "问答服务初始化失败"));
      }
    })();
  }, []);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  const send = async () => {
    const q = input.trim();
    if (!q || busy) return;
    setInput(""); setBusy(true);
    const history = [...turns, { role: "user" as const, content: q }];
    setTurns([...history, { role: "assistant", content: "" }]);
    try {
      const resp = await fetch(ragChatUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ messages: history.map(({ role, content }) => ({ role, content })) }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n"); buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (!payload || payload === "[DONE]") continue;
          try {
            const j = JSON.parse(payload);
            const delta = j.choices?.[0]?.delta;
            if (delta?.content) {
              const refKeys = delta.reference?.chunks ? Object.keys(delta.reference.chunks) : [];
              setTurns((t) => {
                const copy = [...t];
                const last = copy[copy.length - 1];
                copy[copy.length - 1] = {
                  role: "assistant",
                  content: last.content + delta.content,
                  refs: refKeys.length ? refKeys.map((k) => delta.reference.chunks[k]) : last.refs,
                };
                return copy;
              });
            }
          } catch { /* 忽略非 JSON 行 */ }
        }
      }
    } catch (e) {
      message.error(extractDetail(e, "问答失败"));
      setTurns((t) => t.slice(0, -1));
    } finally { setBusy(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "60vh" }}>
      <div style={{ flex: 1, overflowY: "auto", padding: "8px 4px" }}>
        {turns.length === 0 && (
          <Typography.Paragraph type="secondary">
            基于本部门知识库的智能问答，回答附带引用来源。例如：管道埋深有哪些历史审查问题？
          </Typography.Paragraph>
        )}
        <List
          dataSource={turns}
          renderItem={(t) => (
            <List.Item style={{ justifyContent: t.role === "user" ? "flex-end" : "flex-start" }}>
              <Card size="small" style={{ maxWidth: "82%", background: t.role === "user" ? "#e6f4ff" : undefined }}>
                <Typography.Text style={{ whiteSpace: "pre-wrap" }}>{t.content}</Typography.Text>
                {t.refs && t.refs.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>引用：</Typography.Text>
                    {t.refs.slice(0, 3).map((r, i) => (
                      <Tag key={i} style={{ fontSize: 12 }}>{typeof r === "string" ? r.slice(0, 60) : JSON.stringify(r).slice(0, 60)}</Tag>
                    ))}
                  </div>
                )}
              </Card>
            </List.Item>
          )}
        />
        <div ref={bottomRef} />
      </div>
      <Space.Compact style={{ width: "100%" }}>
        <Input
          placeholder="向本部门知识库提问…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={() => void send()}
          disabled={busy || !chatIdRef.current}
        />
        <Button type="primary" loading={busy} onClick={() => void send()}>发送</Button>
      </Space.Compact>
    </div>
  );
}

/* ---------------- 管理 ---------------- */

function RagManage() {
  const [datasets, setDatasets] = useState<RagDataset[]>([]);
  const [dsId, setDsId] = useState("");
  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [meta, setMeta] = useState<Record<string, Record<string, string>>>({});
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [question, setQuestion] = useState("");
  const [discipline, setDiscipline] = useState("");
  const [results, setResults] = useState<{ content: string; similarity: number | null }[]>([]);
  const [searching, setSearching] = useState(false);
  const [newName, setNewName] = useState("");
  const pollRef = useRef<number | null>(null);

  const loadDatasets = useCallback(async () => {
    try {
      const { data } = await ragApi.datasets();
      setDatasets(data.data ?? []);
      if (!dsId && data.data?.[0]) setDsId(data.data[0].id);
    } catch (e) { message.error(extractDetail(e, "知识库服务不可用（未开通本部门绑定）")); }
  }, [dsId]);

  const loadDocs = useCallback(async (id: string) => {
    if (!id) return;
    setLoading(true);
    try {
      const { data } = await ragApi.documents(id);
      setDocs(data.documents ?? []);
    } catch (e) { message.error(extractDetail(e, "文档列表加载失败")); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void loadDatasets(); }, [loadDatasets]);
  useEffect(() => {
    if (dsId) void loadDocs(dsId);
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
  }, [dsId, loadDocs]);

  useEffect(() => {
    const running = docs.some((d) => d.run === "RUNNING" || d.run === "UNSTART");
    if (running && dsId && !pollRef.current) {
      pollRef.current = window.setInterval(() => void loadDocs(dsId), 8000);
    } else if (!running && pollRef.current) {
      window.clearInterval(pollRef.current); pollRef.current = null;
    }
  }, [docs, dsId, loadDocs]);

  const doUpload = async (file: File) => {
    setUploading(true);
    try {
      const { data } = await ragApi.upload(dsId, file);
      message.success(`已上传 ${data.accepted?.length ?? 0} 个文件，解析已触发`);
      void loadDocs(dsId);
    } catch (e) { message.error(extractDetail(e, "上传失败")); }
    finally { setUploading(false); }
    return false;
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
      void loadDocs(dsId);
    } catch (e) { message.error(extractDetail(e, "删除失败")); }
  };

  const doCreateDs = async () => {
    if (!newName.trim()) return;
    try {
      await ragApi.createDataset(newName.trim());
      setNewName(""); message.success("已创建");
      void loadDatasets();
    } catch (e) { message.error(extractDetail(e, "创建失败")); }
  };

  const doDeleteDs = async () => {
    try {
      await ragApi.deleteDataset(dsId);
      message.success("知识库已删除");
      setDsId(""); void loadDatasets();
    } catch (e) { message.error(extractDetail(e, "删除失败")); }
  };

  const doSearch = async () => {
    if (!question.trim() || !dsId) return;
    setSearching(true);
    try {
      const { data } = await ragApi.retrieve(question, [dsId], discipline || undefined);
      setResults((data.chunks ?? []).map((c) => ({ content: c.content ?? "", similarity: c.similarity })));
    } catch (e) { message.error(extractDetail(e, "检索失败")); }
    finally { setSearching(false); }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card size="small" title="知识库（当前部门）" extra={
        <Button size="small" icon={<ReloadOutlined />} onClick={() => (dsId ? void loadDocs(dsId) : void loadDatasets())}>刷新</Button>
      }>
        <Space wrap>
          <Select style={{ minWidth: 220 }} placeholder="选择知识库" value={dsId || undefined}
            onChange={setDsId} options={datasets.map((d) => ({ value: d.id, label: d.name }))} />
          <Upload accept=".docx,.pdf,.txt,.md,.csv,.xlsx" showUploadList={false}
            customRequest={({ file }) => void doUpload(file as unknown as File)}>
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
                d.run === "DONE" ? <Button key="t" size="small" icon={<TagsOutlined />} onClick={() => void doTag(d.id)}>打标</Button> : null,
                <Popconfirm key="d" title="删除该文档？" onConfirm={() => void doDeleteDoc(d.id)}>
                  <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                </Popconfirm>,
              ].filter(Boolean)}>
                <List.Item.Meta
                  title={<Space>{d.name}<Tag color={t.color}>{t.text}</Tag></Space>}
                  description={meta[d.id]
                    ? Object.entries(meta[d.id]).filter(([, v]) => v).map(([k, v]) => <Tag key={k} color="blue">{k}: {v}</Tag>)
                    : undefined}
                />
              </List.Item>
            );
          }} />
      </Card>

      <Card size="small" title="片段检索（可按专业过滤）">
        <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
          <Input placeholder="例如：管道埋深" value={question} onChange={(e) => setQuestion(e.target.value)} onPressEnter={() => void doSearch()} prefix={<SearchOutlined />} />
          <Select style={{ minWidth: 120 }} allowClear placeholder="专业过滤" value={discipline || undefined}
            onChange={(v) => setDiscipline(v ?? "")}
            options={["给排水", "电气", "暖通", "结构", "市政", "信息化"].map((s) => ({ value: s, label: s }))} />
          <Button type="primary" loading={searching} onClick={() => void doSearch()}>检索</Button>
        </Space.Compact>
        {searching ? <Spin /> : (
          <List size="small" dataSource={results} locale={{ emptyText: question ? "无匹配" : "输入问题开始检索" }}
            renderItem={(r) => (
              <List.Item>
                <Typography.Text style={{ whiteSpace: "pre-wrap" }}>
                  {r.similarity != null && <Tag color="geekblue">{(r.similarity * 100).toFixed(0)}%</Tag>}
                  {r.content?.slice(0, 400)}
                </Typography.Text>
              </List.Item>
            )} />
        )}
      </Card>
    </Space>
  );
}

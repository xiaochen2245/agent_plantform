import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button, Card, Input, List, message, Select, Space, Spin, Tag, Typography, Upload,
} from "antd";
import { InboxOutlined, ReloadOutlined, SearchOutlined, TagsOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd";
import { extractDetail } from "../api/http";
import { ragApi, type RagDataset, type RagDocument } from "../api/rag";

const RUN_TAG: Record<string, { color: string; text: string }> = {
  UNSTART: { color: "default", text: "待解析" },
  RUNNING: { color: "processing", text: "解析中" },
  DONE: { color: "success", text: "已完成" },
  FAIL: { color: "error", text: "失败" },
  CANCEL: { color: "warning", text: "已取消" },
};

/** 知识库应用（功能④）：本部门入库 → 解析 → 打标 → 按专业过滤检索。 */
export default function RagKnowledge() {
  const [datasets, setDatasets] = useState<RagDataset[]>([]);
  const [dsId, setDsId] = useState<string>("");
  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [meta, setMeta] = useState<Record<string, Record<string, string>>>({});
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [question, setQuestion] = useState("");
  const [discipline, setDiscipline] = useState("");
  const [results, setResults] = useState<{ content: string; similarity: number | null }[]>([]);
  const [searching, setSearching] = useState(false);
  const pollRef = useRef<number | null>(null);

  const loadDatasets = useCallback(async () => {
    try {
      const { data } = await ragApi.datasets();
      setDatasets(data.data ?? []);
      if (!dsId && data.data?.[0]) setDsId(data.data[0].id);
    } catch (e) {
      message.error(extractDetail(e, "知识库服务不可用（请联系管理员开通本部门绑定）"));
    }
  }, [dsId]);

  const loadDocs = useCallback(async (id: string) => {
    if (!id) return;
    setLoading(true);
    try {
      const { data } = await ragApi.documents(id);
      setDocs(data.documents ?? []);
    } catch (e) {
      message.error(extractDetail(e, "文档列表加载失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadDatasets(); }, [loadDatasets]);
  useEffect(() => {
    if (dsId) void loadDocs(dsId);
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
  }, [dsId, loadDocs]);

  // 解析中的文档自动轮询
  useEffect(() => {
    const hasRunning = docs.some((d) => d.run === "RUNNING" || d.run === "UNSTART");
    if (hasRunning && dsId && !pollRef.current) {
      pollRef.current = window.setInterval(() => void loadDocs(dsId), 8000);
    } else if (!hasRunning && pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [docs, dsId, loadDocs]);

  const doUpload = async (file: File) => {
    if (!dsId) return false;
    setUploading(true);
    try {
      const { data } = await ragApi.upload(dsId, file);
      message.success(`已上传 ${data.accepted?.length ?? 0} 个文件，解析已触发`);
      void loadDocs(dsId);
    } catch (e) {
      message.error(extractDetail(e, "上传失败"));
    } finally {
      setUploading(false);
    }
    return false;
  };

  const doTag = async (docId: string) => {
    try {
      const { data } = await ragApi.tag(dsId, docId);
      setMeta((m) => ({ ...m, [docId]: data.meta_fields }));
      message.success("打标完成");
    } catch (e) {
      message.error(extractDetail(e, "打标失败（文档需先完成解析）"));
    }
  };

  const doSearch = async () => {
    if (!question.trim() || !dsId) return;
    setSearching(true);
    try {
      const { data } = await ragApi.retrieve(question, [dsId], discipline || undefined);
      setResults((data.chunks ?? []).map((c) => ({ content: c.content ?? "", similarity: c.similarity })));
    } catch (e) {
      message.error(extractDetail(e, "检索失败"));
    } finally {
      setSearching(false);
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 1080, margin: "0 auto" }}>
      <Typography.Title level={3}>知识库 · 部门经验资产</Typography.Title>
      <Typography.Paragraph type="secondary">
        上传设计审查单 / 经验反馈表 / 项目文档，自动解析打标后可按专业、项目跨文档检索。
      </Typography.Paragraph>

      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Card size="small" title="知识库（当前部门）" extra={
          <Button size="small" icon={<ReloadOutlined />} onClick={() => dsId ? void loadDocs(dsId) : void loadDatasets()}>刷新</Button>
        }>
          <Space wrap>
            <Select
              style={{ minWidth: 240 }}
              placeholder="选择知识库"
              value={dsId || undefined}
              onChange={setDsId}
              options={datasets.map((d) => ({ value: d.id, label: d.name }))}
            />
            <Upload
              accept=".docx,.pdf,.txt,.md,.csv,.xlsx"
              showUploadList={false}
              customRequest={({ file }) => void doUpload(file as unknown as File)}
            >
              <Button icon={<InboxOutlined />} loading={uploading} disabled={!dsId}>上传文档</Button>
            </Upload>
          </Space>
        </Card>

        <Card size="small" title={`文档（${docs.length}）`} loading={loading}>
          <List
            size="small"
            dataSource={docs}
            locale={{ emptyText: "暂无文档，上传第一批经验文档吧" }}
            renderItem={(d) => {
              const t = RUN_TAG[d.run] ?? { color: "default", text: d.run };
              return (
                <List.Item
                  actions={[
                    d.run === "DONE" ? (
                      <Button key="tag" size="small" icon={<TagsOutlined />} onClick={() => void doTag(d.id)}>打标</Button>
                    ) : null,
                  ].filter(Boolean)}
                >
                  <List.Item.Meta
                    title={<Space>{d.name}<Tag color={t.color}>{t.text}</Tag></Space>}
                    description={
                      meta[d.id]
                        ? Object.entries(meta[d.id]).filter(([, v]) => v).map(([k, v]) => (
                            <Tag key={k} color="blue">{k}: {v}</Tag>
                          ))
                        : undefined
                    }
                  />
                </List.Item>
              );
            }}
          />
        </Card>

        <Card size="small" title="经验检索（可按专业过滤）">
          <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
            <Input
              placeholder="例如：管道埋深有哪些历史问题？"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onPressEnter={() => void doSearch()}
              prefix={<SearchOutlined />}
            />
            <Select
              style={{ minWidth: 140 }}
              allowClear
              placeholder="专业过滤"
              value={discipline || undefined}
              onChange={(v) => setDiscipline(v ?? "")}
              options={["给排水", "电气", "暖通", "结构", "市政", "信息化"].map((s) => ({ value: s, label: s }))}
            />
            <Button type="primary" loading={searching} onClick={() => void doSearch()}>检索</Button>
          </Space.Compact>
          {searching ? <Spin /> : (
            <List
              size="small"
              dataSource={results}
              locale={{ emptyText: question ? "无匹配结果" : "输入问题开始检索" }}
              renderItem={(r) => (
                <List.Item>
                  <Typography.Text style={{ whiteSpace: "pre-wrap" }}>
                    {r.similarity != null && <Tag color="geekblue">{(r.similarity * 100).toFixed(0)}%</Tag>}
                    {r.content?.slice(0, 500)}
                  </Typography.Text>
                </List.Item>
              )}
            />
          )}
        </Card>
      </Space>
    </div>
  );
}

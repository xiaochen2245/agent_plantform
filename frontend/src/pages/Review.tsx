import { useState } from "react";
import {
  Alert, Button, Card, Empty, List, message, Space, Tabs, Tag, Typography, Upload,
} from "antd";
import { FileWordOutlined, UploadOutlined } from "@ant-design/icons";
import { extractDetail } from "../api/http";
import ChatSurface, { type ChatTurn } from "../components/ChatSurface";
import { reviewApi, type ReviewIssue, type ReviewReport, type TypoCandidate } from "../api/review";
import { streamRagChat } from "../api/rag";

const SEVERITY: Record<string, { color: string; text: string }> = {
  error: { color: "error", text: "错误" },
  warn: { color: "warning", text: "提醒" },
};

const TYPE_LABEL: Record<string, string> = {
  font: "字体", size: "字号", alignment: "对齐", numbering: "编号",
};

/** 文档审查应用（功能①）：规则报告 + 错别字候选 + 对报告问答。 */
export default function Review() {
  const [docFile, setDocFile] = useState<File | null>(null);
  const [tplFile, setTplFile] = useState<File | null>(null);
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [typos, setTypos] = useState<TypoCandidate[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkedName, setCheckedName] = useState("");

  const runChecks = async (doc: File, tpl: File) => {
    setChecking(true);
    setReport(null);
    setTypos(null);
    setCheckedName(doc.name);
    try {
      const [r, t] = await Promise.allSettled([
        reviewApi.rules(doc, tpl),
        reviewApi.typos(doc),
      ]);
      if (r.status === "fulfilled") setReport(r.value.data);
      else message.error(extractDetail(r.reason, "规则审查失败"));
      if (t.status === "fulfilled") setTypos(t.value.data.typos);
      else message.warning(extractDetail(t.reason, "错别字检测失败（LLM 通道）"));
    } finally {
      setChecking(false);
    }
  };

  const reportDigest = report && typos
    ? `规则问题 ${report.summary.total_issues} 项（${Object.entries(report.summary.by_type)
        .map(([k, v]) => `${TYPE_LABEL[k] ?? k}${v}`).join("/") || "无"}），错别字候选 ${typos.length} 项。`
    : "";

  return (
    <Tabs
      defaultActiveKey="review"
      items={[
        {
          key: "review",
          label: "审查",
          children: (
            <div style={{ padding: 16, maxWidth: 1080, margin: "0 auto" }}>
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Card size="small" title="上传待审文档与公司模板（均为 docx）">
                  <Space direction="vertical" style={{ width: "100%" }} size="small">
                    <Space wrap>
                      <Upload accept=".docx" maxCount={1} showUploadList={false}
                        beforeUpload={(f) => { setDocFile(f); return false; }}>
                        <Button icon={<UploadOutlined />}>
                          {docFile ? `待审：${docFile.name}` : "选择待审文档"}
                        </Button>
                      </Upload>
                      <Upload accept=".docx" maxCount={1} showUploadList={false}
                        beforeUpload={(f) => { setTplFile(f); return false; }}>
                        <Button icon={<FileWordOutlined />}>
                          {tplFile ? `模板：${tplFile.name}` : "选择公司模板"}
                        </Button>
                      </Upload>
                      <Button type="primary" disabled={!docFile || !tplFile} loading={checking}
                        onClick={() => docFile && tplFile && void runChecks(docFile, tplFile)}>
                        开始审查
                      </Button>
                    </Space>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      规则检查对照模板基准（需两份文件）；错别字检测同步运行（LLM 辅助，不自动改）
                    </Typography.Text>
                    {checking && <Alert type="info" showIcon message="审查进行中（规则即时，错别字经 LLM 约需数秒）…" />}
                    {checkedName && !checking && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        最近审查：{checkedName} {reportDigest && `—— ${reportDigest}`}
                      </Typography.Text>
                    )}
                  </Space>
                </Card>

                <Card size="small" title={reportTitle(report)}>
                  {report === null ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="上传文档后显示规则审查结果" />
                  ) : report.summary.total_issues === 0 ? (
                    <Alert type="success" showIcon message="未发现格式偏差（对照模板基准）" />
                  ) : (
                    <List
                      size="small"
                      dataSource={report.issues}
                      renderItem={(i: ReviewIssue) => <IssueRow key={`${i.type}-${i.paragraph}-${i.message}`} issue={i} />}
                    />
                  )}
                </Card>

                <Card size="small" title={`错别字候选（${typos?.length ?? 0}，LLM 辅助不自动改）`}>
                  {typos === null ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="上传文档后显示错别字候选" />
                  ) : typos.length === 0 ? (
                    <Alert type="success" showIcon message="未发现错别字候选" />
                  ) : (
                    <List
                      size="small"
                      dataSource={typos}
                      renderItem={(t) => (
                        <List.Item>
                          <Space wrap>
                            <Tag color="orange">{t.orig} → {t.suggestion}</Tag>
                            <Tag color={t.confidence >= 0.8 ? "red" : "default"}>置信 {(t.confidence * 100).toFixed(0)}%</Tag>
                            <Tag>第 {t.paragraph} 段</Tag>
                            <Typography.Text type="secondary">{t.context}</Typography.Text>
                          </Space>
                        </List.Item>
                      )}
                    />
                  )}
                </Card>
              </Space>
            </div>
          ),
        },
        {
          key: "chat",
          label: "报告问答",
          children: (
            <ChatSurface
              title="文档审查问答"
              description={reportDigest ? `当前报告：${reportDigest}` : "上传文档后可针对报告提问"}
              placeholder="针对审查报告提问，例如：编号问题集中在哪些段落？"
              streamAnswer={async (query, history: ChatTurn[], handlers) => {
                const context = report || typos
                  ? `【审查报告摘要】${reportDigest}\n` +
                    (report ? `规则问题：${JSON.stringify(report.issues.slice(0, 30))}\n` : "") +
                    (typos ? `错别字候选：${JSON.stringify(typos.slice(0, 30))}\n` : "")
                  : "";
                const msgs = [
                  ...(context ? [{ role: "system" as const, content: `以下是当前文档审查报告，回答时以此为准：\n${context.slice(0, 6000)}` }] : []),
                  ...history.map(({ role, content }) => ({ role, content })),
                  { role: "user" as const, content: query },
                ];
                await streamRagChat(msgs, handlers);
              }}
            />
          ),
        },
      ]}
    />
  );
}

function reportTitle(report: ReviewReport | null): string {
  if (!report) return "规则审查（对照公司模板）";
  const parts = Object.entries(report.summary.by_type)
    .map(([k, v]) => `${TYPE_LABEL[k] ?? k} ${v}`);
  return `规则审查（对照公司模板）：共 ${report.summary.total_issues} 项${parts.length ? ` —— ${parts.join("，")}` : ""}`;
}

function IssueRow({ issue }: { issue: ReviewIssue }) {
  const sev = SEVERITY[issue.severity] ?? SEVERITY.warn;
  return (
    <List.Item>
      <Space wrap>
        <Tag color={sev.color}>{sev.text}</Tag>
        <Tag color="blue">{TYPE_LABEL[issue.type] ?? issue.type}</Tag>
        <Tag>第 {issue.paragraph} 段</Tag>
        <Typography.Text>{issue.message}</Typography.Text>
        {issue.expected && issue.actual && (
          <Typography.Text type="secondary">应为「{issue.expected}」，实际「{issue.actual}」</Typography.Text>
        )}
        <Typography.Text type="secondary" ellipsis style={{ maxWidth: 200 }}>{issue.text}</Typography.Text>
      </Space>
    </List.Item>
  );
}

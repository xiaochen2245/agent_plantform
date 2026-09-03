import { useEffect, useState } from "react";
import { Card, Col, Row, Spin, Tag, Typography, message } from "antd";
import {
  AuditOutlined, FileSearchOutlined, FolderOutlined, RobotOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { http } from "../api/http";
import { extractDetail } from "../api/http";

interface AppInfo {
  id: number;
  name: string;
  description: string;
  mode: string;
}

const ICONS: Record<string, React.ReactNode> = {
  kb: <FolderOutlined />,
  review: <AuditOutlined />,
  compare: <FileSearchOutlined />,
  generate: <RobotOutlined />,
};

const ROADMAP: Record<string, { status: string; color: string }> = {
  kb: { status: "已上线", color: "success" },
  review: { status: "W4 开发中", color: "processing" },
  compare: { status: "W5-W7", color: "processing" },
  generate: { status: "P2 规划", color: "default" },
};

/** 门户首页：四大基础应用入口。 */
export default function Home() {
  const [apps, setApps] = useState<AppInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    (async () => {
      try {
        const { data } = await http.get<{ apps: AppInfo[] }>("/apps/me");
        setApps(data.apps ?? []);
      } catch (e) {
        message.error(extractDetail(e, "应用列表加载失败"));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const enter = (mode: string) => {
    if (mode === "kb") navigate("/kb");
    else navigate(`/apps/${mode}`);
  };

  return (
    <div style={{ padding: 24, maxWidth: 1080, margin: "0 auto" }}>
      <Typography.Title level={3}>企业知识平台</Typography.Title>
      <Typography.Paragraph type="secondary">
        四大基础应用 · 2026-11-30 上线目标 · 知识资产按部门隔离
      </Typography.Paragraph>
      {loading ? <Spin /> : (
        <Row gutter={[16, 16]}>
          {apps.map((a) => {
            const road = ROADMAP[a.mode] ?? { status: "", color: "default" };
            return (
              <Col xs={24} sm={12} key={a.id}>
                <Card
                  hoverable
                  onClick={() => enter(a.mode)}
                  title={<span>{ICONS[a.mode]} {a.name}</span>}
                  extra={<Tag color={road.color}>{road.status}</Tag>}
                >
                  <Typography.Text type="secondary">{a.description}</Typography.Text>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}
    </div>
  );
}

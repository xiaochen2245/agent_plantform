import {
  CodeOutlined,
  CloudServerOutlined,
  FileTextOutlined,
  LockOutlined,
  MailOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { Alert, Button, Checkbox, Form, Input } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { extractDetail } from "../api/http";
import Logomark from "../components/Logomark";
import { useAuthStore } from "../stores/auth";

interface LoginForm {
  email: string;
  password: string;
  remember: boolean;
}

const CAPABILITIES = [
  {
    icon: <CloudServerOutlined />,
    title: "IT 运维问答",
    desc: "服务器、网络与账号问题，即时诊断",
  },
  {
    icon: <FileTextOutlined />,
    title: "报销政策查询",
    desc: "差旅与报销规则，一条消息问清楚",
  },
  {
    icon: <CodeOutlined />,
    title: "代码评审助手",
    desc: "提交前预审，指出隐患与规范偏差",
  },
];

/** 登录页：视觉规范 .stitch/designs/login-agent-portal-v3.html。 */
export default function Login() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const status = useAuthStore((s) => s.status);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "authenticated") navigate("/", { replace: true });
  }, [status, navigate]);

  const onFinish = async (values: LoginForm) => {
    setError(null);
    try {
      await login(values.email, values.password);
      navigate("/", { replace: true });
    } catch (e) {
      setError(extractDetail(e, "登录失败，请稍后重试"));
    }
  };

  return (
    <div className="login-shell">
      {/* 品牌栏 */}
      <div className="login-brand">
        <div className="login-dotgrid" />
        <svg className="login-motif" viewBox="0 0 560 560" fill="none" aria-hidden="true">
          <rect x="120" y="120" width="320" height="320" rx="72" stroke="#0F766E" strokeOpacity="0.10" strokeWidth="1.5" />
          <rect x="170" y="170" width="220" height="220" rx="52" stroke="#0F766E" strokeOpacity="0.14" strokeWidth="1.5" strokeDasharray="3 7" />
          <circle cx="290" cy="290" r="58" stroke="#14B8A6" strokeOpacity="0.35" strokeWidth="1.5" />
          <circle cx="290" cy="290" r="7" fill="#14B8A6" fillOpacity="0.55" />
          <circle cx="440" cy="290" r="4" fill="#0F766E" fillOpacity="0.35" />
        </svg>

        <div style={{ display: "flex", alignItems: "center", gap: 12, position: "relative" }}>
          <Logomark size={40} />
          <div>
            <div className="font-display" style={{ fontSize: 17, fontWeight: 700, letterSpacing: "-0.01em", lineHeight: 1 }}>
              Agent 平台
            </div>
            <div style={{ marginTop: 6, fontSize: 10, fontWeight: 600, letterSpacing: "0.18em", color: "var(--text-muted)" }}>
              ENTERPRISE&nbsp;AI&nbsp;WORKSPACE
            </div>
          </div>
        </div>

        <div style={{ position: "relative", maxWidth: 440 }}>
          <div className="eyebrow" style={{ marginBottom: 20 }}>一个入口 · 问所有事</div>
          <h1 className="login-headline font-display">
            让每位员工，
            <br />
            都有一位<span className="accent"> AI 同事</span>。
          </h1>
          <p className="login-sub">
            企业智能助手平台 —— 汇聚运维、财务、研发的领域知识，
            <br />
            以统一的对话入口，秒级响应日常工作问题。
          </p>
          {CAPABILITIES.map((cap) => (
            <div className="cap-row" key={cap.title}>
              <div className="cap-tile">{cap.icon}</div>
              <div>
                <div className="cap-title">{cap.title}</div>
                <div className="cap-desc">{cap.desc}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="login-panel-footer">
          <SafetyCertificateOutlined style={{ fontSize: 14 }} />
          仅限公司内网访问 · 数据不出企业边界
        </div>
      </div>

      {/* 登录卡 */}
      <div className="login-auth">
        <div className="login-card">
          <div className="eyebrow">欢迎回来</div>
          <h2 className="login-title font-display">登录</h2>

          {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 20 }} closable onClose={() => setError(null)} />}

          <Form<LoginForm> layout="vertical" onFinish={onFinish} initialValues={{ remember: true }} requiredMark={false}>
            <Form.Item
              name="email"
              label="企业邮箱"
              rules={[
                { required: true, message: "请输入企业邮箱" },
                { type: "email", message: "邮箱格式不正确" },
              ]}
            >
              <Input size="large" prefix={<MailOutlined style={{ color: "var(--text-muted)" }} />} placeholder="yourname@company.com" />
            </Form.Item>

            <Form.Item
              name="password"
              label="密码"
              rules={[{ required: true, message: "请输入密码" }, { min: 6, message: "密码至少 6 位" }]}
              style={{ marginBottom: 16 }}
            >
              <Input.Password size="large" prefix={<LockOutlined style={{ color: "var(--text-muted)" }} />} placeholder="••••••••" />
            </Form.Item>

            <Form.Item name="remember" valuePropName="checked" style={{ marginBottom: 20 }}>
              <Checkbox>记住我</Checkbox>
            </Form.Item>

            <Form.Item style={{ marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" size="large" block loading={status === "loading"}>
                登&nbsp;录
              </Button>
            </Form.Item>
          </Form>

          <div className="divider-or"><span>或</span></div>
          <button type="button" className="sso-placeholder" disabled>
            企业统一认证登录（即将上线）
          </button>

          <p className="login-footnote">
            仅限公司内部员工使用 · 遇到问题联系
            <span style={{ color: "#64748B", fontWeight: 500 }}> IT 服务台</span>
          </p>
        </div>
      </div>
    </div>
  );
}

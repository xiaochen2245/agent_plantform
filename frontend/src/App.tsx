import { Navigate, Route, Routes, Outlet } from "react-router-dom";
import { useEffect, type ReactNode } from "react";
import { Spin } from "antd";
import { useAuthStore } from "./stores/auth";
import AppShell from "./components/AppShell";
import Login from "./pages/Login";
import Chat from "./pages/Chat";
import History from "./pages/History";
import Admin from "./pages/Admin";
import ComingSoon from "./pages/ComingSoon";
import VersionBanner from "./components/VersionBanner";

/** 会话引导只做一次（模块标志防 StrictMode 双触发/重入） */
let sessionBootstrapped = false;

function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status);
  const fetchMe = useAuthStore((s) => s.fetchMe);

  // A1：idle 时引导会话（F5/直链不再误踢）；/login 不经此组件永不阻塞。
  useEffect(() => {
    if (status === "idle" && !sessionBootstrapped) {
      sessionBootstrapped = true;
      void fetchMe();
    }
  }, [status, fetchMe]);

  if (status === "authenticated") return <>{children}</>;
  if (status === "idle" || status === "loading") {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 12, height: "100vh", alignItems: "center", justifyContent: "center" }}>
        <Spin size="large" />
        <span style={{ color: "#64748B", fontSize: 13 }}>正在恢复会话…</span>
      </div>
    );
  }
  return <Navigate to="/login" replace />;
}

/** 管理端守卫：非 PLATFORM_ADMIN 访问 /admin 一律弹回首页。 */
function RequireAdmin() {
  const me = useAuthStore((s) => s.me);
  if (me?.roles?.includes("PLATFORM_ADMIN")) return <Outlet />;
  return <Navigate to="/" replace />;
}

export default function App() {
  return (
    <>
      <VersionBanner />
      <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Chat />} />
        <Route path="/history" element={<History />} />
        <Route element={<RequireAdmin />}>
          <Route path="/admin" element={<Admin />} />
        </Route>
        {/* 编辑端占位（本切片不展开） */}
        <Route path="/workbench" element={<ComingSoon title="Agent 工作台" description="创建、编排与发布企业 Agent —— 已纳入编辑端规划。" />} />
        <Route path="/workflows" element={<ComingSoon title="工作流编排" description="节点画布：知识检索 → LLM → 条件分支的确定性流程编排。" />} />
        <Route path="/kb" element={<ComingSoon title="知识库" description="企业文档接入、切分与检索配置。" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </>
  );
}

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "./styles/global.css";

import { USE_MOCKS } from "./config";

async function enableMocking() {
  // 仅 dev 且未被 VITE_USE_MOCKS=false 显式关闭时启用 MSW；
  // 生产构建（DEV=false）下此分支恒假 → 无 mock 泄漏。
  if (!import.meta.env.DEV || !USE_MOCKS) return;
  const { worker } = await import("./mocks/browser");
  await worker.start({ onUnhandledRequest: "bypass" });
}

const theme = {
  token: {
    colorPrimary: "#0F766E",
    borderRadius: 8,
    fontFamily: '"Inter", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
    colorText: "#0F172A",
    colorBgLayout: "#F8FAFC",
    colorBorder: "#E2E8F0",
    colorLink: "#0F766E",
  },
};

enableMocking().then(() => {
  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <ConfigProvider locale={zhCN} theme={theme}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ConfigProvider>
    </React.StrictMode>
  );
});

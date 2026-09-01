import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "./styles/global.css";

async function enableMocking() {
  if (!import.meta.env.DEV) return;
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

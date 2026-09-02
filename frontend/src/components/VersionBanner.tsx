import { useEffect, useState } from "react";
import { Alert } from "antd";
import { fetchServerVersion, isStale, VERSION_CHECK_INTERVAL_MS } from "../version";

/**
 * 版本陈旧提示条：boot + 每 15 分钟比对 /api/version；
 * 陈旧时顶部非阻断提示（fixed），点击「立即刷新」location.reload()。
 * 不自动强刷——不打断用户正在进行的对话。
 */
export default function VersionBanner() {
  const [stale, setStale] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      const server = await fetchServerVersion();
      if (!cancelled) setStale(isStale(server));
    };
    void check();
    const timer = setInterval(() => void check(), VERSION_CHECK_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (!stale) return null;

  return (
    <div
      data-testid="version-banner"
      style={{
        position: "fixed",
        top: 12,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 1200,
        boxShadow: "0 8px 24px -8px rgba(15,23,42,.25)",
        borderRadius: 10,
        overflow: "hidden",
      }}
    >
      <Alert
        type="warning"
        showIcon
        message="系统已更新"
        description="检测到新版本，刷新后生效。"
        action={
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              window.location.reload();
            }}
            style={{ fontWeight: 600, whiteSpace: "nowrap" }}
          >
            立即刷新
          </a>
        }
      />
    </div>
  );
}

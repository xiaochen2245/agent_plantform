import { Button, Checkbox, Drawer, message } from "antd";
import { useEffect, useState } from "react";
import { extractDetail } from "../../api/http";
import { useChatStore } from "../../stores/chat";

interface Props {
  open: boolean;
  title: string;
  /** 加载当前授权；返回 app_id 列表。 */
  load: () => Promise<number[]>;
  /** 保存（已做去重的 app_ids）。 */
  save: (appIds: number[]) => Promise<void>;
  hint?: string;
  onClose: () => void;
}

/** 三态（user/dept/role）共享的 Agent 授权抽屉：复选框 + 全量替换。 */
export default function AuthorizationsDrawer({
  open,
  title,
  load,
  save,
  hint,
  onClose,
}: Props) {
  const apps = useChatStore((s) => s.apps);
  const loadApps = useChatStore((s) => s.loadApps);
  const [selected, setSelected] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (apps.length === 0) void loadApps();
  }, [apps.length, loadApps]);

  useEffect(() => {
    if (!open) return;
    setSelected([]);
    setLoading(true);
    load()
      .then(setSelected)
      .catch(() => message.error("授权信息加载失败"))
      .finally(() => setLoading(false));
  }, [open, load]);

  async function onSave() {
    setSaving(true);
    try {
      await save(selected);
      message.success("已保存授权");
      onClose();
    } catch (e) {
      message.error(extractDetail(e, "授权保存失败"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Drawer
      title={title}
      width={420}
      open={open}
      onClose={onClose}
      footer={
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} disabled={loading} onClick={() => void onSave()}>
            保存
          </Button>
        </div>
      }
    >
      {hint && (
        <p style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 0 }}>{hint}</p>
      )}
      {loading ? (
        <span style={{ color: "var(--text-muted)", fontSize: 12.5 }}>加载当前授权…</span>
      ) : (
        <Checkbox.Group
          style={{ display: "flex", flexDirection: "column", gap: 14 }}
          value={selected}
          onChange={(vals) => setSelected(vals as number[])}
          options={apps.map((a) => ({ label: `${a.name} — ${a.description}`, value: a.id }))}
        />
      )}
    </Drawer>
  );
}

import { create } from "zustand";
import { http } from "../api/http";
import { sendChatStream } from "../api/sse";
import type { AppInfo, ChatMessage, ConversationSummary, UploadedFile } from "../types";
let seq = 0;
function nextId(prefix: string): string {
  seq += 1;
  return `${prefix}-${Date.now()}-${seq}`;
}

/** 首轮发送的本地消息桶：真实会话 id 由后端 agent_done 回传后认领迁移 */
const DRAFT_KEY = "__draft__";

interface ChatState {
  apps: AppInfo[];
  appsLoading: boolean;
  activeAppId: number | null;

  conversationsByApp: Record<string, ConversationSummary[]>;
  messagesByConv: Record<string, ChatMessage[]>;
  activeConversationId: string | null;
  streaming: boolean;

  loadApps: () => Promise<void>;
  loadConversations: (appId: number) => Promise<ConversationSummary[]>;
  resumeConversation: (appId: number, conversationId: string) => void;
  /** F3：恢复该应用最近一次会话（拉详情灌桶并激活）；无会话返回 false */
  resumeLatest: (appId: number) => Promise<boolean>;
  setActiveApp: (appId: number) => void;
  messagesOfActive: () => ChatMessage[];
  activeApp: () => AppInfo | null;
  sendMessage: (query: string, inputs?: Record<string, string>, files?: UploadedFile[]) => Promise<void>;
  retryLast: () => Promise<void>;
  stopStreaming: () => void;
}

let abortController: AbortController | null = null;
/** 最近一次发送的 workflow inputs / 附件：重试时保真 */
let lastInputs: Record<string, string> | undefined;
let lastFiles: UploadedFile[] | undefined;

export const useChatStore = create<ChatState>((set, get) => ({
  apps: [],
  appsLoading: false,
  activeAppId: null,
  conversationsByApp: {},
  messagesByConv: {},
  activeConversationId: null,
  streaming: false,

  async loadApps() {
    set({ appsLoading: true });
    try {
      const data = (await http.get<{ apps: AppInfo[] }>("/apps/me")).data;
      const apps = data.apps ?? [];
      set({
        apps,
        activeAppId: get().activeAppId ?? apps[0]?.id ?? null,
      });
    } finally {
      set({ appsLoading: false });
    }
  },

  async loadConversations(appId) {
    const data = (await http.get<{ items: ConversationSummary[] }>("/conversations", { params: { app_id: appId } })).data;
    const remote = data.items ?? [];
    // 远端为真相源，但本地新建会话（后端尚未落库/刷新前）保留在前
    set((s) => {
      const local = s.conversationsByApp[String(appId)] ?? [];
      const localOnly = local.filter((c) => !remote.some((r) => r.id === c.id));
      return { conversationsByApp: { ...s.conversationsByApp, [String(appId)]: [...localOnly, ...remote] } };
    });
    return get().conversationsByApp[String(appId)] ?? [];
  },

  resumeConversation(appId, conversationId) {
    set({ activeAppId: appId, activeConversationId: conversationId });
  },

  async resumeLatest(appId) {
    // 容错语义：恢复失败（网络/后端抖动）＝不恢复，绝不阻断对话页
    try {
      // 直接取远端列表（避免与本地摘要合并噪声），最新在前（后端按 updated_at desc）
      const data = (
        await http.get<{ items: ConversationSummary[] }>("/conversations", { params: { app_id: appId } })
      ).data;
      const latest = (data.items ?? [])[0];
      if (!latest) return false;
      const detail = (
        await http.get<{ messages: Array<{
          id: number;
          role: "user" | "assistant";
          content: string;
          created_at: string;
          files?: UploadedFile[] | null;
          reasoning?: string | null;
        }> }>(`/conversations/${latest.id}/messages`)
      ).data;
      const msgs: ChatMessage[] = (detail.messages ?? []).map((m) => ({
        id: `srv-${latest.id}-${m.id}`,
        conversationId: latest.id,
        role: m.role,
        content: m.content,
        status: "done",
        files: m.files && m.files.length > 0 ? m.files : null,
        reasoning: m.reasoning ?? null,
        createdAt: Date.parse(m.created_at) || Date.now(),
      }));
      set((s) => ({
        activeAppId: appId,
        activeConversationId: latest.id,
        messagesByConv: { ...s.messagesByConv, [latest.id]: msgs },
      }));
      return true;
    } catch {
      return false;
    }
  },

  setActiveApp(appId) {
    set({ activeAppId: appId, activeConversationId: null });
  },

  activeApp() {
    const { apps, activeAppId } = get();
    return apps.find((a) => a.id === activeAppId) ?? null;
  },

  messagesOfActive() {
    const { messagesByConv, activeConversationId } = get();
    // 首轮发送（后端尚未回传真实会话 id）挂在草稿桶，认领后自动迁移
    return messagesByConv[activeConversationId ?? DRAFT_KEY] ?? [];
  },

  async sendMessage(query, inputs, files) {
    const state = get();
    const app = state.activeApp();
    if (!app || state.streaming || !query.trim()) return;
    lastInputs = inputs;
    lastFiles = files && files.length > 0 ? files : undefined;

    // 会话 id 语义（契约 v5）：空串=新建；真实 UUID 由后端 agent_done 回传，前端不再伪造
    const existingId = state.activeConversationId;
    const conversationId = existingId ?? DRAFT_KEY;
    const sendConversationId = existingId ?? "";

    const userMsg: ChatMessage = {
      id: nextId("msg"),
      conversationId,
      role: "user",
      content: query.trim(),
      status: "done",
      files: files && files.length > 0 ? files : null,
      createdAt: Date.now(),
    };
    const assistantMsg: ChatMessage = {
      id: nextId("msg"),
      conversationId,
      role: "assistant",
      content: "",
      status: "streaming",
      createdAt: Date.now(),
    };
    const append = (msgs: ChatMessage[]) =>
      set((s) => ({
        messagesByConv: { ...s.messagesByConv, [conversationId as string]: [...(s.messagesByConv[conversationId as string] ?? []), ...msgs] },
      }));
    // F2：流式增量节流缓冲 —— 逐 token 全量 patch 是 O(n²) 重渲染卡顿根源；
    // 增量先进缓冲，~80ms 合批刷一次（终态事件强制刷余量）
    const pending = { delta: "", reasoning: "" };
    let flushTimer: ReturnType<typeof setInterval> | null = null;

    // adopt 后消息桶会迁移到真实 id 键：按 assistantMsg.id 动态定位桶，避免写孤儿草稿桶
    const patchAssistant = (
      patch: Partial<ChatMessage> | ((m: ChatMessage) => Partial<ChatMessage>)
    ) =>
      set((s) => {
        const entry = Object.entries(s.messagesByConv).find(([, list]) =>
          (list as ChatMessage[]).some((m) => m.id === assistantMsg.id)
        );
        if (!entry) return {};
        const [key, list] = entry as [string, ChatMessage[]];
        return {
          messagesByConv: {
            ...s.messagesByConv,
            [key]: list.map((m) =>
              m.id === assistantMsg.id ? { ...m, ...(typeof patch === "function" ? patch(m) : patch) } : m
            ),
          },
        };
      });

    const flushNow = () => {
      if (!pending.delta && !pending.reasoning) return;
      const d = pending.delta;
      const r = pending.reasoning;
      pending.delta = "";
      pending.reasoning = "";
      patchAssistant((m) => ({
        content: (m.content ?? "") + d,
        ...(r ? { reasoning: (m.reasoning ?? "") + r } : {}),
      }));
    };
    const stopFlushTimer = () => {
      if (flushTimer !== null) {
        clearInterval(flushTimer);
        flushTimer = null;
      }
      flushNow();
    };

    append([userMsg, assistantMsg]);
    set({ streaming: true });
    abortController = new AbortController();
    // A7：stopStreaming 会置空 abortController，捕获信号供 catch 判定中断来源
    const sendSignal = abortController.signal;

    const finish = (realId?: string) => {
      stopFlushTimer(); // 终态：停节流并刷余量
      abortController = null;
      set({ streaming: false });
      // A8：草稿桶且未拿到真实 id 时不落本地摘要（后端已建会话，列表刷新自然带回）——消灭幽灵会话
      if (conversationId === DRAFT_KEY && !realId) return;
      // 会话摘要落本地（标题=首条问题截断，契约 Conversations 段）
      const summaryId = realId ?? get().activeConversationId ?? conversationId;
      const list = get().messagesByConv[conversationId] ?? [];
      const summary: ConversationSummary = {
        id: summaryId,
        title: query.trim().slice(0, 20),
        message_count: list.length,
        updated_at: new Date().toISOString(),
      };
      set((s) => ({
        conversationsByApp: {
          ...s.conversationsByApp,
          [String(app.id)]: [
            summary,
            ...(s.conversationsByApp[String(app.id)] ?? []).filter((c) => c.id !== summary.id),
          ],
        },
      }));
    };

    /** 首轮发送后认领后端回传的真实会话 id：迁移消息桶；仅当用户仍在本会话时才激活（A8） */
    const adopt = (realId: string) => {
      if (existingId || !realId || realId === conversationId) return;
      set((s) => {
        const bucket = s.messagesByConv[conversationId] ?? [];
        const messagesByConv = { ...s.messagesByConv };
        delete messagesByConv[conversationId];
        // 用户已切走（换应用/进其他会话）→ 只迁桶不拽回，避免打断新上下文
        const stillHere =
          s.activeAppId === app.id &&
          (s.activeConversationId === null || s.activeConversationId === conversationId);
        return {
          messagesByConv: { ...messagesByConv, [realId]: bucket },
          ...(stillHere ? { activeConversationId: realId } : {}),
        };
      });
    };

    await sendChatStream(
      { app_id: app.id, query: query.trim(), conversation_id: sendConversationId, inputs, files: lastFiles?.map((f) => f.file_id) },
      {
        onMessage: (delta) => {
          pending.delta += delta;
          if (flushTimer === null) flushTimer = setInterval(flushNow, 80);
        },
        onReasoning: (delta) => {
          // 契约 v6：思考增量进同一节流缓冲，与正文分开累计
          pending.reasoning += delta;
          if (flushTimer === null) flushTimer = setInterval(flushNow, 80);
        },
        onMessageEnd: (usage) => patchAssistant({ usage }),
        onError: (message, kind) => {
          // 错误卡整体替换内容：丢弃缓冲余量，避免半截正文混入错误文案
          pending.delta = "";
          pending.reasoning = "";
          patchAssistant({ status: "error", content: message, errorKind: kind });
        },
        onAgentDone: (data) => {
          // A3：错误态不被 agent_done 覆盖 —— 否则生产环境错误卡永远不可见
          const current = get().messagesByConv[conversationId] ?? [];
          const m = current.find((x) => x.id === assistantMsg.id);
          if (m?.status !== "error") patchAssistant({ status: "done" });
          adopt(data?.conversation_id ?? "");
          finish(data?.conversation_id);
        },
      },
      abortController.signal
    ).catch((err: unknown) => {
      // A7：用户主动停止 → 保留已生成部分答案并标 done；仅真异常才错误卡
      const aborted = sendSignal.aborted || (err as Error | undefined)?.name === "AbortError";
      if (aborted) {
        patchAssistant({ status: "done" });
      } else {
        patchAssistant({ status: "error", content: "连接中断，请重试", errorKind: "generic" });
      }
      finish();
    });
    if (get().streaming) finish(); // 流自然结束但未见 agent_done 的兜底
  },

  async retryLast() {
    const { activeConversationId, messagesByConv } = get();
    if (!activeConversationId) return;
    const list = messagesByConv[activeConversationId] ?? [];
    const lastUser = [...list].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    // 移除末尾的错误 assistant 消息后重发
    set((s) => ({
      messagesByConv: {
        ...s.messagesByConv,
        [activeConversationId]: (s.messagesByConv[activeConversationId] ?? []).filter(
          (m) => m.status !== "error"
        ),
      },
    }));
    await get().sendMessage(lastUser.content, lastInputs, lastFiles);
  },

  stopStreaming() {
    abortController?.abort();
    abortController = null;
    set({ streaming: false });
  },
}));

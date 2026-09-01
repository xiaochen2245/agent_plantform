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
    const patchAssistant = (patch: Partial<ChatMessage>) =>
      set((s) => {
        const list = s.messagesByConv[conversationId as string] ?? [];
        return {
          messagesByConv: {
            ...s.messagesByConv,
            [conversationId as string]: list.map((m) => (m.id === assistantMsg.id ? { ...m, ...patch } : m)),
          },
        };
      });

    append([userMsg, assistantMsg]);
    set({ streaming: true });
    abortController = new AbortController();

    const finish = (realId?: string) => {
      abortController = null;
      set({ streaming: false });
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

    /** 首轮发送后认领后端回传的真实会话 id：迁移消息桶 + 激活 */
    const adopt = (realId: string) => {
      if (existingId || !realId || realId === conversationId) return;
      set((s) => {
        const bucket = s.messagesByConv[conversationId] ?? [];
        const messagesByConv = { ...s.messagesByConv };
        delete messagesByConv[conversationId];
        return {
          messagesByConv: { ...messagesByConv, [realId]: bucket },
          activeConversationId: realId,
        };
      });
    };

    await sendChatStream(
      { app_id: app.id, query: query.trim(), conversation_id: sendConversationId, inputs, files: lastFiles?.map((f) => f.file_id) },
      {
        onMessage: (delta) => {
          const current = get().messagesByConv[conversationId as string] ?? [];
          const m = current.find((x) => x.id === assistantMsg.id);
          patchAssistant({ content: (m?.content ?? "") + delta });
        },
        onReasoning: (delta) => {
          // 契约 v6：思考增量累加，与正文分开累计
          const current = get().messagesByConv[conversationId as string] ?? [];
          const m = current.find((x) => x.id === assistantMsg.id);
          patchAssistant({ reasoning: (m?.reasoning ?? "") + delta });
        },
        onMessageEnd: (usage) => patchAssistant({ usage }),
        onError: (message, kind) => {
          patchAssistant({ status: "error", content: message, errorKind: kind });
        },
        onAgentDone: (data) => {
          patchAssistant({ status: "done" });
          adopt(data?.conversation_id ?? "");
          finish(data?.conversation_id);
        },
      },
      abortController.signal
    ).catch(() => {
      // 网络/中断异常兜底：标记错误态并收尾
      patchAssistant({ status: "error", content: "连接中断，请重试", errorKind: "generic" });
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

import { create } from "zustand";
import { http } from "../api/http";
import { sendChatStream } from "../api/sse";
import type { AppInfo, ChatMessage, ConversationSummary } from "../types";
let seq = 0;
function nextId(prefix: string): string {
  seq += 1;
  return `${prefix}-${Date.now()}-${seq}`;
}

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
  sendMessage: (query: string, inputs?: Record<string, string>) => Promise<void>;
  retryLast: () => Promise<void>;
  stopStreaming: () => void;
}

let abortController: AbortController | null = null;
/** 最近一次发送的 workflow inputs：重试时保真（chat 应用无 inputs） */
let lastInputs: Record<string, string> | undefined;

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
    return activeConversationId ? messagesByConv[activeConversationId] ?? [] : [];
  },

  async sendMessage(query, inputs) {
    const state = get();
    const app = state.activeApp();
    if (!app || state.streaming || !query.trim()) return;
    lastInputs = inputs;

    // 无活跃会话时开启新会话（标题取首条问题）
    let conversationId = state.activeConversationId;
    if (!conversationId) {
      conversationId = nextId("conv");
      set({ activeConversationId: conversationId });
    }

    const userMsg: ChatMessage = {
      id: nextId("msg"),
      conversationId,
      role: "user",
      content: query.trim(),
      status: "done",
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

    const finish = () => {
      abortController = null;
      set({ streaming: false });
      // 会话摘要落本地（标题=首条问题截断，契约 Conversations 段）
      const list = get().messagesByConv[conversationId as string] ?? [];
      const summary: ConversationSummary = {
        id: conversationId as string,
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

    await sendChatStream(
      { app_id: app.id, query: query.trim(), conversation_id: conversationId, inputs },
      {
        onMessage: (delta) => {
          const current = get().messagesByConv[conversationId as string] ?? [];
          const m = current.find((x) => x.id === assistantMsg.id);
          patchAssistant({ content: (m?.content ?? "") + delta });
        },
        onMessageEnd: (usage) => patchAssistant({ usage }),
        onError: (message, kind) => {
          patchAssistant({ status: "error", content: message, errorKind: kind });
        },
        onAgentDone: () => {
          patchAssistant({ status: "done" });
          finish();
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
    await get().sendMessage(lastUser.content, lastInputs);
  },

  stopStreaming() {
    abortController?.abort();
    abortController = null;
    set({ streaming: false });
  },
}));

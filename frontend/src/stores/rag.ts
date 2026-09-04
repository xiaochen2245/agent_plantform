import { create } from "zustand";
import { ragApi, type RagChunk, type RagDataset, type RagDocument } from "../api/rag";

/** 检索测试台参数（RagKnowledge 管理 Tab 持有，随 store 跨 Tab 存活）。 */
export interface RagSearchParams {
  top_n: number;
  similarity_threshold?: number;
  vector_similarity_weight?: number;
  keyword: boolean;
  highlight: boolean;
}

interface RagState {
  datasets: RagDataset[];
  dsId: string;
  docs: RagDocument[];
  loading: boolean;
  searching: boolean;
  results: RagChunk[];
  params: RagSearchParams;
  /** 文档详情抽屉（管理 Tab 行点击与问答引用卡片共用）。 */
  drawer: {
    doc: RagDocument | null;
    dsId: string | null;
    chunks: RagChunk[];
    total: number;
    page: number;
    loading: boolean;
  };

  loadDatasets: (preferId?: string) => Promise<void>;
  selectDataset: (id: string) => Promise<void>;
  refreshDocs: () => Promise<void>;
  setParams: (p: Partial<RagSearchParams>) => void;
  search: (question: string, discipline?: string) => Promise<void>;
  openDrawer: (doc: RagDocument, dsId?: string) => Promise<void>;
  loadChunksPage: (page: number) => Promise<void>;
  closeDrawer: () => void;
}

export const useRagStore = create<RagState>((set, get) => ({
  datasets: [],
  dsId: "",
  docs: [],
  loading: false,
  searching: false,
  results: [],
  params: { top_n: 10, keyword: false, highlight: false },
  drawer: { doc: null, dsId: null, chunks: [], total: 0, page: 1, loading: false },

  async loadDatasets(preferId) {
    const { data } = await ragApi.datasets();
    const list = data.data ?? [];
    set({ datasets: list, dsId: preferId || get().dsId || list[0]?.id || "" });
  },

  async selectDataset(id) {
    set({ dsId: id });
    await get().refreshDocs();
  },

  async refreshDocs() {
    const dsId = get().dsId;
    if (!dsId) return;
    set({ loading: true });
    try {
      const { data } = await ragApi.documents(dsId);
      set({ docs: data.documents ?? [] });
    } finally {
      set({ loading: false });
    }
  },

  setParams(p) {
    set({ params: { ...get().params, ...p } });
  },

  async search(question, discipline) {
    const { dsId, params } = get();
    if (!question.trim() || !dsId) return;
    set({ searching: true });
    try {
      const { data } = await ragApi.retrieve({
        question,
        dataset_ids: [dsId],
        discipline: discipline || undefined,
        similarity_threshold: params.similarity_threshold,
        vector_similarity_weight: params.vector_similarity_weight,
        keyword: params.keyword || undefined,
        highlight: params.highlight || undefined,
        top_n: params.top_n,
      });
      set({ results: data.chunks ?? [] });
    } finally {
      set({ searching: false });
    }
  },

  async openDrawer(doc, dsId) {
    const dataset = dsId ?? get().dsId;
    if (!dataset) return;
    set({ drawer: { doc, dsId: dataset, chunks: [], total: 0, page: 1, loading: true } });
    try {
      const { data } = await ragApi.listChunks(dataset, doc.id, { page: 1 });
      set({ drawer: { ...get().drawer, chunks: data.chunks ?? [], total: data.total ?? 0, loading: false } });
    } catch (e) {
      set({ drawer: { ...get().drawer, loading: false } });
      throw e;
    }
  },

  async loadChunksPage(page) {
    const { drawer } = get();
    if (!drawer.doc || !drawer.dsId) return;
    set({ drawer: { ...drawer, page, loading: true } });
    try {
      const { data } = await ragApi.listChunks(drawer.dsId, drawer.doc.id, { page });
      set({ drawer: { ...get().drawer, chunks: data.chunks ?? [], total: data.total ?? 0, loading: false } });
    } finally {
      set({ drawer: { ...get().drawer, loading: false } });
    }
  },

  closeDrawer() {
    set({ drawer: { doc: null, dsId: null, chunks: [], total: 0, page: 1, loading: false } });
  },
}));

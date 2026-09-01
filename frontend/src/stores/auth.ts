import { create } from "zustand";
import { http } from "../api/http";
import type { MeInfo } from "../types";

export type AuthStatus = "idle" | "loading" | "authenticated" | "anonymous";

interface AuthState {
  me: MeInfo | null;
  status: AuthStatus;
  login: (email: string, password: string) => Promise<void>;
  fetchMe: () => Promise<MeInfo | null>;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  me: null,
  status: "idle",

  async login(email, password) {
    set({ status: "loading" });
    try {
      await http.post("/auth/login", { email, password });
      const me = (await http.get<MeInfo>("/auth/me")).data;
      set({ me, status: "authenticated" });
    } catch (error) {
      set({ status: "anonymous" });
      throw error;
    }
  },

  async fetchMe() {
    try {
      const me = (await http.get<MeInfo>("/auth/me")).data;
      set({ me, status: "authenticated" });
      return me;
    } catch {
      set({ me: null, status: "anonymous" });
      return null;
    }
  },

  async logout() {
    try {
      await http.post("/auth/logout");
    } finally {
      set({ me: null, status: "anonymous" });
    }
  },
}));

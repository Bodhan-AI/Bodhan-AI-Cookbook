import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, tokenStore } from "./api";
import type { User } from "./types";

interface AuthCtx {
  user: User | null; loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (username: string, email: string, password: string) => Promise<void>;
  logout: () => void; setUser: (u: User) => void;
}
const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(!!tokenStore.get());

  const logout = useCallback(() => { tokenStore.clear(); setUser(null); }, []);

  useEffect(() => {
    if (!tokenStore.get()) return;
    api.me().then(setUser).catch(() => tokenStore.clear()).finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    const h = () => logout();
    window.addEventListener("hl:unauthorized", h);
    return () => window.removeEventListener("hl:unauthorized", h);
  }, [logout]);

  const value = useMemo<AuthCtx>(() => ({
    user, loading, setUser, logout,
    login: async (email, password) => { const r = await api.login({ email, password }); tokenStore.set(r.access_token); setUser(r.user); },
    register: async (username, email, password) => { const r = await api.register({ username, email, password }); tokenStore.set(r.access_token); setUser(r.user); },
  }), [user, loading, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside <AuthProvider>");
  return v;
}

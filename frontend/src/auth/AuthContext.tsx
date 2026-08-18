import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, tokenStore } from "../api/client";

interface Identity {
  tenant: string;
  email: string;
  expiresAt: number;
}

interface AuthValue {
  identity: Identity | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthValue | null>(null);
const IDENTITY_KEY = "deeprunner.identity";

/** The token is the only credential. The tenant shown here is decoded from
 *  it for display — the server never trusts anything the client sends. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Identity | null>(() => {
    const raw = localStorage.getItem(IDENTITY_KEY);
    if (!raw || !tokenStore.get()) return null;
    const parsed = JSON.parse(raw) as Identity;
    return parsed.expiresAt > Date.now() ? parsed : null;
  });

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.login(email, password);
    tokenStore.set(res.access_token);
    const next: Identity = {
      tenant: res.tenant,
      email,
      expiresAt: Date.now() + res.expires_in * 1000,
    };
    localStorage.setItem(IDENTITY_KEY, JSON.stringify(next));
    setIdentity(next);
  }, []);

  const logout = useCallback(() => {
    tokenStore.clear();
    localStorage.removeItem(IDENTITY_KEY);
    setIdentity(null);
  }, []);

  const value = useMemo(() => ({ identity, login, logout }), [identity, login, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside AuthProvider");
  return v;
}

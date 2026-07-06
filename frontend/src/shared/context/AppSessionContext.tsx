import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { backendLogout, getStoredAuthToken } from "@/shared/lib/api";

const LOADER_KEY = "sentinel_skip_loader_v1";
const SCROLL_KEY = "sentinel_index_scroll_y_v1";
const AUTH_KEY = "sentinel_logged_in_v1";
const TOKEN_EXPIRY_KEY = "sentinel_token_expiry_v1";

type AppSessionContextValue = {
  loaderDone: boolean;
  markLoaderDone: () => void;
  saveIndexScroll: () => void;
  consumeIndexScroll: () => number | null;
  loggedIn: boolean;
  login: (expiresIn?: number) => void;
  logout: () => void;
};

const AppSessionContext = createContext<AppSessionContextValue | null>(null);

export function AppSessionProvider({ children }: { children: ReactNode }) {
  const [loaderDone, setLoaderDone] = useState(() => typeof window !== "undefined" && sessionStorage.getItem(LOADER_KEY) === "1");
  const [loggedIn, setLoggedIn] = useState(() => {
    if (typeof window === "undefined") return false;
    return (
      localStorage.getItem(AUTH_KEY) === "1" || localStorage.getItem("sentinel_session_v1") != null
    );
  });

  const markLoaderDone = useCallback(() => {
    sessionStorage.setItem(LOADER_KEY, "1");
    setLoaderDone(true);
  }, []);

  const saveIndexScroll = useCallback(() => {
    sessionStorage.setItem(SCROLL_KEY, String(window.scrollY));
  }, []);

  const consumeIndexScroll = useCallback(() => {
    const raw = sessionStorage.getItem(SCROLL_KEY);
    if (raw == null) return null;
    sessionStorage.removeItem(SCROLL_KEY);
    const y = Number(raw);
    return Number.isFinite(y) ? y : null;
  }, []);

  const login = useCallback((expiresIn?: number) => {
    localStorage.setItem(AUTH_KEY, "1");
    if (expiresIn && expiresIn > 0) {
      localStorage.setItem(TOKEN_EXPIRY_KEY, String(Date.now() + expiresIn * 1000));
    }
    setLoggedIn(true);
  }, []);

  // Auto-logout on 401 from any API call
  useEffect(() => {
    const handler = () => {
      localStorage.removeItem(AUTH_KEY);
      localStorage.removeItem("sentinel_session_v1");
      try { localStorage.removeItem("sentinel_auth_token_v1"); } catch { /* noop */ }
      setLoggedIn(false);
    };
    window.addEventListener("sentinel-unauthorized", handler);
    return () => window.removeEventListener("sentinel-unauthorized", handler);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(AUTH_KEY);
    localStorage.removeItem(TOKEN_EXPIRY_KEY);
    localStorage.removeItem("sentinel_session_v1");
    setLoggedIn(false);
    void backendLogout();
  }, []);

  // Periodic token expiry check (every 30s)
  useEffect(() => {
    const check = () => {
      const expiry = localStorage.getItem(TOKEN_EXPIRY_KEY);
      if (expiry && Date.now() > Number(expiry)) {
        logout();
      }
      // Also sync login state across tabs
      const flag = localStorage.getItem(AUTH_KEY) === "1";
      const hasToken = !!getStoredAuthToken();
      if (loggedIn && !flag && !hasToken) setLoggedIn(false);
      if (!loggedIn && flag) setLoggedIn(true);
    };
    const iv = window.setInterval(check, 30000);
    return () => window.clearInterval(iv);
  }, [loggedIn, logout]);

  const value = useMemo(
    () => ({ loaderDone, markLoaderDone, saveIndexScroll, consumeIndexScroll, loggedIn, login, logout }),
    [loaderDone, markLoaderDone, saveIndexScroll, consumeIndexScroll, loggedIn, login, logout]
  );

  return <AppSessionContext.Provider value={value}>{children}</AppSessionContext.Provider>;
}

export function useAppSession() {
  const ctx = useContext(AppSessionContext);
  if (!ctx) throw new Error("useAppSession must be used within AppSessionProvider");
  return ctx;
}

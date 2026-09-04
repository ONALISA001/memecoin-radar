"use client";
import { useEffect, useRef, useState } from "react";

const TOKEN_KEY = "mcr_token";

// In-memory fallback: sandboxed/preview iframes may block localStorage entirely.
// Also dedupes concurrent token mints (each page fires several hooks at once).
let memToken: string | null = null;
let tokenPromise: Promise<string | null> | null = null;

function readStored(): string | null {
  try { return window.localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
function writeStored(tok: string) {
  memToken = tok;
  try { window.localStorage.setItem(TOKEN_KEY, tok); } catch { /* iframe-blocked storage */ }
}
function clearStored() {
  memToken = null;
  try { window.localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
}

async function mintToken(): Promise<string | null> {
  try {
    const r = await fetch("/api/auth/demo", { method: "POST" });
    if (!r.ok) return null;
    const d = await r.json();
    writeStored(d.token);
    return d.token;
  } catch {
    return null;
  }
}

async function ensureToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const tok = memToken || readStored();
  if (tok) return tok;
  if (!tokenPromise) {
    tokenPromise = (async () => {
      const t = await mintToken();
      tokenPromise = null;
      return t;
    })();
  }
  return tokenPromise;
}

export async function api<T = any>(path: string, opts: RequestInit = {}, auth = true): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(opts.headers as any) };
  let tok: string | null = null;
  if (auth) {
    tok = await ensureToken();
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
  }
  let r = await fetch(`/api${path}`, { ...opts, headers });
  if (r.status === 401 && auth) {
    clearStored();
    tok = await ensureToken();
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    r = await fetch(`/api${path}`, { ...opts, headers });
  }
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json() as Promise<T>;
}

/** Polling fetch hook (auto-refreshing dashboards). */
export function useApi<T = any>(path: string | null, refreshMs = 30000, auth = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let dead = false;
    async function load() {
      if (!path) return;
      try {
        const d = await api<T>(path, {}, auth);
        if (!dead) { setData(d); setError(null); }
      } catch (e: any) {
        if (!dead) setError(e.message || "error");
      } finally {
        if (!dead) setLoading(false);
      }
    }
    load();
    if (refreshMs > 0) timer.current = setInterval(load, refreshMs);
    return () => { dead = true; if (timer.current) clearInterval(timer.current); };
  }, [path, refreshMs]);

  return { data, error, loading };
}

export async function apiPost<T = any>(path: string, body?: any): Promise<T> {
  return api<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
}
export async function apiPatch<T = any>(path: string, body?: any): Promise<T> {
  return api<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined });
}
export async function apiDelete<T = any>(path: string): Promise<T> {
  return api<T>(path, { method: "DELETE" });
}

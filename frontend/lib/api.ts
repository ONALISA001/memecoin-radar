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
    announceRole();
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

// ---------------- roles: visitor (read-only) vs owner ----------------
export type Role = "owner" | "visitor" | null;
const ROLE_EVENT = "mcr-role";

export function tokenRole(tok: string | null): Role {
  if (!tok) return null;
  try {
    const part = tok.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), "=")));
    if (payload.exp && payload.exp * 1000 < Date.now()) return null;
    return payload.role === "owner" ? "owner" : "visitor";
  } catch {
    return null;
  }
}

export function currentRole(): Role {
  if (typeof window === "undefined") return null;
  return tokenRole(memToken || readStored());
}

function announceRole() {
  try { window.dispatchEvent(new Event(ROLE_EVENT)); } catch { /* ignore */ }
}

/** Exchange the owner key for an owner token. Throws with a readable message. */
export async function ownerLogin(key: string): Promise<void> {
  const r = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!r.ok) {
    let msg = r.status === 401 ? "Wrong key" : `Login failed (${r.status})`;
    try { const d = await r.json(); if (r.status !== 401 && d.detail) msg = d.detail; } catch {}
    throw new Error(msg);
  }
  const d = await r.json();
  writeStored(d.token);
  announceRole();
}

/** Drop the owner token; the next request mints a read-only visitor token. */
export function ownerLogout() {
  clearStored();
  announceRole();
}

/** React hook: the current role, updated on login/logout. */
export function useRole(): Role {
  const [role, setRole] = useState<Role>(null);
  useEffect(() => {
    const update = () => setRole(currentRole());
    update();
    window.addEventListener(ROLE_EVENT, update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener(ROLE_EVENT, update);
      window.removeEventListener("storage", update);
    };
  }, []);
  return role;
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
    announceRole();
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

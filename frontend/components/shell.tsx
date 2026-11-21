"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, apiPost, AUTH_NOTICE, ERROR_NOTICE, ownerLogin, ownerLogout, reportError, useApi, useRole } from "@/lib/api";
import { Badge, LivePulse } from "./ui";
import { timeAgo } from "@/lib/format";
import {
  Gavel,
  Bell, BellRing, GitCompareArrows, History, Layers, LayoutDashboard, Menu, Radar, Radio, Search,
  SlidersHorizontal, Sparkles, Star, Wallet, X, PauseCircle, Lock, LockOpen,
} from "lucide-react";

type NavItem = { href: string; label: string; icon: React.ComponentType<{ size?: number; className?: string }> };
const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  { title: "Discover", items: [
    { href: "/", label: "Dashboard", icon: LayoutDashboard },
    { href: "/new", label: "New coins", icon: Sparkles },
    { href: "/screener", label: "Screener", icon: SlidersHorizontal },
    { href: "/narratives", label: "Narratives", icon: Layers },
  ] },
  { title: "Analyze", items: [
    { href: "/verdicts", label: "Verdicts", icon: Gavel },
    { href: "/feed", label: "Intelligence", icon: Radio },
    { href: "/compare", label: "Compare", icon: GitCompareArrows },
    { href: "/backtesting", label: "Backtesting", icon: History },
  ] },
  { title: "Trade", items: [
    { href: "/paper", label: "Paper trader", icon: Wallet },
  ] },
  { title: "You", items: [
    { href: "/watchlist", label: "Watchlist", icon: Star },
    { href: "/alerts", label: "Alerts", icon: BellRing },
  ] },
];

function AlertBell() {
  const { data } = useApi("/alerts/events", 20000);
  const [open, setOpen] = useState(false);
  const [notify, setNotify] = useState(false);
  const seen = useRef<Set<number>>(new Set());
  const items: any[] = data?.items || [];

  useEffect(() => {
    if (!notify || typeof Notification === "undefined" || Notification.permission !== "granted") return;
    for (const ev of items) {
      if (!seen.current.has(ev.id) && !ev.read) {
        seen.current.add(ev.id);
        const p = ev.payload || {};
        new Notification(`🚨 MemeCoin Radar — ${p.alert || "Alert"}`, {
          body: `${p.token ? `$${p.token} · ` : ""}${p.trigger || ""}\nTrend ${fmt(p.trend)} · FOMO ${fmt(p.fomo)} · Risk ${fmt(p.risk)}`,
        });
      }
    }
  }, [items, notify]);

  function fmt(v: any) { return typeof v === "number" ? Math.round(v) : "—"; }

  async function toggleNotify() {
    if (!notify && typeof Notification !== "undefined" && Notification.permission === "default") {
      await Notification.requestPermission();
    }
    setNotify(!notify);
  }

  return (
    <div className="relative">
      <button className="btn relative !p-2" onClick={() => setOpen(!open)} title="Alert events" aria-label="Alert events">
        <Bell size={16} />
        {(data?.unread ?? 0) > 0 && (
          <span className="absolute -top-1.5 -right-1.5 bg-redx text-bg text-xs font-extrabold rounded-full min-w-[16px] h-4 px-1 flex items-center justify-center">
            {data.unread > 99 ? "99+" : data.unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-50 w-[380px] max-h-[70vh] overflow-auto panel shadow-2xl">
          <div className="panel-hd sticky top-0 bg-panel z-10">
            <span className="panel-title">Alert events</span>
            <div className="flex gap-1.5">
              <button className="btn !py-0.5 !px-2 !text-2xs" onClick={toggleNotify}>
                {notify ? "browser: on" : "browser: off"}
              </button>
              <button className="btn !py-0.5 !px-2 !text-2xs"
                      onClick={async () => { try { await apiPost("/alerts/events/read-all"); setOpen(false); } catch (e) { reportError(e); } }}>
                mark read
              </button>
            </div>
          </div>
          {items.length === 0 && <div className="p-4 text-xs text-dim">No alert events yet.</div>}
          {items.map((ev) => {
            const p = ev.payload || {};
            return (
              <Link key={ev.id} href={ev.symbol ? `/token/${p.address}` : "/alerts"}
                    onClick={() => setOpen(false)}
                    className={`block px-3.5 py-2.5 border-b border-line/60 hover:bg-panel2 ${ev.read ? "opacity-55" : ""}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-bold text-ink">{p.alert}</span>
                  <span className="text-xs text-dim shrink-0">{timeAgo(ev.ts)}</span>
                </div>
                <div className="text-sm text-dim mt-0.5">
                  {p.token && <span className="text-cyanx font-semibold">${p.token} · </span>}{p.trigger}
                </div>
                {p.token && (
                  <div className="flex gap-2 mt-1">
                    <Badge kind="cyan">T {fmt(p.trend)}</Badge>
                    <Badge kind="amber">F {fmt(p.fomo)}</Badge>
                    <Badge kind="red">R {fmt(p.risk)}</Badge>
                  </div>
                )}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SearchBox() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return; }
    const h = setTimeout(async () => {
      try { setRes(await api(`/search?q=${encodeURIComponent(q.trim())}`, {}, false)); setOpen(true); } catch {}
    }, 250);
    return () => clearTimeout(h);
  }, [q]);
  return (
    <div className="relative w-full max-w-sm">
      <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-dim pointer-events-none" />
      <input className="inp w-full !pl-9" placeholder="Search token, address, narrative…"
             value={q} onChange={(e) => setQ(e.target.value)}
             onFocus={() => res && setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 200)} />
      {open && res && (
        <div className="absolute left-0 right-0 top-9 panel z-50 max-h-80 overflow-auto shadow-2xl">
          {res.address_valid === false && <div className="px-3 py-2 text-sm text-redx">Invalid address format</div>}
          {(res.tokens || []).map((t: any) => (
            <Link key={t.address} href={`/token/${t.address}`}
                  className="flex items-center justify-between px-3 py-2 hover:bg-panel2 border-b border-line/50"
                  onClick={() => setOpen(false)}>
              <span className="text-xs font-semibold text-ink">{t.symbol} <span className="panel-sub">{t.name}</span></span>
              <span className="num text-xs text-cyanx">{Math.round(t.trend)}</span>
            </Link>
          ))}
          {(res.narratives || []).map((n: any) => (
            <Link key={n.id} href={`/narratives/${n.id}`}
                  className="flex items-center justify-between px-3 py-2 hover:bg-panel2 border-b border-line/50"
                  onClick={() => setOpen(false)}>
              <span className="text-xs text-violetx flex items-center gap-1.5"><Layers size={13} /> {n.name}</span>
              <span className="num text-xs text-violetx">{Math.round(n.score)}</span>
            </Link>
          ))}
          {!(res.tokens || []).length && !(res.narratives || []).length && res.address_valid !== false && (
            <div className="px-3 py-3 text-xs text-dim">No matches.</div>
          )}
        </div>
      )}
    </div>
  );
}

/** Shown while DexScreener is down: last real data, scoring + paper trading paused. */
function OutageBanner() {
  const { data } = useApi("/sources", 60000, false);
  const dex = (data?.sources || []).find((x: any) => x.name === "dexscreener");
  if (!dex || dex.status !== "down") return null;
  const since = dex.meta?.since ? new Date(dex.meta.since * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
  return (
    <div className="bg-amberx/10 border-b border-amberx/40 text-sm px-4 py-2.5 text-center">
      <span className="text-amberx font-semibold inline-flex items-center gap-1.5"><PauseCircle size={14} /> Market data paused</span>
      <span className="text-dim"> — DexScreener is unreachable{since ? ` since ${since}` : ""}. You&apos;re seeing the last real data;
        scores, alerts and paper trading resume automatically when it&apos;s back.</span>
    </div>
  );
}

/** Shown when an action needs the owner and the owner session is missing or expired. */
function AuthBanner() {
  const role = useRole();
  const [kind, setKind] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const on = (e: Event) => setKind((e as CustomEvent).detail || "owner_required");
    window.addEventListener(AUTH_NOTICE, on);
    return () => window.removeEventListener(AUTH_NOTICE, on);
  }, []);
  useEffect(() => { if (role === "owner") setKind(null); }, [role]);
  if (!kind || role === "owner") return null;
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!key) return;
    setBusy(true); setErr(null);
    try { await ownerLogin(key); setKey(""); setKind(null); }
    catch (ex: any) { setErr(ex.message || "Login failed"); }
    finally { setBusy(false); }
  }
  return (
    <div role="alert" className="bg-amberx/10 border-b border-amberx/40 text-sm px-4 py-2.5">
      <form onSubmit={submit} className="max-w-[1400px] mx-auto flex flex-wrap items-center gap-2">
        <Lock size={14} className="text-amberx" />
        <span className="text-amberx font-semibold">
          {kind === "owner_expired" ? "Your owner session expired" : "Owner login required"}
        </span>
        <span className="text-dim">— resets, backtests, supervisor approvals and deleting alerts need the owner key. Log in, then try again.</span>
        <input type="password" autoComplete="current-password" placeholder="Owner key" value={key}
               onChange={(e) => setKey(e.target.value)} aria-label="Owner key"
               className="rounded-md bg-panel2 border border-line px-2.5 py-1 text-xs text-ink outline-none focus:border-cyanx w-40" />
        <button type="submit" disabled={busy || !key} className="rounded-md bg-cyanx/15 text-cyanx text-xs px-3 py-1 disabled:opacity-50">
          {busy ? "Checking…" : "Log in"}</button>
        <button type="button" className="text-xs text-dim hover:text-ink" onClick={() => setKind(null)}>Dismiss</button>
        {err && <span className="text-2xs text-redx w-full">{err}</span>}
      </form>
    </div>
  );
}

/** Short-lived error toast for failed actions (see reportError). */
function ErrorToast() {
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    let t: ReturnType<typeof setTimeout> | undefined;
    const on = (e: Event) => {
      setMsg((e as CustomEvent).detail);
      if (t) clearTimeout(t);
      t = setTimeout(() => setMsg(null), 7000);
    };
    window.addEventListener(ERROR_NOTICE, on);
    return () => { window.removeEventListener(ERROR_NOTICE, on); if (t) clearTimeout(t); };
  }, []);
  if (!msg) return null;
  return (
    <div role="alert" className="fixed bottom-4 right-4 z-[60] max-w-sm panel !border-redx/50 px-4 py-3 text-sm shadow-lg">
      <div className="flex items-start gap-3">
        <span className="text-redx font-semibold">Action failed</span>
        <button className="ml-auto text-dim hover:text-ink" aria-label="Dismiss" onClick={() => setMsg(null)}><X size={14} /></button>
      </div>
      <div className="text-xs text-ink/80 mt-1 break-words">{msg}</div>
    </div>
  );
}

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

function SideNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="flex flex-col gap-6 px-3 py-5">
      {NAV_GROUPS.map((g) => (
        <div key={g.title}>
          <div className="px-3 mb-1.5 text-2xs font-semibold uppercase tracking-wider text-dim/70">{g.title}</div>
          <div className="flex flex-col gap-0.5">
            {g.items.map(({ href, label, icon: Icon }) => {
              const active = isActive(pathname, href);
              return (
                <Link key={href} href={href} onClick={onNavigate}
                      className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                        active ? "bg-cyanx/10 text-cyanx" : "text-dim hover:text-ink hover:bg-panel2"}`}>
                  <Icon size={17} className="shrink-0" />
                  {label}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

function OwnerAccess() {
  const role = useRole();
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!key) return;
    setBusy(true); setErr(null);
    try {
      await ownerLogin(key);
      setKey(""); setOpen(false);
    } catch (ex: any) {
      setErr(ex.message || "Login failed");
    } finally {
      setBusy(false);
    }
  }

  if (role === "owner") {
    return (
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="flex items-center gap-1.5 text-cyanx"><LockOpen size={14} /> Owner</span>
        <button className="text-dim hover:text-ink" onClick={() => ownerLogout()}>Log out</button>
      </div>
    );
  }
  if (!open) {
    return (
      <button className="flex items-center gap-1.5 text-xs text-dim hover:text-ink" onClick={() => setOpen(true)}>
        <Lock size={14} /> Owner login
      </button>
    );
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <input type="password" autoComplete="current-password" placeholder="Owner key" value={key} autoFocus
             onChange={(e) => setKey(e.target.value)}
             className="w-full rounded-md bg-panel2 border border-line px-2.5 py-1.5 text-xs text-ink outline-none focus:border-cyanx" />
      {err && <span className="text-2xs text-redx">{err}</span>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy || !key}
                className="flex-1 rounded-md bg-cyanx/15 text-cyanx text-xs py-1.5 disabled:opacity-50">
          {busy ? "Checking…" : "Log in"}
        </button>
        <button type="button" className="text-xs text-dim hover:text-ink px-2" onClick={() => { setOpen(false); setErr(null); }}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2.5 shrink-0">
      <span className="w-8 h-8 rounded-lg bg-cyanx/15 text-cyanx flex items-center justify-center"><Radar size={18} /></span>
      <span className="font-bold text-base text-ink tracking-tight">MemeCoin <span className="text-cyanx">Radar</span></span>
    </Link>
  );
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const [drawer, setDrawer] = useState(false);
  const pathname = usePathname();
  useEffect(() => { setDrawer(false); }, [pathname]);
  return (
    <div className="min-h-screen lg:pl-60">
      {/* desktop sidebar */}
      <aside className="hidden lg:flex fixed inset-y-0 left-0 w-60 flex-col bg-panel border-r border-line z-40">
        <div className="h-16 flex items-center px-5 border-b border-line"><Logo /></div>
        <div className="flex-1 overflow-y-auto"><SideNav /></div>
        <div className="px-5 py-4 border-t border-line flex flex-col gap-3">
          <OwnerAccess />
          <div className="text-2xs text-dim leading-relaxed">Analytics only — not financial advice.</div>
        </div>
      </aside>

      {/* mobile drawer */}
      {drawer && (
        <div className="lg:hidden fixed inset-0 z-50">
          <div className="absolute inset-0 bg-black/60" onClick={() => setDrawer(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 bg-panel border-r border-line overflow-y-auto">
            <div className="h-16 flex items-center justify-between px-5 border-b border-line">
              <Logo />
              <button className="text-dim hover:text-ink" onClick={() => setDrawer(false)} aria-label="Close menu"><X size={20} /></button>
            </div>
            <SideNav onNavigate={() => setDrawer(false)} />
            <div className="px-5 py-4 border-t border-line"><OwnerAccess /></div>
          </aside>
        </div>
      )}

      <header className="sticky top-0 z-30 bg-bg/90 backdrop-blur border-b border-line">
        <div className="max-w-[1400px] mx-auto px-4 lg:px-6 h-16 flex items-center gap-3">
          <button className="lg:hidden text-dim hover:text-ink" onClick={() => setDrawer(true)} aria-label="Open menu"><Menu size={22} /></button>
          <div className="lg:hidden"><Logo /></div>
          <div className="hidden sm:block flex-1"><SearchBox /></div>
          <div className="flex-1 sm:hidden" />
          <LivePulse />
          <AlertBell />
        </div>
        <div className="sm:hidden px-4 pb-3"><SearchBox /></div>
      </header>
      <AuthBanner />
      <ErrorToast />
      <OutageBanner />
      <main className="max-w-[1400px] mx-auto px-4 lg:px-6 py-6">{children}</main>
      <footer className="max-w-[1400px] mx-auto px-4 lg:px-6 py-6 text-xs text-dim border-t border-line/60">
        <details>
          <summary className="cursor-pointer hover:text-ink">MemeCoin Radar is an analytics tool, not financial advice. Read the full disclaimer</summary>
          <p className="mt-2 leading-relaxed max-w-3xl">
            Scores are algorithmic estimates over data that may be incomplete or delayed. Memecoins are extremely
            volatile and can go to zero. A high Trend Score does not mean a price will increase; extreme FOMO often
            signals overheating, not opportunity. Do your own research.
          </p>
        </details>
      </footer>
    </div>
  );
}

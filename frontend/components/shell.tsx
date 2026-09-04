"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, apiPost, useApi } from "@/lib/api";
import { Badge, LivePulse } from "./ui";
import { timeAgo } from "@/lib/format";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/feed", label: "Intelligence" },
  { href: "/paper", label: "Paper Trader" },
  { href: "/new", label: "New Coins" },
  { href: "/screener", label: "Screener" },
  { href: "/narratives", label: "Narratives" },
  { href: "/backtesting", label: "Backtesting" },
  { href: "/compare", label: "Compare" },
  { href: "/watchlist", label: "Watchlist" },
  { href: "/alerts", label: "Alerts" },
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
      <button className="btn relative !py-1.5" onClick={() => setOpen(!open)} title="Alert events">
        🔔
        {(data?.unread ?? 0) > 0 && (
          <span className="absolute -top-1.5 -right-1.5 bg-redx text-bg text-[10px] font-extrabold rounded-full min-w-[16px] h-4 px-1 flex items-center justify-center">
            {data.unread > 99 ? "99+" : data.unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-50 w-[380px] max-h-[70vh] overflow-auto panel shadow-2xl">
          <div className="panel-hd sticky top-0 bg-panel z-10">
            <span className="panel-title">Alert events</span>
            <div className="flex gap-1.5">
              <button className="btn !py-0.5 !px-2 !text-[9px]" onClick={toggleNotify}>
                {notify ? "browser: on" : "browser: off"}
              </button>
              <button className="btn !py-0.5 !px-2 !text-[9px]"
                      onClick={async () => { await apiPost("/alerts/events/read-all"); setOpen(false); }}>
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
                  <span className="text-[11px] font-bold text-ink">{p.alert}</span>
                  <span className="text-[10px] text-dim shrink-0">{timeAgo(ev.ts)}</span>
                </div>
                <div className="text-[11px] text-dim mt-0.5">
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
    <div className="relative w-64 max-w-[45vw]">
      <input className="inp w-full !py-1.5" placeholder="Search token, address, narrative…"
             value={q} onChange={(e) => setQ(e.target.value)}
             onFocus={() => res && setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 200)} />
      {open && res && (
        <div className="absolute left-0 right-0 top-9 panel z-50 max-h-80 overflow-auto shadow-2xl">
          {res.address_valid === false && <div className="px-3 py-2 text-[11px] text-redx">Invalid address format</div>}
          {(res.tokens || []).map((t: any) => (
            <Link key={t.address} href={`/token/${t.address}`}
                  className="flex items-center justify-between px-3 py-2 hover:bg-panel2 border-b border-line/50"
                  onClick={() => setOpen(false)}>
              <span className="text-xs font-semibold text-ink">{t.symbol} <span className="text-dim font-normal">{t.name}</span></span>
              <span className="num text-xs text-cyanx">{Math.round(t.trend)}</span>
            </Link>
          ))}
          {(res.narratives || []).map((n: any) => (
            <Link key={n.id} href={`/narratives/${n.id}`}
                  className="flex items-center justify-between px-3 py-2 hover:bg-panel2 border-b border-line/50"
                  onClick={() => setOpen(false)}>
              <span className="text-xs text-violetx">🧠 {n.name}</span>
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

export default function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 bg-bg/95 backdrop-blur border-b border-line">
        <div className="max-w-[1500px] mx-auto px-4 h-12 flex items-center gap-4">
          <Link href="/" className="flex items-center gap-2 shrink-0">
            <span className="text-lg">🛰️</span>
            <span className="font-extrabold tracking-[0.12em] text-[13px] text-ink">
              MEMECOIN<span className="text-cyanx">RADAR</span>
            </span>
          </Link>
          <nav className="hidden md:flex items-center gap-1">
            {NAV.map((n) => (
              <Link key={n.href} href={n.href}
                    className={`px-3 py-1.5 rounded-md text-[11px] font-bold tracking-wider uppercase transition-colors ${
                      (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href))
                        ? "bg-cyanx/10 text-cyanx border border-cyanx/20" : "text-dim hover:text-ink"}`}>
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="flex-1" />
          <SearchBox />
          <LivePulse />
          <AlertBell />
        </div>
      </header>
      <main className="max-w-[1500px] mx-auto px-4 py-4">{children}</main>
      <footer className="max-w-[1500px] mx-auto px-4 pb-6 pt-2 text-[10px] text-dim leading-relaxed border-t border-line/50 mt-6">
        MemeCoin Radar is an analytics tool. Scores are algorithmic estimates over data that may be incomplete,
        delayed or simulated (mock sources are labeled). Memecoins are extremely volatile and can go to zero.
        A high Trend Score does not mean a price will increase; extreme FOMO often signals overheating, not opportunity.
        Nothing here is financial advice — do your own research.
      </footer>
    </div>
  );
}

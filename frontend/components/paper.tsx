"use client";
/* v4 Paper Trader UI (§11–§19, §26–§28, §32). Every surface is labeled
   PAPER/SIMULATION — execution is virtual accounting over live market data. */
import Link from "next/link";
import { useState } from "react";
import { reportError, apiPost, useApi } from "@/lib/api";
import { Badge, Panel } from "@/components/ui";

/* ------------------------------------------------------------- helpers */
export function fmtUtc(ts?: number | null): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n: number, l = 2) => String(n).padStart(l, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
         `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())} UTC`;
}
export function holdStr(secs?: number | null): string {
  if (secs == null) return "—";
  if (secs < 3600) return `${Math.max(1, Math.round(secs / 60))}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
  return `${Math.floor(secs / 86400)}d ${Math.floor((secs % 86400) / 3600)}h`;
}
export const usd = (v?: number | null, digits = 2): string =>
  v == null ? "—" : `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
export const px = (v?: number | null): string =>
  v == null ? "—" : `$${v >= 0.01 ? v.toFixed(4) : Number(v.toPrecision(4)).toString()}`;
export const pnlCls = (v?: number | null) => v == null ? "text-dim" : v >= 0 ? "text-greenx" : "text-redx";
export const pnlSign = (v?: number | null) => (v == null ? "—" : `${v >= 0 ? "+" : ""}$${v.toFixed(2)}`);

export function SimStrip({ compact }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2 flex-wrap rounded-md border border-amberx/40 bg-amberx/10 px-3 py-1.5">
      <span className="text-xs font-black tracking-widest text-amberx">AI PAPER TRADER · LIVE SIMULATION</span>
      <span className="text-2xs text-amberx/80">
        {compact ? "Virtual funds — no real transactions."
          : "Market data is live; execution is virtual. No wallet, no keys, no real orders — nothing here touches real funds."}
      </span>
    </div>
  );
}

export function StatTile({ label, value, sub, cls }: { label: string; value: React.ReactNode; sub?: string; cls?: string }) {
  return (
    <div className="rounded-md bg-line/15 border border-line/50 px-3 py-2">
      <div className="text-2xs font-bold tracking-wider text-dim uppercase">{label}</div>
      <div className={`num text-lg font-bold mt-0.5 ${cls || "text-ink"}`}>{value}</div>
      {sub && <div className="text-2xs text-dim mt-0.5 truncate">{sub}</div>}
    </div>
  );
}

/* ------------------------------------------------------------- equity curve */
export function EquityCurve({ points, base, h = 120 }: { points: { ts: number; equity: number; drawdown_pct?: number }[]; base: number; h?: number }) {
  if (!points || points.length < 2)
    return <div className="h-24 flex items-center justify-center text-dim text-xs">Collecting equity history — curve appears after the first ticks.</div>;
  const W = 640;
  const ys = points.map((p) => p.equity);
  const min = Math.min(...ys, base) * 0.998, max = Math.max(...ys, base) * 1.002;
  const xs = points.map((p) => p.ts);
  const xmin = Math.min(...xs), xmax = Math.max(...xs) || xmin + 1;
  const X = (t: number) => ((t - xmin) / (xmax - xmin)) * W;
  const Y = (v: number) => h - ((v - min) / (max - min)) * h;
  const path = points.map((p, i) => `${i ? "L" : "M"}${X(p.ts).toFixed(1)},${Y(p.equity).toFixed(1)}`).join(" ");
  const dd = points.map((p, i) => `${i ? "L" : "M"}${X(p.ts).toFixed(1)},${(h - ((p.drawdown_pct || 0) / Math.max(...points.map(q => q.drawdown_pct || 0), 1)) * h * 0.35).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  const up = last.equity >= base;
  return (
    <svg viewBox={`0 0 ${W} ${h}`} className="w-full" style={{ height: h }}>
      <path d={`${path} L${W},${h} L0,${h} Z`} fill={up ? "#34D39918" : "#F8717118"} />
      <path d={path} fill="none" stroke={up ? "#34D399" : "#F87171"} strokeWidth="1.8" />
      <path d={dd} fill="none" stroke="#F59E0B66" strokeWidth="1" strokeDasharray="3 3" />
      <line x1="0" x2={W} y1={Y(base)} y2={Y(base)} stroke="#6B7A9055" strokeDasharray="4 4" />
      <text x={W - 5} y={Y(last.equity) - 5} textAnchor="end" fontSize="11"
            className="num" fill={up ? "#34D399" : "#F87171"}>
        {usd(last.equity)}
      </text>
      <text x="5" y={Y(base) - 4} fontSize="9" fill="#6B7A90">start {usd(base, 0)}</text>
      <text x="5" y="h - 4" fontSize="8.5" fill="#F59E0B">--- drawdown</text>
    </svg>
  );
}

/* ------------------------------------------------------------- positions (§12) */
export function PositionsTable({ items, onClosed }: { items: any[]; onClosed?: () => void }) {
  const [busy, setBusy] = useState<number | null>(null);
  if (!items?.length)
    return <div className="p-4 text-dim text-xs">No open positions — the AI opens one when a signal passes the risk engine.</div>;
  const close = async (id: number) => {
    setBusy(id);
    try { await apiPost(`/paper/trades/${id}/close`); onClosed?.(); } catch (e) { reportError(e); } finally { setBusy(null); }
  };
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-2xs text-dim uppercase tracking-wider border-b border-line/60">
            <th className="text-left py-1.5 pl-3">Token</th><th className="text-right">Entry</th>
            <th className="text-right">Current</th><th className="text-right">Size</th>
            <th className="text-right">P&amp;L</th><th className="text-right">Conf</th>
            <th className="text-right">Stop</th><th className="text-right">Target</th>
            <th className="text-right">Hold</th><th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.id} className="border-b border-line/30 last:border-0 hover:bg-panel2/50">
              <td className="py-2 pl-3">
                <Link href={`/paper/trades/${p.id}`} className="font-bold text-cyanx hover:underline">${p.symbol}</Link>
              </td>
              <td className="num text-right">{px(p.entry_price)}</td>
              <td className="num text-right">{px(p.current_price)}</td>
              <td className="num text-right">{usd(p.size_usd, 0)}</td>
              <td className={`num text-right font-bold ${pnlCls(p.unrealized)}`}>
                {pnlSign(p.unrealized)} <span className="text-2xs font-normal">({p.unrealized_pct >= 0 ? "+" : ""}{p.unrealized_pct}%)</span>
              </td>
              <td className="num text-right">{Math.round(p.ai_confidence ?? 0)}%</td>
              <td className="num text-right text-redx/80">{px(p.stop_price)}</td>
              <td className="num text-right text-greenx/80">{px(p.target_price)}</td>
              <td className="num text-right">{holdStr(p.hold_secs)}</td>
              <td className="text-right pr-2">
                <button disabled={busy === p.id} onClick={() => close(p.id)}
                        className="pill-mini hover:!text-redx" title="Close at market (simulated)">
                  {busy === p.id ? "…" : "close"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------- trade rows */
export function TradesTable({ items, showStop }: { items: any[]; showStop?: boolean }) {
  if (!items?.length)
    return <div className="p-4 text-dim text-xs">No trades yet — closed trades appear here with full accounting.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-2xs text-dim uppercase tracking-wider border-b border-line/60">
            <th className="text-left py-1.5 pl-3">#</th><th className="text-left">Token</th>
            <th className="text-left">Entry (UTC)</th><th className="text-right">Entry</th>
            <th className="text-right">Exit</th><th className="text-right">Conf</th>
            <th className="text-right">P&amp;L</th><th className="text-left pl-2">Exit reason</th>
            <th className="text-right pr-3">Hold</th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.id} className="border-b border-line/30 last:border-0 hover:bg-panel2/50">
              <td className="py-1.5 pl-3 text-dim num">{t.id}</td>
              <td><Link href={`/paper/trades/${t.id}`} className="font-bold text-cyanx hover:underline">${t.symbol}</Link></td>
              <td className="text-dim text-2xs">{fmtUtc(t.entry_ts)}</td>
              <td className="num text-right">{px(t.entry_price)}</td>
              <td className="num text-right">{t.exit_price ? px(t.exit_price) : <Badge kind="green">open</Badge>}</td>
              <td className="num text-right">{Math.round(t.ai_confidence ?? 0)}%</td>
              <td className={`num text-right font-bold ${pnlCls(t.realized_pnl)}`}>
                {t.realized_pnl != null ? `${pnlSign(t.realized_pnl)} (${t.pnl_pct >= 0 ? "+" : ""}${t.pnl_pct}%)` : "—"}
              </td>
              <td className="pl-2 text-dim">{t.exit_reason?.replace(/_/g, " ") ?? "—"}</td>
              <td className="num text-right pr-3">{holdStr(t.hold_secs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------- timeline (§14) */
const KIND_COLORS: Record<string, string> = {
  BUY: "#34D399", SELL: "#F87171", PROFIT: "#4ADE80", LOSS: "#F43F5E",
  SIGNAL: "#38BDF8", RISK: "#F59E0B", POSITION: "#A78BFA", SYSTEM: "#64748B",
};
export function TradeTimeline({ limit = 40 }: { limit?: number }) {
  const [kinds, setKinds] = useState<Set<string>>(new Set());
  const q = kinds.size ? `&kinds=${[...kinds].join(",")}` : "";
  const { data } = useApi(`/paper/timeline?limit=${limit}${q}`, 12000);
  const items: any[] = data?.items || [];
  const ALL = ["SIGNAL", "RISK", "BUY", "SELL", "POSITION", "PROFIT", "LOSS", "SYSTEM"];
  return (
    <div>
      <div className="flex gap-1 flex-wrap px-3 pt-2">
        {ALL.map((k) => (
          <button key={k} className="pill-mini"
                  style={kinds.has(k) ? { color: KIND_COLORS[k], borderColor: KIND_COLORS[k] + "99", background: KIND_COLORS[k] + "14" } : {}}
                  onClick={() => { const n = new Set(kinds); n.has(k) ? n.delete(k) : n.add(k); setKinds(n); }}>
            {k}
          </button>
        ))}
      </div>
      <div className="max-h-[420px] overflow-y-auto mt-1">
        {items.length === 0 && <div className="p-4 text-dim text-xs">No events yet — the timeline narrates detection → analysis → decision → execution → result.</div>}
        {items.map((e) => (
          <div key={e.id} className="flex gap-2.5 px-3 py-1.5 border-b border-line/30 last:border-0 items-baseline">
            <span className="w-1.5 h-1.5 rounded-full shrink-0 mt-1" style={{ background: KIND_COLORS[e.kind] || "#64748B" }} />
            <span className="text-2xs text-dim num shrink-0 w-[132px]">{fmtUtc(e.ts).slice(5, -4)}</span>
            <div className="min-w-0">
              <Link href={e.position_id ? `/paper/trades/${e.position_id}` : "#"} className="text-xs font-bold text-ink/85 hover:text-cyanx block truncate">
                {e.title}
              </Link>
              <div className="text-2xs text-dim leading-snug line-clamp-2">{e.detail}</div>
            </div>
            <span className="pill-mini ml-auto shrink-0" style={{ color: KIND_COLORS[e.kind], borderColor: KIND_COLORS[e.kind] + "55" }}>{e.kind}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- AI signals (§4/§25) */
export function SignalsPanel({ limit = 14 }: { limit?: number }) {
  const { data } = useApi(`/paper/signals?limit=${limit}`, 15000);
  const items: any[] = data?.items || [];
  if (!items.length) return <div className="p-4 text-dim text-xs">No signals yet — AI signals appear as the intelligence engine fires them.</div>;
  return (
    <div className="max-h-[420px] overflow-y-auto">
      {items.map((s) => (
        <div key={s.id} className="px-3 py-2 border-b border-line/30 last:border-0">
          <div className="flex items-center gap-2">
            <span className="font-bold text-cyanx text-sm">${s.symbol}</span>
            <span className="text-xs text-ink/75 truncate">{s.kind?.replace(/_/g, " ")}</span>
            <span className={`pill-mini ml-auto ${s.status === "approved" ? "!text-greenx !border-greenx/50" : "!text-amberx !border-amberx/50"}`}>
              {s.status === "approved" ? "✓ traded" : "✕ rejected"}
            </span>
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-2xs text-dim">
            <span>conf {Math.round(s.confidence ?? 0)}%</span>
            <span>·</span><span>{s.importance}</span>
            <span>·</span><span className="num">{fmtUtc(s.ts).slice(11, -4)}</span>
          </div>
          {s.status === "rejected" && (
            <div className="text-2xs text-amberx/90 italic mt-0.5 truncate">risk: {s.reject_reason}</div>
          )}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------- error strip */
export function ErrStrip({ error, label }: { error?: string | null; label: string }) {
  if (!error) return null;
  const need803 = /401|403/.test(error);
  return (
    <div className="rounded-md border border-redx/40 bg-redx/10 px-3 py-2 text-xs text-redx leading-snug">
      ⚠ <span className="font-bold">{label}</span> — {need803 ? "not signed in (demo session) — retrying automatically…" : <>backend unreachable (<span className="num">{error}</span>) — retrying automatically…</>}
    </div>
  );
}

/* ------------------------------------------------------------- bars */
export function Bar({ pct, color = "#34D399", h = 6 }: { pct: number; color?: string; h?: number }) {
  return (
    <div className="w-full rounded-full bg-line/40" style={{ height: h }}>
      <div style={{ width: `${Math.min(100, Math.max(0, pct))}%`, height: h, background: color, borderRadius: 999 }} />
    </div>
  );
}

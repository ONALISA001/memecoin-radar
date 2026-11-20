"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { apiDelete, reportError, useApi } from "@/lib/api";
import { WatchlistIntel } from "@/components/intelfeed";
import { BAND_COLORS, bandLabel } from "@/components/marketintel";
import { RatingBadge, utcSeconds } from "@/components/verdict";
import { Badge, ConfChip, Empty, FomoPill, Panel, RiskPill, Sparkline, TokenAvatar, TrendPill } from "@/components/ui";
import { fmtPct, fmtPrice, fmtUsd, pctColor, timeAgo, trendColor } from "@/lib/format";

function Trajectory({ history }: { history: any[] }) {
  const pts = (history || []).map((h) => h.trend);
  if (pts.length < 2) return <span className="text-xs text-dim">collecting history…</span>;
  return (
    <div className="flex items-center gap-2">
      <Sparkline points={pts} w={90} h={26} color={trendColor(pts[pts.length - 1])} />
      <span className="num text-xs text-dim whitespace-nowrap">
        {pts.slice(-5).map((v) => Math.round(v)).join(" → ")}
      </span>
      {pts[pts.length - 1] > pts[0] ? <Badge kind="green">strengthening</Badge> : pts[pts.length - 1] < pts[0] ? <Badge kind="red">weakening</Badge> : null}
    </div>
  );
}

/** One-line state for a watched coin, from the same intelligence the feed uses. */
function stateOf(t: any): { label: string; kind: string } {
  const i = t.intel || {};
  const v = t.verdict?.rating;
  if (v === "RUG_RISK" || v === "AVOID") return { label: "Verdict says avoid", kind: "red" };
  if (i.sellp_band === "heavy_distribution" || (t.events_24h?.down || 0) >= 2) return { label: "Risk signals active", kind: "red" };
  if (i.sellp_band === "distribution") return { label: "Distribution watch", kind: "amber" };
  if (t.priority?.passed) return { label: "Priority: passes every gate", kind: "green" };
  if (i.momentum_band === "strong" || i.momentum_band === "extreme") return { label: "Momentum building", kind: "cyan" };
  if (i.momentum_band === "weakening" || i.momentum_band === "low") return { label: "Momentum fading", kind: "gray" };
  return { label: "Mixed / quiet", kind: "gray" };
}

function Band({ label, band, score }: { label: string; band?: string; score?: number | null }) {
  const c = BAND_COLORS[band || ""] || "#8593A8";
  return (
    <div className="min-w-0">
      <div className="text-2xs text-dim font-bold tracking-widest">{label}</div>
      <div className="text-xs font-semibold truncate" style={{ color: c }}>
        {band ? bandLabel(band) : "—"}{score != null ? <span className="num text-dim font-normal"> {Math.round(score)}</span> : null}
      </div>
    </div>
  );
}

const SORTS: [string, string][] = [["priority", "Priority score"], ["momentum", "Momentum"], ["risk", "Lowest risk"],
  ["change", "Price 1h"], ["events", "Most events 24h"], ["added", "Recently added"]];

export default function WatchlistPage() {
  const { data, loading } = useApi("/watchlist", 20000);
  const [busy, setBusy] = useState<string | null>(null);
  const [sort, setSort] = useState("priority");
  const [onlyPriority, setOnlyPriority] = useState(false);
  const raw: any[] = data?.items || [];
  const items = useMemo(() => {
    const key: Record<string, (t: any) => number> = {
      priority: (t) => t.priority?.score ?? -1, momentum: (t) => t.intel?.momentum ?? -1,
      risk: (t) => -(t.risk ?? 101), change: (t) => t.intel?.price_change_h1 ?? -1e9,
      events: (t) => (t.events_24h?.up || 0) + (t.events_24h?.down || 0),
      added: (t) => utcSeconds(t.added_at) ?? 0,
    };
    return raw.filter((t) => !onlyPriority || t.priority?.passed).sort((a, b) => key[sort](b) - key[sort](a));
  }, [raw, sort, onlyPriority]);
  const passing = raw.filter((t) => t.priority?.passed).length;
  async function remove(address: string) {
    setBusy(address);
    try { await apiDelete(`/watchlist/${address}`); location.reload(); } catch (e) { reportError(e); } finally { setBusy(null); }
  }
  return (
    <div className="space-y-4">
    <WatchlistIntel hours={24} />
    <Panel title={<span>Watchlist <span className="panel-sub">— {raw.length} coins · {passing} pass every priority gate</span></span>}
           right={raw.length > 0 && (
             <div className="flex items-center gap-2">
               <button className="pill-mini" aria-pressed={onlyPriority} onClick={() => setOnlyPriority(!onlyPriority)}
                       style={onlyPriority ? { color: "#34D399", borderColor: "#34D39999", background: "#34D3991c" } : {}}>⭐ priority only</button>
               <select className="sel !w-auto !py-1 !text-xs" aria-label="Sort watchlist" value={sort} onChange={(e) => setSort(e.target.value)}>
                 {SORTS.map(([v, l]) => <option key={v} value={v}>Sort: {l}</option>)}
               </select>
             </div>)}>
      {loading && raw.length === 0 ? <Empty text="Loading…" /> :
       raw.length === 0 ? (
        <div className="p-8 text-center text-dim text-sm">
          Nothing watched yet. Open any token and hit <span className="text-cyanx">☆ Watch</span>, or use <span className="text-cyanx">+ Watch</span> on a feed event.
        </div>
      ) : items.length === 0 ? <Empty text="No watched coin passes every priority gate right now." /> : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-0 divide-x divide-y divide-line/60">
          {items.map((t: any) => {
            const i = t.intel || {};
            const st = stateOf(t);
            const vt = utcSeconds(t.verdict?.created_at);
            return (
              <div key={t.address} className="p-4 hover:bg-panel2/60 space-y-3">
                <div className="flex items-center gap-3">
                  <TokenAvatar url={t.image_url} symbol={t.symbol} size={34} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Link href={`/token/${t.address}`} className="font-bold text-lg hover:text-cyanx">${t.symbol}</Link>
                      <Badge kind={st.kind}>{st.label}</Badge>
                    </div>
                    <div className="text-sm text-dim truncate">{t.name} · {t.chain}</div>
                  </div>
                  <ConfChip level={(t.summary || {}).confidence} />
                  <button className="btn btn-danger !py-1 !px-2 !text-xs" disabled={busy === t.address}
                          onClick={() => remove(t.address)}>remove</button>
                </div>

                <div className="flex items-end justify-between gap-3">
                  <div>
                    <div className="num text-base font-semibold">{fmtPrice(i.price)}</div>
                    <div className="text-xs num flex gap-2">
                      <span className={pctColor(i.price_change_h1)}>1h {fmtPct(i.price_change_h1)}</span>
                      <span className={pctColor(i.price_change_h24)}>24h {fmtPct(i.price_change_h24)}</span>
                    </div>
                  </div>
                  <div className="text-right text-xs num text-dim">
                    <div>liq {fmtUsd(i.liquidity, 0)}</div>
                    <div>vol 1h {fmtUsd(i.volume_1h, 0)}</div>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-2">
                  <div><div className="text-2xs text-dim font-bold tracking-widest">TREND</div><TrendPill value={t.trend} /></div>
                  <div><div className="text-2xs text-dim font-bold tracking-widest">FOMO</div><FomoPill value={t.fomo} cls={t.fomo_class} /></div>
                  <div><div className="text-2xs text-dim font-bold tracking-widest">RISK</div><RiskPill value={t.risk} cls={t.risk_class} /></div>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  <Band label="MOMENTUM" band={i.momentum_band} score={i.momentum} />
                  <Band label="FLOW" band={i.sellp_band} score={i.sellp} />
                  <Band label="LIQUIDITY" band={i.liq_band} score={i.liq_health} />
                  <Band label="WHALES" band={i.whale_class} />
                </div>

                <div className="rounded-md border border-line/60 px-3 py-2 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap text-xs">
                    <span className="text-dim">Verdict</span>
                    {t.verdict ? <RatingBadge rating={t.verdict.rating} /> : <span className="text-dim">not judged yet</span>}
                    {vt && <span className="text-2xs text-dim">{timeAgo(vt)}</span>}
                    <span className="ml-auto num text-2xs">
                      <span className="text-greenx">{t.events_24h?.up ?? 0}↑</span> <span className="text-redx">{t.events_24h?.down ?? 0}↓</span>
                      <span className="text-dim"> events 24h</span>
                    </span>
                  </div>
                  {t.verdict?.reason && <div className="text-2xs text-dim line-clamp-2">{t.verdict.reason}</div>}
                  {t.priority && (
                    <div className="text-2xs">
                      {t.priority.passed
                        ? <span className="text-greenx">⭐ Passes every priority gate (score {Math.round(t.priority.score ?? 0)})</span>
                        : <span className="text-dim">Priority gates missed: {(t.priority.failed || []).join(" · ") || "—"}</span>}
                    </div>
                  )}
                </div>

                <div className="flex items-center justify-between gap-3">
                  <Trajectory history={t.score_history} />
                  <div className="flex gap-2 text-xs">
                    <Link href={`/compare?tokens=${t.address}`} className="text-dim hover:text-cyanx hover:underline">Compare</Link>
                    <Link href={`/alerts?token=${t.address}`} className="text-dim hover:text-cyanx hover:underline">Alert</Link>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
    </div>
  );
}

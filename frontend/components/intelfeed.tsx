"use client";
/* v3 intelligence surfaces (§20–§24, §28, §52): rich event cards with
   importance/confidence/why/concern, Market Pulse, filter bar, token
   highlight mode. All data comes from the event engine — observed/derived
   signals only (§38), with inferred signals labeled. */
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { apiPost, useApi } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Panel } from "@/components/ui";

/* ------------------------------------------------ meta (§20 card design) */
export const CAT_META: Record<string, { icon: string; label: string; color: string }> = {
  new:           { icon: "🆕", label: "New token",     color: "#22D3EE" },
  momentum:      { icon: "⚡", label: "Momentum",      color: "#A78BFA" },
  volume:        { icon: "📈", label: "Volume",        color: "#60A5FA" },
  flow:          { icon: "🟢", label: "Buy/sell flow", color: "#34D399" },
  whales:        { icon: "🐋", label: "Whales",        color: "#38BDF8" },
  holders:       { icon: "👥", label: "Holders",       color: "#4ADE80" },
  liquidity:     { icon: "💧", label: "Liquidity",     color: "#2DD4BF" },
  ath:           { icon: "🏆", label: "New high",      color: "#FBBF24" },
  reversal:      { icon: "🔄", label: "Reversal",      color: "#FB923C" },
  distribution:  { icon: "🔴", label: "Distribution",  color: "#F87171" },
  risk:          { icon: "⚠️", label: "Risk",          color: "#F87171" },
  pattern:       { icon: "🧩", label: "Chain",         color: "#E879F9" },
  paper:         { icon: "🤖", label: "Paper trade",   color: "#C084FC" },
  uncategorized: { icon: "•",  label: "Event",         color: "#64748B" },
};
export const IMP_STYLE: Record<string, { color: string; label: string }> = {
  CRITICAL: { color: "#F43F5E", label: "CRITICAL" },
  HIGH:     { color: "#F59E0B", label: "HIGH" },
  MEDIUM:   { color: "#38BDF8", label: "MEDIUM" },
  LOW:      { color: "#64748B", label: "LOW" },
};
const SEV_BAR: Record<string, string> = {
  danger: "#F43F5E", warning: "#F59E0B", positive: "#34D399", info: "#38BDF8",
};
/* event kind → alert-rule prefill (alerts page metric set) */
const ALERT_MAP: Record<string, [string, number]> = {
  momentum_acceleration: ["trend_score", 70],
  unusual_volume:        ["volume_growth_pct", 100],
  holder_growth:         ["holder_growth_pct", 10],
  liquidity_drop:        ["liquidity_drop_pct", -20],
  sell_pressure_increasing: ["risk_score", 65],
  elevated_risk:         ["risk_score", 80],
};

const METRIC_LABEL: Record<string, (v: number) => string> = {
  momentum: (v) => `mom ${Math.round(v)}`,
  sell_pressure: (v) => `sellp ${Math.round(v)}`,
  liq_health: (v) => `liqh ${Math.round(v)}`,
  buy_share: (v) => `buy ${Math.round(v)}%`,
  holders_g_1h: (v) => `hld ${v > 0 ? "+" : ""}${v.toFixed(1)}%`,
  volume_g_1h: (v) => `vol ${v > 0 ? "+" : ""}${Math.round(v)}%`,
  risk: (v) => `risk ${Math.round(v)}`,
};
const METRIC_KEYS = ["momentum", "sell_pressure", "liq_health", "buy_share", "holders_g_1h", "volume_g_1h", "risk"];

export function confColor(c: number | null | undefined): string {
  if (c == null) return "#64748B";
  return c >= 75 ? "#34D399" : c >= 55 ? "#FBBF24" : "#FB923C";
}

function Chip({ children, color, title }: { children: React.ReactNode; color: string; title?: string }) {
  return (
    <span className="pill-mini" title={title}
          style={{ color, borderColor: color + "55", background: color + "12" }}>
      {children}
    </span>
  );
}

/* ------------------------------------------------- rich event card §20 */
export function EventCard({ e, focus }: { e: any; focus?: boolean }) {
  const [open, setOpen] = useState(!!focus);
  const [watched, setWatched] = useState(false);
  const cat = CAT_META[e.category || "uncategorized"] || CAT_META.uncategorized;
  const imp = IMP_STYLE[e.importance || "LOW"] || IMP_STYLE.LOW;
  const m = e.metrics || {};
  const chips = METRIC_KEYS.filter((k) => typeof m[k] === "number").slice(0, 4);
  const alertPrefill = ALERT_MAP[e.kind];
  return (
    <div id={`event-${e.id}`}
         className={`mx-3 sm:mx-4 my-2 rounded-lg border border-line/70 bg-panel2/60 transition-shadow ${focus ? "ring-2 ring-cyanx/70" : ""}`}
         style={{ borderLeft: `3px solid ${SEV_BAR[e.severity] || SEV_BAR.info}` }}>
      <div className="px-3 pt-2.5 pb-2">
        {/* header */}
        <div className="flex items-start gap-2">
          <span className="text-sm leading-none mt-0.5" title={cat.label}>{cat.icon}</span>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[11.5px] font-bold text-ink/90">{e.title}</span>
              <span className="text-[9px] text-dim num ml-auto shrink-0">{timeAgo(e.ts)}</span>
            </div>
            <div className="flex items-center gap-1 flex-wrap mt-1">
              <Chip color="#22D3EE"><a href={`/token/${e.address}`} className="hover:underline font-bold">${e.symbol}</a></Chip>
              <Chip color={imp.color} title={`importance score ${e.importance_score}/100`}>{imp.label}</Chip>
              {e.confidence != null && e.category && (
                <Chip color={confColor(e.confidence)} title="confidence = share of supporting indicators that fired, capped by token age">
                  {e.confidence}% conf
                </Chip>
              )}
              {e.category && <Chip color={cat.color}>{cat.label}</Chip>}
              {e.chain && <Chip color="#E879F9" title="multi-event signal chain (§16)">🔗 chain</Chip>}
              {e.key && e.kind === "unusual_volume" && e.key !== "1x" && (
                <Chip color="#60A5FA" title="escalation bucket — re-alerted because magnitude increased (§43)">↑ {e.key}</Chip>
              )}
            </div>
          </div>
        </div>

        {/* signal statement */}
        <p className="text-[11px] text-ink/80 leading-snug mt-1.5">{(e.signal_text || e.detail || "").slice(0, 240)}</p>

        {/* metric chips */}
        {chips.length > 0 && (
          <div className="flex gap-1 flex-wrap mt-1.5">
            {chips.map((k) => (
              <span key={k} className="pill-mini text-dim" title={k}>{METRIC_LABEL[k](m[k])}</span>
            ))}
          </div>
        )}

        {/* actions */}
        <div className="flex items-center gap-3 mt-2 text-[10px]">
          {(e.checklist?.length > 0 || e.why?.length > 0 || e.concern) && (
            <button onClick={() => setOpen(!open)} className="text-cyanx hover:underline font-bold">
              {open ? "Hide why ▾" : "Why detected ▸"}
            </button>
          )}
          <Link href={`/token/${e.address}`} className="text-dim hover:text-ink hover:underline">View token</Link>
          <button className={`hover:underline ${watched ? "text-greenx" : "text-dim hover:text-ink"}`}
                  onClick={async () => { try { await apiPost("/watchlist", { address: e.address }); setWatched(true); } catch {} }}>
            {watched ? "✓ Watched" : "+ Watch"}
          </button>
          <Link href={alertPrefill
                ? `/alerts?token=${e.address}&metric=${alertPrefill[0]}&value=${alertPrefill[1]}&op=${alertPrefill[1] < 0 ? "lte" : "gte"}`
                : `/alerts?token=${e.address}`}
                className="text-dim hover:text-ink hover:underline">Alert</Link>
          <Link href={`/compare?tokens=${e.address}`} className="text-dim hover:text-ink hover:underline">Compare</Link>
        </div>

        {/* expandable why/checklist/concern (§52: WHAT/WHY/contradiction) */}
        {open && (
          <div className="mt-2 rounded-md bg-line/20 border border-line/50 p-2.5 space-y-2">
            {e.checklist?.length > 0 && (
              <div>
                <div className="text-[9px] font-bold tracking-wider text-dim mb-1">SUPPORTING CHECKS</div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                  {e.checklist.map((c: any, i: number) => (
                    <div key={i} className="text-[10px] flex gap-1 items-baseline min-w-0">
                      <span style={{ color: c.passed ? "#34D399" : "#64748B" }}>{c.passed ? "✓" : "✗"}</span>
                      <span className="text-ink/75 truncate">{c.label}</span>
                      <span className="text-dim num text-[9px] truncate ml-auto">{c.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {e.why?.length > 0 && (
              <div>
                <div className="text-[9px] font-bold tracking-wider text-dim mb-1">WHY</div>
                <ul className="space-y-0.5">
                  {e.why.map((w: string, i: number) => (
                    <li key={i} className="text-[10px] text-ink/70 leading-snug">• {w}</li>
                  ))}
                </ul>
              </div>
            )}
            {e.concern && (
              <div className="text-[10px] text-amberx/90 italic border-t border-line/40 pt-1.5">
                ⚠ Possible contradiction: {e.concern}
              </div>
            )}
            <div className="text-[8.5px] text-dim/80 border-t border-line/40 pt-1">
              Observed & derived metrics; whale flow is inferred from aggregates — analytics, not financial advice.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------- Market Pulse §22 */
export function MarketPulse({ window: win, onWindow }: { window: string; onWindow: (w: string) => void }) {
  const { data } = useApi(`/signals/pulse?window=${win}`, 20000);
  if (!data) return <Panel title={<span>🌐 Market Pulse</span>}><div className="p-4 text-dim text-xs">loading…</div></Panel>;
  const imp = data.by_importance || {};
  const total = Math.max(data.total_events || 0, 1);
  const pos = Math.round((data.direction_balance?.positive_share || 0) * 100);
  const dgr = Math.round((data.direction_balance?.danger_share || 0) * 100);
  const cats = Object.entries(data.by_category || {}).sort((a: any, b: any) => b[1] - a[1]).slice(0, 7);
  return (
    <Panel title={<span>🌐 Market Pulse <span className="text-dim font-normal text-[10px]">— aggregate signal flow, last {win}</span></span>}
           right={
             <div className="flex gap-0.5">
               {["15m", "1h", "4h", "24h"].map((w) => (
                 <button key={w} onClick={() => onWindow(w)}
                         className={`pill-mini ${w === win ? "!text-cyanx !border-cyanx/60" : ""}`}>{w}</button>
               ))}
             </div>
           }>
      <div className="px-4 py-3 space-y-3">
        <div className="grid grid-cols-4 gap-2 text-center">
          {[
            [data.total_events ?? "—", "events"],
            [(imp.HIGH || 0) + (imp.CRITICAL || 0), "high+critical"],
            [data.chains_detected ?? 0, "chains 🔗"],
            [data.avg_confidence != null ? `${Math.round(data.avg_confidence)}%` : "—", "avg confidence"],
          ].map(([v, l], i) => (
            <div key={i} className="rounded-md bg-line/15 border border-line/40 py-1.5">
              <div className="num text-sm font-bold text-ink">{v}</div>
              <div className="text-[8.5px] text-dim uppercase tracking-wider">{l}</div>
            </div>
          ))}
        </div>
        {/* importance mix */}
        <div>
          <div className="text-[9px] font-bold tracking-wider text-dim mb-1">IMPORTANCE MIX</div>
          <div className="h-2 rounded-full overflow-hidden flex bg-line/30">
            {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map((k) => (
              <div key={k} style={{ width: `${(imp[k] || 0) / total * 100}%`, background: IMP_STYLE[k].color }}
                   title={`${k}: ${imp[k] || 0}`} />
            ))}
          </div>
          <div className="flex justify-between text-[8.5px] text-dim mt-0.5">
            <span>critical {imp.CRITICAL || 0}</span><span>high {imp.HIGH || 0}</span>
            <span>medium {imp.MEDIUM || 0}</span><span>low {imp.LOW || 0}</span>
          </div>
        </div>
        {/* direction balance */}
        <div>
          <div className="text-[9px] font-bold tracking-wider text-dim mb-1">BULLISH vs RISK SIGNALS</div>
          <div className="h-2 rounded-full overflow-hidden flex bg-line/30">
            <div style={{ width: `${pos}%`, background: "#34D399" }} title={`positive ${pos}%`} />
            <div style={{ width: `${dgr}%`, background: "#F43F5E" }} title={`danger ${dgr}%`} />
          </div>
          <div className="flex justify-between text-[8.5px] mt-0.5">
            <span className="text-greenx">{pos}% constructive</span>
            <span className="text-redx">{dgr}% risk/distribution</span>
          </div>
        </div>
        {/* categories + top tokens */}
        {cats.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {cats.map(([c, n]: any) => (
              <Chip key={c} color={(CAT_META[c] || CAT_META.uncategorized).color}>
                {(CAT_META[c] || {}).icon} {c} {n}
              </Chip>
            ))}
          </div>
        )}
        {data.top_tokens?.length > 0 && (
          <div>
            <div className="text-[9px] font-bold tracking-wider text-dim mb-1">MOST ACTIVE</div>
            <div className="flex gap-1 flex-wrap">
              {data.top_tokens.map((t: any) => (
                <span key={t.symbol} className="pill-mini text-ink/80"
                      title={`${t.events} events · ${t.danger} danger · ${t.positive} positive`}>
                  ${t.symbol} {t.danger > 0 ? "🔴" : t.positive > t.danger ? "🟢" : "⚪"} {t.events}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}

/* ------------------------------------------------- feed filters §23–24 */
export interface FeedFilters {
  categories: string[]; importance: string; age: string; momentum: string;
  sort: string; search: string;
}
const SORTS: [string, string][] = [
  ["smart", "smart (importance × recency)"], ["recent", "most recent"],
  ["importance", "importance"], ["confidence", "confidence"],
];
const MOM_BANDS: [string, string][] = [
  ["", "any momentum"], ["extreme", "extreme"], ["strong", "strong"],
  ["developing", "developing"], ["weakening", "weakening"], ["low", "low"],
];

export function FeedFilterBar({ f, setF }: { f: FeedFilters; setF: (g: FeedFilters) => void }) {
  const toggleCat = (c: string) =>
    setF({ ...f, categories: f.categories.includes(c) ? f.categories.filter((x) => x !== c) : [...f.categories, c] });
  return (
    <div className="space-y-2">
      <div className="flex gap-1 flex-wrap">
        {Object.entries(CAT_META).filter(([k]) => k !== "uncategorized").map(([k, m]) => (
          <button key={k} onClick={() => toggleCat(k)} className="pill-mini"
                  style={f.categories.includes(k)
                    ? { color: m.color, borderColor: m.color + "99", background: m.color + "1c" } : {}}>
            {m.icon} {m.label}
          </button>
        ))}
      </div>
      <div className="flex gap-2 flex-wrap items-center">
        <select className="sel !w-auto" value={f.importance} onChange={(e) => setF({ ...f, importance: e.target.value })}>
          {["", "LOW", "MEDIUM", "HIGH", "CRITICAL"].map((v) => (
            <option key={v} value={v}>{v ? `≥ ${v}` : "any importance"}</option>
          ))}
        </select>
        <select className="sel !w-auto" value={f.momentum} onChange={(e) => setF({ ...f, momentum: e.target.value })}>
          {MOM_BANDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select className="sel !w-auto" value={f.age} onChange={(e) => setF({ ...f, age: e.target.value })}>
          {[["", "any age"], ["new", "new <3h"], ["young", "young <24h"], ["established", "established ≥24h"]].map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
        <select className="sel !w-auto" value={f.sort} onChange={(e) => setF({ ...f, sort: e.target.value })}>
          {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <input className="inp !w-44" placeholder="search symbol / title…" value={f.search}
               onChange={(e) => setF({ ...f, search: e.target.value })} />
        {(f.categories.length > 0 || f.importance || f.momentum || f.age || f.search) && (
          <button className="text-[10px] text-dim hover:text-ink hover:underline"
                  onClick={() => setF({ categories: [], importance: "", age: "", momentum: "", sort: f.sort, search: "" })}>
            reset
          </button>
        )}
      </div>
    </div>
  );
}

export function feedQuery(f: FeedFilters, extra = ""): string {
  const p = new URLSearchParams();
  if (f.categories.length) p.set("categories", f.categories.join(","));
  if (f.importance) p.set("importance", f.importance);
  if (f.age) p.set("age", f.age);
  if (f.momentum) p.set("momentum", f.momentum);
  if (f.sort) p.set("sort", f.sort);
  if (f.search.trim()) p.set("search", f.search.trim());
  const s = p.toString();
  return s ? `&${s}` : "";
}

/* ------------------------------------------------- main feed list */
export function IntelFeed({ address, compact, focusId }: { address?: string; compact?: number; focusId?: number }) {
  const [f, setF] = useState<FeedFilters>({ categories: [], importance: "", age: "", momentum: "", sort: "smart", search: "" });
  const [win, setWin] = useState("1h");
  const q = useMemo(() => {
    const lim = compact || 120;
    const base = `/signals/feed?limit=${lim}${address ? `&address=${address}` : ""}`;
    const extra = address ? "" : feedQuery(f);
    return base + (extra.startsWith("&") ? extra : extra ? `&${extra}` : "");
  }, [f, address, compact]);
  const { data } = useApi(q, 15000);
  const items: any[] = data?.items || [];

  useEffect(() => {
    if (focusId) {
      const el = document.getElementById(`event-${focusId}`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [focusId, items.length]);

  const seenFocus = focusId && items.some((e) => e.id === focusId);
  return (
    <div className="space-y-3">
      {!address && !compact && <MarketPulse window={win} onWindow={setWin} />}
      {!address && !compact && <FeedFilterBar f={f} setF={setF} />}
      <Panel title={<span>📡 {address ? "Intelligence timeline" : compact ? "Live intelligence" : "Live Intelligence Feed"}
        <span className="text-dim font-normal text-[10px]"> — {items.length} events{!compact && data?.filters ? ` · sort: ${f.sort}` : ""}</span></span>}
           right={compact ? <Link href="/feed" className="text-[10px] text-cyanx hover:underline">open full feed →</Link> : undefined}>
        {items.length === 0 && (
          <div className="p-5 text-dim text-xs">
            No events match — the engine only writes when observed signals cross thresholds (never fills silence).
          </div>
        )}
        <div className="max-h-[70vh] overflow-y-auto">
          {items.map((e: any) => (
            <EventCard key={e.id ?? `${e.ts}-${e.kind}-${e.symbol}`} e={e} focus={e.id === focusId && !!seenFocus} />
          ))}
        </div>
        {items.length > 0 && !focusId && (
          <div className="px-4 py-2 text-[9px] text-dim border-t border-line/40">
            Signals reflect observed/derived metrics; whale flows are aggregate inferences. Analytics only — not financial advice.
          </div>
        )}
      </Panel>
    </div>
  );
}

/* ------------------------------------------------- watchlist intelligence §30 */
export function WatchlistIntel({ hours = 24 }: { hours?: number }) {
  const { data } = useApi(`/signals/watchlist?hours=${hours}`, 30000, true);
  const items: any[] = data?.items || [];
  return (
    <Panel title={<span>🧠 Watchlist Intelligence <span className="text-dim font-normal text-[10px]">— events on your tokens, last {hours}h</span></span>}>
      {items.length === 0 && <div className="p-4 text-dim text-xs">Watch tokens to see their event intelligence here.</div>}
      {items.length > 0 && items.every((t) => t.event_count === 0) && (
        <div className="px-4 pt-3 text-[11px] text-dim">Quiet period — no MEDIUM+ events on watched tokens in {hours}h. Silence is data.</div>
      )}
      <div className="divide-y divide-line/50">
        {items.filter((t) => t.event_count > 0).map((t) => (
          <div key={t.address} className="px-4 py-2.5">
            <div className="flex items-center gap-2 flex-wrap">
              <a href={`/token/${t.address}`} className="font-bold text-cyanx text-[12px] hover:underline">${t.symbol}</a>
              <span className={`pill-mini ${t.danger_count >= 2 ? "!text-redx !border-redx/50" : t.positive_count >= 2 ? "!text-greenx !border-greenx/50" : ""}`}>
                {t.headline}
              </span>
              <span className="text-[9px] text-dim ml-auto num">{t.event_count} events · max {t.max_importance}</span>
            </div>
            <div className="mt-1.5 space-y-1">
              {t.events.slice(0, 3).map((e: any) => {
                const imp = IMP_STYLE[e.importance || "LOW"] || IMP_STYLE.LOW;
                return (
                  <a key={e.id} href={`/token/${t.address}?event=${e.id}`}
                     className="flex items-center gap-2 text-[10.5px] group">
                    <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: SEV_BAR[e.severity] || SEV_BAR.info }} />
                    <span className="text-ink/80 truncate group-hover:text-cyanx">{e.title}</span>
                    <span className="pill-mini !py-0.5 shrink-0" style={{ color: imp.color, borderColor: imp.color + "55" }}>{imp.label}</span>
                    <span className="text-[9px] text-dim num shrink-0">{timeAgo(e.ts)}</span>
                  </a>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ------------------------------------------------- signal quality §50 */
export function SignalQualityPanel() {
  const { data } = useApi("/signals/quality", 60000);
  if (!data) return null;
  const cats = Object.entries(data.by_category || {}).filter(([c]) => c !== "uncategorized")
    .sort((a: any, b: any) => b[1] - a[1]);
  const rets = Object.entries(data.avg_forward_return_by_category || {})
    .filter(([_, v]: any) => v?.ret_1h != null)
    .sort((a: any, b: any) => (b[1].ret_1h ?? 0) - (a[1].ret_1h ?? 0));
  return (
    <Panel title={<span>📏 Signal quality — event engine <span className="text-dim font-normal text-[10px]">(last 24h, §50)</span></span>}>
      <div className="p-4 space-y-4">
        <div className="grid grid-cols-4 gap-2 text-center">
          {[
            [data.events_24h ?? "—", "events 24h"],
            [data.labeling?.outcomes_24h ?? 0, "outcomes labeled"],
            [`${((data.dedupe?.dup_rate ?? 0) * 100).toFixed(1)}%`, "dup rate"],
            [data.freshness_secs != null ? `${Math.max(0, Math.round(data.freshness_secs / 60))}m` : "—", "feed freshness"],
          ].map(([v, l], i) => (
            <div key={i} className="rounded-md bg-line/15 border border-line/40 py-1.5">
              <div className="num text-sm font-bold text-ink">{v}</div>
              <div className="text-[8.5px] text-dim uppercase tracking-wider">{l}</div>
            </div>
          ))}
        </div>
        {cats.length > 0 && (
          <div>
            <div className="text-[9px] font-bold tracking-wider text-dim mb-1">EVENTS BY CATEGORY · AVG CONFIDENCE</div>
            <table className="w-full text-[10.5px]">
              <tbody>
                {cats.map(([c, n]: any) => (
                  <tr key={c} className="border-b border-line/30 last:border-0">
                    <td className="py-1">{(CAT_META[c] || {}).icon} {c}</td>
                    <td className="num text-right">{n}</td>
                    <td className="num text-right" style={{ color: confColor(data.avg_confidence_by_category?.[c]) }}>
                      {data.avg_confidence_by_category?.[c] != null ? `${Math.round(data.avg_confidence_by_category[c])}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data.outcome_stats_by_horizon?.length > 0 && (
          <div>
            <div className="text-[9px] font-bold tracking-wider text-dim mb-1">OUTCOME LABELING BY HORIZON (§49)</div>
            <table className="w-full text-[10.5px]">
              <thead><tr className="text-dim text-[9px]">
                <th className="text-left">horizon</th><th className="text-right">labeled</th>
                <th className="text-right">avg fwd ret</th><th className="text-right">avg max dd</th>
              </tr></thead>
              <tbody>
                {data.outcome_stats_by_horizon.map((h: any) => (
                  <tr key={h.horizon_secs} className="border-b border-line/30 last:border-0">
                    <td className="py-1 num">+{h.horizon_secs / 60}m</td>
                    <td className="num text-right">{h.labeled}</td>
                    <td className={`num text-right font-bold ${(h.avg_fwd_ret_pct ?? 0) >= 0 ? "text-greenx" : "text-redx"}`}>
                      {h.avg_fwd_ret_pct != null ? `${h.avg_fwd_ret_pct > 0 ? "+" : ""}${h.avg_fwd_ret_pct}%` : "—"}
                    </td>
                    <td className="num text-right text-redx/90">{h.avg_max_dd_pct != null ? `${h.avg_max_dd_pct}%` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-[9px] text-dim mt-1">
              pending first-horizon labels: {data.labeling?.pending_min_horizon ?? 0} · horizons are +15m/+1h/+4h/+24h
            </div>
          </div>
        )}
        {rets.length > 0 && (
          <div>
            <div className="text-[9px] font-bold tracking-wider text-dim mb-1">AVG +1H RETURN AFTER EVENT, BY CATEGORY</div>
            <div className="space-y-1">
              {rets.map(([c, v]: any) => (
                <div key={c} className="flex items-center gap-2 text-[10.5px]">
                  <span className="w-24 truncate">{(CAT_META[c] || {}).icon} {c}</span>
                  <div className="flex-1 h-1.5 rounded bg-line/30 overflow-hidden">
                    <div style={{
                      width: `${Math.min(100, Math.abs(v.ret_1h) * 4)}%`,
                      background: v.ret_1h >= 0 ? "#34D399" : "#F87171",
                      marginLeft: v.ret_1h >= 0 ? 0 : "auto",
                    }} />
                  </div>
                  <span className={`num w-16 text-right font-bold ${v.ret_1h >= 0 ? "text-greenx" : "text-redx"}`}>
                    {v.ret_1h > 0 ? "+" : ""}{v.ret_1h}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        <p className="text-[9px] text-dim leading-snug border-t border-line/40 pt-2">
          Dedupe health measures same-(token,kind) refires inside cooldown windows (§43 target ~0%).
          Forward returns are descriptive statistics over detected events — a validation loop, not a promise.
        </p>
      </div>
    </Panel>
  );
}

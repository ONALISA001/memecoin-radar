"use client";
import Link from "next/link";
import React, { useState } from "react";
import { useApi } from "@/lib/api";
import { ageLabel, fmtNum, fmtPct, fmtPrice, fmtUsd, pctColor, timeAgo } from "@/lib/format";
import { Badge, Empty, Panel, RiskPill, SimTag, SkeletonRows, TokenAvatar, TrendPill } from "./ui";
import { IntelFeed } from "./intelfeed";
import { BAND_COLORS, bandLabel } from "./marketintel";
import { PriorityCoins, WindowPills } from "./priority";
import { ChevronDown, Flame } from "lucide-react";

const C = { green: "#34D399", red: "#F87171", amber: "#FBBF24", cyan: "#22D3EE", violet: "#A78BFA", ink: "#E6ECF3" };

/* ------------------------------------------------ market overview */
function StatTile({ label, value, color, hint, className = "" }: { label: string; value: React.ReactNode; color?: string; hint?: string; className?: string }) {
  return (
    <div className={`panel px-4 py-3.5 ${className}`} title={hint}>
      <div className="text-xs text-dim">{label}</div>
      <div className="num text-2xl font-semibold mt-1" style={{ color: color || C.ink }}>{value}</div>
    </div>
  );
}

function OverviewStrip() {
  const { data } = useApi("/market/overview", 30000);
  const { data: sources } = useApi("/sources", 60000);
  const [more, setMore] = useState(false);
  if (!data || !data.tracked) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="panel px-4 py-3.5 space-y-2"><div className="skel h-3 w-20" /><div className="skel h-6 w-16" /></div>
        ))}
      </div>
    );
  }
  const phaseColor = data.phase === "RISK-ON" ? C.green : data.phase === "RISK-OFF" ? C.red : C.amber;
  const extra = [
    { l: "Momentum", v: `${Math.round(data.sector_momentum)}/100` },
    { l: "Tracked", v: data.tracked },
    { l: "Avg trend", v: data.avg_trend },
    { l: "Emerging", v: data.emerging },
    { l: "Extreme FOMO", v: data.extreme_fomo },
  ];
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatTile label="Market phase" value={data.phase} color={phaseColor} />
        <StatTile label="24h volume" value={fmtUsd(data.total_volume_24h)} />
        <StatTile label="Breadth (24h)" value={`${Math.round(data.breadth * 100)}%`} color={data.breadth > 0.5 ? C.green : C.red}
                  hint="Share of tracked tokens up over 24h" />
        <StatTile label="High risk" value={data.high_risk} color={data.high_risk ? C.red : undefined} />
        <StatTile label="Anomalies" value={data.anomalies} color={data.anomalies ? C.amber : undefined} className="col-span-2 md:col-span-1" />
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-1 text-xs text-dim">
        {(sources?.sources || []).map((s: any) => (
          <span key={s.name} className="flex items-center gap-1.5" title={s.meta?.note || ""}>
            <span className="w-2 h-2 rounded-full" style={{ background: s.status === "ok" ? C.green : s.status === "mock" ? C.amber : C.red }} />
            {s.name}{s.status === "mock" && <Badge kind="amber">mock</Badge>}
          </span>
        ))}
        <button className="inline-flex items-center gap-1 hover:text-ink" onClick={() => setMore(!more)}>
          {more ? "Fewer stats" : "More stats"} <ChevronDown size={13} className={more ? "rotate-180" : ""} />
        </button>
        {data.updated_at && <span className="ml-auto">Updated {timeAgo(data.updated_at)}</span>}
      </div>
      {more && (
        <div className="panel flex flex-wrap gap-x-8 gap-y-2 px-4 py-3">
          {extra.map((e) => (
            <div key={e.l}><span className="text-xs text-dim">{e.l}</span> <span className="num text-sm font-semibold ml-1">{e.v}</span></div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------ token row */
function topBadges(t: any): { kind: string; label: string }[] {
  const b: { kind: string; label: string }[] = [];
  if (t.risk >= 70) b.push({ kind: "red", label: "High risk" });
  if (t.regime === "distribution") b.push({ kind: "red", label: "Distribution" });
  if (typeof t.organic === "number" && t.organic < 40) b.push({ kind: "amber", label: "Manufactured?" });
  if ((t.anomalies || []).length > 0) b.push({ kind: "amber", label: "Anomaly" });
  if (t.conflicts > 0) b.push({ kind: "amber", label: "Conflict" });
  if (t.emerging) b.push({ kind: "violet", label: "Early" });
  if (t.fomo >= 81) b.push({ kind: "red", label: "Extreme FOMO" });
  if (t.trend >= 65) b.push({ kind: "cyan", label: "Trending" });
  return b;
}

function Signals({ t }: { t: any }) {
  const b = topBadges(t);
  if (!b.length) return <span className="text-dim">—</span>;
  return (
    <div className="flex gap-1 items-center justify-end">
      <Badge kind={b[0].kind}>{b[0].label}</Badge>
      {b.length > 1 && <span className="text-2xs text-dim" title={b.slice(1).map((x) => x.label).join(", ")}>+{b.length - 1}</span>}
    </div>
  );
}

function TokenCell({ t }: { t: any }) {
  return (
    <Link href={`/token/${t.address}`} className="flex items-center gap-3 min-w-0">
      <TokenAvatar url={t.image_url} symbol={t.symbol} size={30} />
      <div className="min-w-0">
        <div className="font-semibold text-sm text-ink leading-tight">{t.symbol}</div>
        <div className="text-xs text-dim truncate max-w-[160px]">
          <span className="capitalize">{t.chain}</span> · {ageLabel(t.age_hours)}
        </div>
      </div>
    </Link>
  );
}

export function TokenRow({ t }: { t: any }) {
  return (
    <tr>
      <td><TokenCell t={t} /></td>
      <td>
        <div className="num">{fmtPrice(t.price)}</div>
        <div className={`num text-xs ${pctColor(t.price_change_h1)}`}>{fmtPct(t.price_change_h1)}</div>
      </td>
      <td className="num">{fmtUsd(t.volume_1h)}</td>
      <td className="num">{fmtUsd(t.liquidity)}</td>
      <td className={`num ${pctColor(t.holder_growth)}`}>{t.holders == null ? "…" : fmtPct(t.holder_growth)}</td>
      <td><RiskPill value={t.risk} /></td>
      <td><TrendPill value={t.trend} /></td>
      <td><Signals t={t} /></td>
    </tr>
  );
}

function TokenCard({ t }: { t: any }) {
  return (
    <Link href={`/token/${t.address}`} className="flex items-center gap-3 px-4 py-3 border-b border-line/60 hover:bg-panel2">
      <TokenAvatar url={t.image_url} symbol={t.symbol} size={34} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-sm">{t.symbol}</span>
          {topBadges(t).slice(0, 1).map((x) => <Badge key={x.label} kind={x.kind}>{x.label}</Badge>)}
        </div>
        <div className="text-xs text-dim num">Vol {fmtUsd(t.volume_1h)} · Liq {fmtUsd(t.liquidity)}</div>
      </div>
      <div className="text-right">
        <div className="num text-sm">{fmtPrice(t.price)}</div>
        <div className={`num text-xs ${pctColor(t.price_change_h1)}`}>{fmtPct(t.price_change_h1)}</div>
      </div>
      <div className="w-10 text-right"><RiskPill value={t.risk} /></div>
    </Link>
  );
}

const SORTS: [string, string][] = [
  ["trend", "Trend score (sim)"], ["volume", "Volume 1h"], ["price_change", "Price 1h"], ["liquidity", "Liquidity"],
  ["holder_growth", "Holder growth"], ["risk", "Risk"], ["age", "Newest"], ["fomo", "FOMO (sim)"], ["social_growth", "Social growth (sim)"],
];

/* ------------------------------------------------ trending table */
function TrendingTable() {
  const [chain, setChain] = useState("");
  const [sort, setSort] = useState("volume");
  const [minLiq, setMinLiq] = useState("0");
  const [maxRisk, setMaxRisk] = useState("100");
  const qs = `/tokens/trending?sort=${sort}&limit=40${chain ? `&chain=${chain}` : ""}&min_liquidity=${minLiq}&max_risk=${maxRisk}`;
  const { data, loading } = useApi(qs, 25000);
  const items = data?.items || [];
  return (
    <Panel
      title={<span className="flex items-center gap-2"><Flame size={16} className="text-amberx" /> Trending tokens</span>}
      right={<span className="text-xs text-dim">{data?.count ?? 0} tracked</span>}
    >
      <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-line/60">
        <select className="sel" value={sort} onChange={(e) => setSort(e.target.value)}>
          {SORTS.map(([v, l]) => <option key={v} value={v}>Sort: {l}</option>)}
        </select>
        <select className="sel" value={chain} onChange={(e) => setChain(e.target.value)}>
          <option value="">All chains</option>
          {["solana", "ethereum", "base", "bnb"].map((c) => <option key={c} value={c}>{c[0].toUpperCase() + c.slice(1)}</option>)}
        </select>
        <select className="sel" value={minLiq} onChange={(e) => setMinLiq(e.target.value)}>
          {[["0", "Any liquidity"], ["5000", "Liq ≥ $5K"], ["25000", "Liq ≥ $25K"], ["100000", "Liq ≥ $100K"]].map(([v, l]) =>
            <option key={v} value={v}>{l}</option>)}
        </select>
        <select className="sel" value={maxRisk} onChange={(e) => setMaxRisk(e.target.value)}>
          {[["100", "Any risk"], ["69", "Below high risk"], ["49", "Below elevated"], ["29", "Low risk only"]].map(([v, l]) =>
            <option key={v} value={v}>{l}</option>)}
        </select>
      </div>
      {loading && !items.length ? <SkeletonRows rows={8} /> : !items.length ? <Empty text="No tokens match these filters." /> : (
        <>
          <div className="hidden md:block overflow-auto max-h-[75vh]">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Token</th><th>Price / 1h</th><th>Vol 1h</th><th>Liquidity</th><th>Holders 24h</th><th>Risk</th>
                  <th>Trend<SimTag /></th><th>Signals</th>
                </tr>
              </thead>
              <tbody>{items.map((t: any) => <TokenRow key={t.address} t={t} />)}</tbody>
            </table>
          </div>
          <div className="md:hidden">{items.map((t: any) => <TokenCard key={t.address} t={t} />)}</div>
        </>
      )}
    </Panel>
  );
}

/* ------------------------------------------------ side tabs: emerging / narratives / momentum */
function EmergingBody() {
  const { data, loading } = useApi("/tokens/emerging", 25000);
  const items = (data?.items || []).slice(0, 8);
  if (loading && !data) return <SkeletonRows rows={4} />;
  if (!items.length) return (
    <div className="p-4 text-sm text-dim leading-relaxed">
      No early signals right now. A token shows up here when several independent signals accelerate at once.
    </div>
  );
  return <>{items.map((t: any) => (
    <Link key={t.address} href={`/token/${t.address}`} className="flex items-center gap-3 px-4 py-3 border-b border-line/50 hover:bg-panel2">
      <TokenAvatar url={t.image_url} symbol={t.symbol} size={28} />
      <div className="flex-1 min-w-0">
        <div className="font-semibold text-sm">{t.symbol}</div>
        <div className="text-xs text-dim truncate">{(t.narratives || []).slice(0, 2).join(" · ") || "—"}</div>
      </div>
      <div className="text-right text-xs num">
        <div className="text-dim">Vol <span className="text-ink">{fmtUsd(t.volume_1h)}</span></div>
      </div>
    </Link>
  ))}</>;
}

export function NarrativeList({ limit = 6 }: { limit?: number }) {
  const { data, loading } = useApi("/narratives/trending", 30000);
  const items = (data?.items || []).slice(0, limit);
  if (loading && !data) return <SkeletonRows rows={4} />;
  return (
    <>
      {items.length === 0 && <Empty text="No active narratives." />}
      {items.map((n: any) => (
        <Link key={n.id} href={`/narratives/${n.id}`} className="block px-4 py-3 border-b border-line/50 hover:bg-panel2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-sm text-ink">{n.name}</span>
            <span className="num text-sm font-semibold text-violetx">{Math.round(n.score)}</span>
          </div>
          <div className="scorebar mt-2"><div style={{ width: `${Math.min(100, n.score)}%`, background: C.violet }} /></div>
          <div className="flex items-center gap-3 mt-2 text-xs text-dim">
            <span className={pctColor(n.mentions_growth)}>{fmtPct(n.mentions_growth)}</span>
            <span>{n.token_count} tokens</span>
            {n.strong_candidates > 0 && <span className="text-cyanx">{n.strong_candidates} strong</span>}
          </div>
        </Link>
      ))}
      <Link href="/narratives" className="block px-4 py-2.5 text-xs text-cyanx hover:underline">View all narratives →</Link>
    </>
  );
}

function MomentumBody() {
  const { data } = useApi("/market/overview", 30000);
  if (!data) return <SkeletonRows rows={2} />;
  return (
    <div className="p-4 space-y-4">
      <div>
        <div className="flex justify-between text-xs text-dim mb-1.5"><span>Sector momentum</span><span className="num text-ink">{Math.round(data.sector_momentum)}/100</span></div>
        <div className="scorebar"><div style={{ width: `${data.sector_momentum}%`, background: C.cyan }} /></div>
      </div>
      <div>
        <div className="flex justify-between text-xs text-dim mb-1.5"><span>24h breadth</span><span className="num text-ink">{Math.round(data.breadth * 100)}% advancing</span></div>
        <div className="scorebar"><div style={{ width: `${data.breadth * 100}%`, background: data.breadth > 0.5 ? C.green : C.red }} /></div>
      </div>
    </div>
  );
}

function MarketTabs() {
  const [tab, setTab] = useState<"emerging" | "narratives" | "momentum">("emerging");
  const tabs: [typeof tab, string][] = [["emerging", "Emerging"], ["narratives", "Narratives"], ["momentum", "Momentum"]];
  return (
    <div className="panel overflow-hidden">
      <div className="tabs">
        {tabs.map(([k, l]) => (
          <button key={k} className={`tab ${tab === k ? "tab-active" : ""}`} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        {tab === "emerging" ? <EmergingBody /> : tab === "narratives" ? <NarrativeList /> : <MomentumBody />}
      </div>
    </div>
  );
}

/* ------------------------------------------------ risk feed */
export function RiskFeed({ limit = 8 }: { limit?: number }) {
  const { data, loading } = useApi(`/risk/events?limit=${limit}`, 25000);
  const items = data?.items || [];
  const sev: Record<string, string> = { high: "red", warn: "amber", info: "cyan" };
  return (
    <Panel title="Risk alerts" right={<span className="text-xs text-dim">newly detected</span>}>
      {loading && !data ? <SkeletonRows rows={4} /> : items.length === 0 ? <Empty text="No risk conditions detected." /> : (
        <div className="max-h-[420px] overflow-y-auto">
          {items.map((e: any, i: number) => (
            <Link key={i} href={`/token/${e.address}`} className="block px-4 py-3 border-b border-line/50 hover:bg-panel2">
              <div className="flex items-center gap-2">
                <Badge kind={sev[e.severity] || "gray"}>{e.severity}</Badge>
                <span className="text-sm font-semibold">{e.symbol}</span>
                <span className="text-xs text-dim capitalize">{e.category}</span>
                {e.telegram_sent && <span className="text-xs text-dim" title="Sent to Telegram">✈</span>}
                <span className="ml-auto text-xs text-dim">{timeAgo(e.ts)}</span>
              </div>
              <div className="text-sm text-dim mt-1 leading-snug">{e.message}</div>
              {e.ai_note && <div className="text-xs text-ink/80 mt-1 leading-snug">🤖 AI read: {e.ai_note}</div>}
            </Link>
          ))}
        </div>
      )}
    </Panel>
  );
}

export default function Dashboard() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-dim mt-1">Live memecoin momentum, liquidity and risk across chains.</p>
      </div>
      <OverviewStrip />
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 items-start">
        <div className="xl:col-span-2"><TrendingTable /></div>
        <div className="space-y-6">
          <PriorityCoins compact limit={6} />
          <RiskFeed />
          <MarketTabs />
        </div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 items-start">
        <IntelFeed compact={8} />
        <DistributionWatch />
      </div>
    </div>
  );
}

/* v2 §6/§21: tokens most likely distributing right now (sell-pressure leaders,
   excluding dead-liquidity dust so the list stays decision-useful). Filterable by
   momentum band, whale flow, liquidity and a 1h–24h look-back window. */
const DIST_MOMENTUM: [string, string][] = [
  ["", "Any momentum"], ["weakening,low", "Fading (weakening / low)"], ["developing", "Developing"],
  ["strong,extreme", "Still strong (strong / extreme)"],
];
const DIST_WHALE: [string, string][] = [
  ["", "Any whale flow"], ["distributing,heavy_distribution", "Whales distributing"],
  ["neutral", "Whales neutral"], ["accumulating", "Whales accumulating"],
];
const DIST_SORT: [string, string][] = [
  ["sellp", "Sell pressure"], ["price_drop", "Biggest drop in window"], ["momentum_low", "Weakest momentum"],
  ["liquidity", "Liquidity"], ["volume", "Volume 1h"],
];
const DIST_SELLP: [string, string][] = [["55", "Sell pressure ≥ 55"], ["65", "Heavy only (≥ 65)"], ["45", "Any distribution (≥ 45)"]];
const DIST_LIQ: [string, string][] = [["3000", "Liq ≥ $3K"], ["10000", "Liq ≥ $10K"], ["50000", "Liq ≥ $50K"], ["0", "Any liquidity"]];

function DistributionWatch() {
  const [win, setWin] = useState("4h");
  const [mom, setMom] = useState("");
  const [whale, setWhale] = useState("");
  const [sort, setSort] = useState("sellp");
  const [minLiq, setMinLiq] = useState("3000");
  const [minSellp, setMinSellp] = useState("55");
  const qs = `/market/distribution?window=${win}&sort=${sort}&min_liquidity=${minLiq}&min_sellp=${minSellp}&limit=10`
    + (mom ? `&momentum=${mom}` : "") + (whale ? `&whale=${whale}` : "");
  const { data, loading } = useApi(qs, 30000);
  const items: any[] = data?.items || [];
  const filtered = !!(mom || whale || minLiq !== "3000" || minSellp !== "55");
  return (
    <Panel title={<span>Distribution watch <span className="panel-sub">— {data?.total ?? 0} under sell pressure</span></span>}
           right={<WindowPills value={win} onChange={setWin} />}>
      <div className="flex flex-wrap gap-2 px-4 py-2.5 border-b border-line/60">
        <select className="sel !w-auto" aria-label="Momentum" value={mom} onChange={(e) => setMom(e.target.value)}>
          {DIST_MOMENTUM.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select className="sel !w-auto" aria-label="Whale flow" value={whale} onChange={(e) => setWhale(e.target.value)}>
          {DIST_WHALE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select className="sel !w-auto" aria-label="Sell pressure threshold" value={minSellp} onChange={(e) => setMinSellp(e.target.value)}>
          {DIST_SELLP.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select className="sel !w-auto" aria-label="Liquidity" value={minLiq} onChange={(e) => setMinLiq(e.target.value)}>
          {DIST_LIQ.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select className="sel !w-auto" aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value)}>
          {DIST_SORT.map(([v, l]) => <option key={v} value={v}>Sort: {l}</option>)}
        </select>
        {filtered && (
          <button className="text-xs text-dim hover:text-ink hover:underline"
                  onClick={() => { setMom(""); setWhale(""); setMinLiq("3000"); setMinSellp("55"); }}>reset</button>
        )}
      </div>
      {loading && !data ? <SkeletonRows rows={4} /> : items.length === 0 ? (
        <Empty text={filtered ? "No token under sell pressure matches these filters." : "No liquid token is under heavy sell pressure right now."} />
      ) : (
        <div className="max-h-[440px] overflow-y-auto">
          {items.map((t: any) => (
            <Link key={t.address} href={`/token/${t.address}`}
                  className="flex items-center gap-3 px-4 py-2.5 border-b border-line/40 last:border-0 hover:bg-panel2">
              <TokenAvatar url={t.image_url} symbol={t.symbol} size={26} />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold truncate">{t.symbol} <span className="text-dim font-normal text-xs">{t.name}</span></div>
                <div className="text-2xs num flex flex-wrap gap-x-2">
                  <span style={{ color: BAND_COLORS[t.sellp_band] || C.amber }}>sell pressure {Math.round(t.sellp)}</span>
                  <span style={{ color: BAND_COLORS[t.momentum_band] || "#8593A8" }}>{bandLabel(t.momentum_band)}{t.momentum != null ? ` ${Math.round(t.momentum)}` : ""}</span>
                  <span className="text-dim">whale {(t.whale_class || "neutral").replace("_", " ")}</span>
                </div>
              </div>
              <div className="text-right">
                <div className={`num text-sm font-semibold ${pctColor(t.window_change)}`}
                     title={t.window_change == null ? "Not enough price history for this window yet" : `Price change over the last ${win}`}>
                  {t.window_change == null ? "—" : fmtPct(t.window_change)}
                </div>
                <div className="text-2xs text-dim num">{win} · liq ${fmtNum(t.liquidity)}</div>
              </div>
            </Link>
          ))}
        </div>
      )}
      <div className="px-4 py-2 border-t border-line/50 text-2xs text-dim">Investigate, don&apos;t condemn: sell pressure and whale flow are inferred from aggregate trades.</div>
    </Panel>
  );
}

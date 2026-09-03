"use client";
import Link from "next/link";
import React, { useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import { ageLabel, fmtNum, fmtPct, fmtPrice, fmtUsd, pctColor, timeAgo } from "@/lib/format";
import { Badge, ConfChip, Empty, FomoPill, Panel, RiskPill, RowBadges, TokenAvatar, TrendPill } from "./ui";

/* ------------------------------------------------ market overview strip */
function OverviewStrip() {
  const { data } = useApi("/market/overview", 30000);
  const { data: sources } = useApi("/sources", 60000);
  if (!data || !data.tracked) return <div className="panel p-4 text-dim text-xs">Loading market overview…</div>;
  const cards = [
    { l: "SECTOR", v: data.phase, c: data.phase === "RISK-ON" ? "#34D399" : data.phase === "RISK-OFF" ? "#F87171" : "#FBBF24" },
    { l: "MOMENTUM", v: `${Math.round(data.sector_momentum)}`, c: "#22D3EE" },
    { l: "TRACKED", v: data.tracked },
    { l: "AVG TREND", v: data.avg_trend },
    { l: "BREADTH 24H", v: `${Math.round(data.breadth * 100)}%`, c: data.breadth > 0.5 ? "#34D399" : "#F87171" },
    { l: "24H VOLUME", v: fmtUsd(data.total_volume_24h) },
    { l: "EMERGING", v: data.emerging, c: "#A78BFA" },
    { l: "EXTREME FOMO", v: data.extreme_fomo, c: data.extreme_fomo ? "#F87171" : undefined },
    { l: "HIGH RISK", v: data.high_risk, c: data.high_risk ? "#F87171" : undefined },
    { l: "ANOMALIES", v: data.anomalies, c: "#FBBF24" },
  ];
  return (
    <div className="panel">
      <div className="grid grid-cols-2 sm:grid-cols-5 lg:grid-cols-10 divide-x divide-line/60">
        {cards.map((c) => (
          <div key={c.l} className="px-3 py-2.5">
            <div className="text-[9px] font-bold tracking-widest text-dim">{c.l}</div>
            <div className="num text-lg font-bold mt-0.5" style={{ color: c.c || "#DCE4EE" }}>{c.v}</div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 border-t border-line/60 text-[10px] text-dim">
        <span className="uppercase tracking-wider font-bold">sources</span>
        {(sources?.sources || []).map((s: any) => (
          <span key={s.name} className="flex items-center gap-1" title={s.meta?.note || ""}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: s.status === "ok" ? "#34D399" : s.status === "mock" ? "#FBBF24" : "#F87171" }} />
            {s.name}{s.status === "mock" && <span className="badge badge-amber !text-[8px]">mock</span>}
          </span>
        ))}
        <span className="ml-auto">{data.updated_at ? `updated ${timeAgo(data.updated_at)}` : ""}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ token row */
export function TokenRow({ t, showGrowth = false }: { t: any; showGrowth?: boolean }) {
  return (
    <tr>
      <td className="!text-left">
        <Link href={`/token/${t.address}`} className="flex items-center gap-2 min-w-0">
          <TokenAvatar url={t.image_url} symbol={t.symbol} />
          <div className="min-w-0">
            <div className="font-bold text-[13px] text-ink leading-tight">${t.symbol}</div>
            <div className="text-[10px] text-dim truncate max-w-[140px]">{t.name}</div>
          </div>
        </Link>
      </td>
      <td><TrendPill value={t.trend} /></td>
      <td><FomoPill value={t.fomo} cls={t.fomo_class} /></td>
      <td><RiskPill value={t.risk} cls={t.risk_class} /></td>
      <td>
        <div className="num text-[12px]">{fmtPrice(t.price)}</div>
        <div className={`num text-[10px] ${pctColor(t.price_change_h1)}`}>{fmtPct(t.price_change_h1)} 1h</div>
      </td>
      <td className="num">{fmtUsd(t.volume_1h)}</td>
      <td className="num">{fmtUsd(t.liquidity)}</td>
      {showGrowth ? (
        <>
          <td className={`num ${pctColor(t.social_growth)}`}>{fmtPct(t.social_growth)}</td>
          <td className={`num ${pctColor(t.holder_growth)}`}>{fmtPct(t.holder_growth)}</td>
        </>
      ) : (
        <>
          <td className="text-[10px] text-dim uppercase">{t.chain}</td>
          <td className="text-[10px] text-dim">{ageLabel(t.age_hours)}</td>
        </>
      )}
      <td><RowBadges item={t} /></td>
    </tr>
  );
}

const HEADERS_GROWTH = ["TOKEN", "TREND", "FOMO", "RISK", "PRICE", "VOL 1H", "LIQ", "SOC G", "HOLD G", "SIGNALS"];
const HEADERS_BASIC = ["TOKEN", "TREND", "FOMO", "RISK", "PRICE", "VOL 1H", "LIQ", "CHAIN", "AGE", "SIGNALS"];

/* -------------------------------------------------------- trending table */
function TrendingTable() {
  const [chain, setChain] = useState("");
  const [sort, setSort] = useState("trend");
  const [minLiq, setMinLiq] = useState("0");
  const [maxRisk, setMaxRisk] = useState("100");
  const qs = `/tokens/trending?sort=${sort}&limit=40${chain ? `&chain=${chain}` : ""}&min_liquidity=${minLiq}&max_risk=${maxRisk}`;
  const { data, loading } = useApi(qs, 25000);
  const items = data?.items || [];
  return (
    <Panel
      title={<span>🔥 Trending now <span className="text-dim normal-case font-normal">(highest current Trend Scores)</span></span>}
      right={<span className="text-[10px] text-dim">{data?.count ?? 0} tracked</span>}
    >
      <div className="flex flex-wrap gap-2 px-3 py-2 border-b border-line/60">
        <select className="sel !py-1 !text-[11px]" value={chain} onChange={(e) => setChain(e.target.value)}>
          <option value="">all chains</option>
          {["solana", "ethereum", "base", "bnb"].map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className="sel !py-1 !text-[11px]" value={sort} onChange={(e) => setSort(e.target.value)}>
          {[["trend", "Trend Score"], ["fomo", "FOMO"], ["volume", "Volume 1h"], ["social_growth", "Social growth"],
            ["holder_growth", "Holder growth"], ["liquidity", "Liquidity"], ["risk", "Risk ↑"], ["price_change", "Price 1h"],
            ["age", "Newest"]].map(([v, l]) => <option key={v} value={v}>sort: {l}</option>)}
        </select>
        <select className="sel !py-1 !text-[11px]" value={minLiq} onChange={(e) => setMinLiq(e.target.value)}>
          {[["0", "any liq"], ["5000", "liq ≥$5K"], ["25000", "liq ≥$25K"], ["100000", "liq ≥$100K"]].map(([v, l]) =>
            <option key={v} value={v}>{l}</option>)}
        </select>
        <select className="sel !py-1 !text-[11px]" value={maxRisk} onChange={(e) => setMaxRisk(e.target.value)}>
          {[["100", "any risk"], ["69", "risk < high"], ["49", "risk < elevated"], ["29", "low risk only"]].map(([v, l]) =>
            <option key={v} value={v}>{l}</option>)}
        </select>
      </div>
      <div className="overflow-x-auto">
        {loading && !items.length ? <Empty text="Loading…" /> : !items.length ? <Empty text="No tokens match the filters." /> : (
          <table className="tbl">
            <thead><tr>{HEADERS_GROWTH.map((h) => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>{items.map((t: any) => <TokenRow key={t.address} t={t} showGrowth />)}</tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------ emerging */
function EmergingList() {
  const { data } = useApi("/tokens/emerging", 25000);
  const items = (data?.items || []).slice(0, 8);
  return (
    <Panel title={<span>👀 Emerging <span className="text-dim normal-case font-normal">(accelerating before mainstream)</span></span>}>
      {items.length === 0 ? (
        <div className="p-4 text-[11px] text-dim leading-relaxed">
          No early signals right now. Emerging requires unusual acceleration across
          several independent signals at once — trend, FOMO, social, volume and holders (§10).
        </div>
      ) : items.map((t: any) => (
        <Link key={t.address} href={`/token/${t.address}`}
              className="flex items-center gap-2.5 px-3.5 py-2.5 border-b border-line/50 hover:bg-panel2">
          <TokenAvatar url={t.image_url} symbol={t.symbol} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="font-bold text-[12px]">${t.symbol}</span>
              <Badge kind="violet">early</Badge>
            </div>
            <div className="text-[10px] text-dim truncate">{(t.narratives || []).slice(0, 2).join(" · ")}</div>
          </div>
          <div className="text-right text-[11px] num space-y-0.5">
            <div><span className="text-dim">soc</span> <span className="text-greenx">{fmtPct(t.social_growth)}</span></div>
            <div><span className="text-dim">vol</span> <span className="text-cyanx">{fmtUsd(t.volume_1h)}</span></div>
          </div>
          <TrendPill value={t.trend} />
        </Link>
      ))}
    </Panel>
  );
}

/* ------------------------------------------------------------ narratives */
export function NarrativeList({ limit = 6 }: { limit?: number }) {
  const { data } = useApi("/narratives/trending", 30000);
  const items = (data?.items || []).slice(0, limit);
  return (
    <Panel title={<span>🧠 Narratives <Link href="/narratives" className="text-cyanx normal-case font-normal">view all →</Link></span>}>
      {items.length === 0 && <Empty text="No active narratives." />}
      {items.map((n: any) => (
        <Link key={n.id} href={`/narratives/${n.id}`}
              className="block px-3.5 py-2.5 border-b border-line/50 hover:bg-panel2">
          <div className="flex items-center justify-between">
            <span className="font-bold text-[12px] text-ink">{n.name}</span>
            <span className="num text-[12px] font-bold text-violetx">{Math.round(n.score)}</span>
          </div>
          <div className="scorebar mt-1.5"><div style={{ width: `${Math.min(100, n.score)}%`, background: "#A78BFA" }} /></div>
          <div className="flex items-center gap-2 mt-1.5 text-[10px] text-dim flex-wrap">
            <span className={pctColor(n.mentions_growth)}>{fmtPct(n.mentions_growth)}</span>
            <span>{n.token_count} tokens</span>
            {n.strong_candidates > 0 && <span className="text-cyanx">{n.strong_candidates} strong</span>}
            <Badge kind={n.cross_platform === "HIGH" ? "green" : n.cross_platform === "MEDIUM" ? "amber" : "gray"}>
              {n.cross_platform} x-platform
            </Badge>
            {n.kind === "auto" && <Badge kind="violet">auto-discovered</Badge>}
          </div>
        </Link>
      ))}
    </Panel>
  );
}

/* ------------------------------------------------------------ risk feed */
export function RiskFeed({ limit = 12 }: { limit?: number }) {
  const { data } = useApi(`/risk/events?limit=${limit}`, 25000);
  const items = data?.items || [];
  const sev: Record<string, string> = { high: "badge-red", warn: "badge-amber", info: "badge-cyan" };
  return (
    <Panel title={<span>🚨 Risk alerts <span className="text-dim normal-case font-normal">(newly detected conditions)</span></span>}>
      {items.length === 0 && <Empty text="No risk conditions detected." />}
      {items.map((e: any, i: number) => (
        <Link key={i} href={`/token/${e.address}`}
              className="block px-3.5 py-2.5 border-b border-line/50 hover:bg-panel2">
          <div className="flex items-center gap-2">
            <span className={`badge ${sev[e.severity] || "badge-gray"}`}>{e.severity}</span>
            <span className="text-[12px] font-bold">${e.symbol}</span>
            <span className="text-[10px] text-dim uppercase tracking-wider">{e.category}</span>
            <span className="ml-auto text-[10px] text-dim">{timeAgo(e.ts)}</span>
          </div>
          <div className="text-[11px] text-dim mt-1 leading-snug">{e.message}</div>
        </Link>
      ))}
    </Panel>
  );
}

/* ------------------------------------------------------------- momentum */
function SectorMomentum() {
  const { data } = useApi("/market/overview", 30000);
  if (!data?.top_narratives) return null;
  return (
    <Panel title="📈 Market momentum">
      <div className="p-3.5 space-y-3">
        <div>
          <div className="flex justify-between text-[10px] text-dim font-bold tracking-wider mb-1">
            <span>SECTOR MOMENTUM</span><span>{Math.round(data.sector_momentum)}/100</span>
          </div>
          <div className="scorebar"><div style={{ width: `${data.sector_momentum}%`, background: "#22D3EE" }} /></div>
        </div>
        <div>
          <div className="flex justify-between text-[10px] text-dim font-bold tracking-wider mb-1">
            <span>24H BREADTH</span><span>{Math.round(data.breadth * 100)}% advancing</span>
          </div>
          <div className="scorebar"><div style={{ width: `${data.breadth * 100}%`, background: data.breadth > 0.5 ? "#34D399" : "#F87171" }} /></div>
        </div>
        <div className="text-[10px] text-dim leading-relaxed border-t border-line/60 pt-2">
          Context feeds the Market Context component (10 pts) of every Trend Score.
        </div>
      </div>
    </Panel>
  );
}

export default function Dashboard() {
  return (
    <div className="space-y-4">
      <OverviewStrip />
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start">
        <div className="xl:col-span-2"><TrendingTable /></div>
        <div className="space-y-4">
          <EmergingList />
          <NarrativeList />
          <SectorMomentum />
          <RiskFeed />
        </div>
      </div>
    </div>
  );
}

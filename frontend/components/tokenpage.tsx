"use client";
import { NewsRow } from "@/components/news";
import Link from "next/link";
import React, { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, apiDelete, apiPost, useApi } from "@/lib/api";
import { ageLabel, fomoBandClass, fmtNum, fmtPct, fmtPrice, fmtUsd, pctColor,
         riskTextClass, timeAgo } from "@/lib/format";
import { Badge, ConfChip, Empty, Gauge, Panel, RiskPill, RowBadges, Sparkline, TokenAvatar } from "./ui";
import ForensicsPanel from "./forensics";
import TimeMachinePanel from "./timemachine";
import { MarketIntelPanel } from "./marketintel";
import { IntelFeed } from "./intelfeed";
import { HoldersChart, LiquidityChart, PriceChart, ScoreChart, SocialChart, VolumeChart } from "./charts";

/* ------------------------------------------------------------- helpers */
function FeedWithFocus({ address }: { address: string }) {
  const sp = useSearchParams();
  const focusId = sp.get("event") ? Number(sp.get("event")) : undefined;
  return <IntelFeed address={address} compact={40} focusId={focusId} />;
}

function Stat({ label, value, sub, color }: { label: string; value: React.ReactNode; sub?: React.ReactNode; color?: string }) {
  return (
    <div className="px-3 py-2">
      <div className="text-2xs font-bold tracking-widest text-dim uppercase">{label}</div>
      <div className="num text-lg font-bold mt-0.5" style={{ color }}>{value}</div>
      {sub && <div className="text-xs text-dim mt-0.5">{sub}</div>}
    </div>
  );
}

function DriverBar({ d, color }: { d: any; color: string }) {
  const pct = Math.min(100, (d.points / d.max) * 100);
  return (
    <div className="py-1">
      <div className="flex justify-between text-xs gap-2">
        <span className="text-ink/80 truncate" title={d.value}>{d.label}</span>
        <span className="num text-dim shrink-0">{d.value}</span>
        <span className="num font-bold shrink-0" style={{ color }}>+{d.points}/{d.max}</span>
      </div>
      <div className="scorebar mt-0.5"><div style={{ width: `${pct}%`, background: color }} /></div>
    </div>
  );
}

function TrendBreakdown({ components }: { components: Record<string, any> }) {
  const order: [string, string][] = [
    ["social", "#22D3EE"], ["market", "#34D399"], ["onchain", "#A78BFA"],
    ["liquidity", "#38BDF8"], ["narrative", "#F472B6"], ["market_context", "#94A3B8"],
  ];
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-4 p-4">
      {order.map(([key, color]) => {
        const c = components[key];
        if (!c) return null;
        return (
          <div key={key}>
            <div className="flex justify-between items-baseline mb-1">
              <span className="text-sm font-bold tracking-wide uppercase" style={{ color }}>
                {key.replace("_", " ")}
              </span>
              <span className="text-xs text-dim">{c.points}/{c.max} · src: {c.source}</span>
            </div>
            {(c.drivers || []).map((d: any, i: number) => <DriverBar key={i} d={d} color={color} />)}
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------- buttons */
function WatchButton({ address }: { address: string }) {
  const { data, loading } = useApi("/watchlist", 0);
  const [busy, setBusy] = useState(false);
  const [inList, setInList] = useState<boolean | null>(null);
  const member = inList ?? (data?.items || []).some((i: any) => i.address === address);
  async function toggle() {
    setBusy(true);
    try {
      if (member) { await apiDelete(`/watchlist/${address}`); setInList(false); }
      else { await apiPost("/watchlist", { address }); setInList(true); }
    } finally { setBusy(false); }
  }
  return (
    <button className={`btn ${member ? "" : "btn-primary"}`} onClick={toggle} disabled={busy || loading}>
      {member ? "★ Watching" : "☆ Watch"}
    </button>
  );
}

function WhyButton({ address }: { address: string }) {
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try { const d = await api(`/tokens/${address}/why`, {}, false); setText(d.explanation); }
    catch (e: any) { setText(`Error: ${e.message}`); }
    finally { setBusy(false); }
  }
  return (
    <div className="w-full">
      <button className="btn w-full justify-center !py-2 !text-sm !bg-violetx/10 !border-violetx/30 !text-violetx hover:!bg-violetx/20"
              onClick={run} disabled={busy}>
        {busy ? "Analyzing…" : "❓ WHY IS THIS TRENDING?"}
      </button>
      {text && (
        <div className="mt-2 p-3 rounded-lg bg-violetx/5 border border-violetx/20 text-sm leading-relaxed text-ink/90">
          {text}
          <div className="text-xs text-dim mt-2">Generated from stored metrics only — no fabricated data (§18).</div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ sections */
function MetricsStrip({ t }: { t: any }) {
  return (
    <div className="panel grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-9 divide-x divide-line/60">
      <Stat label="Price" value={fmtPrice(t.price)} sub={<span className={pctColor(t.price_change_h1)}>{fmtPct(t.price_change_h1)} 1h</span>} />
      <Stat label="24h change" value={<span className={pctColor(t.price_change_h24)}>{fmtPct(t.price_change_h24)}</span>} sub="vs 24h ago" />
      <Stat label="Market cap" value={fmtUsd(t.mcap)} sub={`FDV ${fmtUsd(t.fdv)}`} />
      <Stat label="Liquidity" value={fmtUsd(t.liquidity)} sub="active pair" />
      <Stat label="Volume 24h" value={fmtUsd(t.volume_24h)} sub={`1h ${fmtUsd(t.volume_1h)}`} />
      <Stat label="Buy share 1h" value={t.buy_share == null ? "—" : fmtPct(t.buy_share * 100, false)} sub={`${fmtNum(t.mentions_1h)} mentions (sim)`} />
      <Stat label="Holders" value={t.holders == null ? "—" : `${fmtNum(t.holders)}${t.holders_capped ? "+" : ""}`} sub={<span className={pctColor(t.holder_growth)}>{t.holders == null ? "not checked yet" : `${fmtPct(t.holder_growth)} ~24h · real`}</span>} />
      <Stat label="Social growth" value={<span className={`opacity-60 ${pctColor(t.social_growth)}`}>{fmtPct(t.social_growth)}</span>} sub="mentions 1h (sim)" />
      <Stat label="Age" value={ageLabel(t.age_hours)} sub={t.chain} />
    </div>
  );
}

function ScoresRow({ d, sc }: { d: any; sc: any }) {
  const [tab, setTab] = useState<"trend" | "fomo" | "risk">("trend");
  return (
    <div className="panel">
      <div className="grid grid-cols-3 divide-x divide-line/60">
        <button onClick={() => setTab("trend")} className={`py-4 ${tab === "trend" ? "bg-cyanx/5" : ""}`}>
          <Gauge value={sc.trend} label="Trend" color="#22D3EE"
                 sub={<span className="text-2xs text-dim">multi-source momentum</span>} />
        </button>
        <button onClick={() => setTab("fomo")} className={`py-4 ${tab === "fomo" ? "bg-amberx/5" : ""}`}>
          <Gauge value={sc.fomo} label="FOMO" color={sc.fomo >= 81 ? "#F87171" : "#FBBF24"}
                 sub={<span className={`badge ${fomoBandClass(sc.fomo_class)}`}>{sc.fomo_class}</span>} />
        </button>
        <button onClick={() => setTab("risk")} className={`py-4 ${tab === "risk" ? "bg-redx/5" : ""}`}>
          <Gauge value={sc.risk} label="Risk" color={sc.risk >= 70 ? "#F87171" : sc.risk >= 50 ? "#FB923C" : "#34D399"}
                 sub={<span className={`text-xs font-bold uppercase ${riskTextClass(sc.risk)}`}>{sc.risk_class} risk</span>} />
        </button>
      </div>
      {sc.fomo_warning && tab === "fomo" && (
        <div className="mx-4 mb-3 p-2.5 rounded-lg bg-redx/10 border border-redx/30 text-sm text-redx/90">
          ⚠️ {sc.fomo_warning}
        </div>
      )}
      <div className="border-t border-line/60">
        {tab === "trend" && <TrendBreakdown components={sc.trend_components || {}} />}
        {tab === "fomo" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 p-4">
            {(sc.fomo_components || []).map((c: any, i: number) => <DriverBar key={i} d={c} color="#FBBF24" />)}
          </div>
        )}
        {tab === "risk" && (
          <div className="p-4 space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
              {(sc.risk_components || []).map((c: any, i: number) => (
                <div key={i}>
                  <DriverBar d={c} color="#F87171" />
                  {c.source && String(c.source).startsWith("mock") && (
                    <div className="text-2xs text-amberx/80 -mt-0.5 mb-1">simulated source — verify on-chain</div>
                  )}
                </div>
              ))}
            </div>
            {(sc.risk_flags || []).length > 0 && (
              <div className="pt-2 border-t border-line/60">
                <div className="text-xs font-bold tracking-wider text-redx uppercase mb-1.5">Risk flags</div>
                <ul className="space-y-1">
                  {sc.risk_flags.map((f: string, i: number) => (
                    <li key={i} className="text-sm text-ink/80 flex gap-1.5"><span className="text-redx">▸</span>{f}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function SignalsPanel({ sc }: { sc: any }) {
  const checks = sc.signals?.checks || {};
  const rows: [string, boolean][] = [["Social", checks.social], ["Market", checks.market], ["On-chain", checks.onchain], ["News", checks.news]];
  return (
    <Panel title={<>Signal confirmation <ConfChip level={sc.signals?.confidence} /></>}>
      <div className="p-4 space-y-2.5">
        {rows.map(([l, ok]) => (
          <div key={l} className="flex items-center justify-between text-sm">
            <span className="text-ink/80">{l}</span>
            <span className={`font-bold ${ok ? "text-greenx" : "text-dim"}`}>{ok ? "✓" : "—"}</span>
          </div>
        ))}
        <div className="pt-2 border-t border-line/60 text-xs text-dim leading-snug">{sc.signals?.note}</div>
        <div className="text-xs text-dim">
          Confidence reflects independent-source agreement and data availability — never certainty (§11/§19).
        </div>
        {(sc.conflicts || []).length > 0 && (
          <div className="space-y-2 pt-2 border-t border-line/60">
            {sc.conflicts.map((c: any, i: number) => (
              <div key={i} className="p-2.5 rounded bg-amberx/5 border border-amberx/25">
                <div className="text-xs font-bold text-amberx">⚠ {c.title}</div>
                <div className="text-sm text-ink/75 mt-0.5 leading-snug">{c.detail}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Panel>
  );
}

function AnalysisPanel({ address }: { address: string }) {
  const { data, loading } = useApi(`/tokens/${address}/analysis`, 0, false);
  if (loading || !data) return <Panel title="AI analysis"><div className="p-4 text-dim text-xs">Composing…</div></Panel>;
  const cols: [string, string[], string][] = [
    ["Observed facts", data.observed || [], "#22D3EE"],
    ["Interpretation", data.interpretation || [], "#A78BFA"],
    ["Uncertainty", data.uncertainty || [], "#FBBF24"],
  ];
  return (
    <Panel title={<span>AI analysis <span className="panel-sub">(facts vs interpretation vs uncertainty)</span></span>}>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-0 divide-x divide-line/60">
        {cols.map(([title, items, color]) => (
          <div key={title} className="p-4">
            <div className="text-xs font-bold tracking-widest uppercase mb-2" style={{ color }}>{title}</div>
            <ul className="space-y-1.5">
              {items.map((s, i) => <li key={i} className="text-sm text-ink/80 leading-snug flex gap-1.5"><span style={{ color }}>▸</span>{s}</li>)}
            </ul>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function TokenNewsPanel({ symbol }: { symbol: string }) {
  const { data } = useApi(`/news?symbol=${encodeURIComponent(symbol)}&limit=8`, 120000);
  const items = data?.items || [];
  if (!data || data.source !== "rss" || items.length === 0) return null;   // only show real coverage
  return (
    <Panel title={<span>In the news <span className="badge badge-green !text-2xs ml-1">real · publisher feeds</span></span>}>
      {items.map((a: any, i: number) => <NewsRow key={i} a={a} />)}
    </Panel>
  );
}

function OnchainRiskPanel({ address }: { address: string }) {
  const { data } = useApi(`/tokens/${address}/onchain-risk`, 60000);
  const sevCls: Record<string, string> = { high: "text-redx", medium: "text-amberx", low: "text-greenx" };
  const f = data?.facts;
  return (
    <Panel title={<span>On-chain risk <span className="badge badge-green !text-2xs ml-1">real · Helius</span></span>}>
      {!data && <Empty text="Loading…" />}
      {data && !data.available && <Empty text={data.error ? `Check failed: ${data.error}` : (data.reason || "Not available.")} />}
      {data?.available && f && (
        <div className="px-3.5 py-2 text-sm space-y-1.5">
          <div className="flex justify-between"><span className="text-dim">Overall</span>
            <span className={`font-bold uppercase ${sevCls[data.label] || ""}`}>{data.label}</span></div>
          <div className="flex justify-between"><span className="text-dim">Mint authority</span>
            <span className={f.mint_renounced ? "text-greenx" : "text-redx"}>{f.mint_renounced ? "renounced ✓" : "ACTIVE ✗"}</span></div>
          <div className="flex justify-between"><span className="text-dim">Freeze authority</span>
            <span className={f.freeze_disabled ? "text-greenx" : "text-redx"}>{f.freeze_disabled ? "none ✓" : "ACTIVE ✗"}</span></div>
          {f.top10_wallet_pct != null ? (<>
            <div className="flex justify-between"><span className="text-dim">Largest wallet</span>
              <span className={`num ${f.top1_wallet_pct >= 20 ? "text-redx" : f.top1_wallet_pct >= 8 ? "text-amberx" : ""}`}>{fmtPct(f.top1_wallet_pct, false)}</span></div>
            <div className="flex justify-between"><span className="text-dim">Top 10 wallets</span>
              <span className={`num ${f.top10_wallet_pct >= 50 ? "text-redx" : f.top10_wallet_pct >= 30 ? "text-amberx" : ""}`}>{fmtPct(f.top10_wallet_pct, false)}</span></div>
            <div className="flex justify-between"><span className="text-dim">In pools / burned</span><span className="num text-dim">{fmtPct(f.excluded_pct, false)}</span></div>
          </>) : (<>
            <div className="flex justify-between"><span className="text-dim">Top account</span><span className="num">{fmtPct(f.top1_pct, false)}</span></div>
            <div className="flex justify-between"><span className="text-dim">Top 10 accounts</span><span className="num">{fmtPct(f.top10_pct, false)}</span></div>
          </>)}
          {data.flags?.map((fl: any) => (
            <div key={fl.id} className={`text-xs ${sevCls[fl.severity] || ""}`}>⚠ {fl.message}</div>
          ))}
          <div className="text-2xs text-dim pt-1">{data.caveat} Checked {timeAgo(Math.floor(new Date(data.checked_at).getTime() / 1000))}.</div>
        </div>
      )}
    </Panel>
  );
}

function WhalesPanel({ address }: { address: string }) {
  const { data } = useApi(`/tokens/${address}/holders`, 30000);
  const whales = data?.whales || [];
  return (
    <Panel title={<span>Whale activity {data?.mock
      ? <span className="badge badge-amber !text-2xs ml-1">simulated</span>
      : <span className="badge badge-green !text-2xs ml-1">real · top-10 wallets</span>}</span>}>
      {whales.length === 0 && <Empty text={data?.mock === false
        ? "Individual whale trades aren't tracked yet. Changes in the top-10 real wallets' share (Helius) feed the whale-flow score."
        : "No large transfers recently."} />}
      {whales.slice(0, 12).map((w: any, i: number) => (
        <div key={i} className="flex items-center gap-2 px-3.5 py-2 border-b border-line/50 text-sm">
          <span className={w.side === "buy" ? "text-greenx" : "text-redx"}>{w.side === "buy" ? "▲" : "▼"}</span>
          <span className="font-mono text-dim truncate flex-1">{w.wallet}</span>
          <span className="num font-bold">{fmtUsd(w.amount_usd)}</span>
          <span className="text-dim text-xs">{timeAgo(w.ts)}</span>
        </div>
      ))}
    </Panel>
  );
}

function PostsPanel({ address }: { address: string }) {
  const { data } = useApi(`/tokens/${address}/social`, 30000);
  const posts = data?.posts || [];
  const tg = data?.source === "telegram";
  return (
    <Panel title={<span>{tg ? "✈️ Telegram calls" : "𝕏 Social feed"} {data?.mock
      ? <span className="badge badge-amber !text-2xs ml-1">simulated</span>
      : tg ? <span className="badge badge-green !text-2xs ml-1">real · watched channels</span>
      : <span className="badge !text-2xs ml-1">no source</span>}</span>}>
      {posts.length === 0 && <Empty text={data?.mock ? "No posts captured." : tg
        ? "None of your watched Telegram channels has posted this coin's contract address in the last 3 days."
        : "No real social source is connected. Add public Telegram call channels in TELEGRAM_CALL_CHANNELS (Railway variables) to see real calls here."} />}
      {tg && posts.length > 0 && <div className="px-3.5 py-1.5 text-2xs text-amberx border-b border-line/50">{data.note}</div>}
      {posts.slice(0, 10).map((p: any, i: number) => (
        <div key={i} className="px-3.5 py-2 border-b border-line/50">
          <div className="flex items-center gap-2 text-xs text-dim">
            <span className="text-cyanx">{p.author}</span>
            {p.platform === "telegram"
              ? <span>{fmtNum(p.views)} views</span>
              : <span>quality {Math.round(p.quality * 100)}%</span>}
            <span className="ml-auto">{timeAgo(p.ts)}</span>
          </div>
          <div className="text-sm text-ink/85 mt-0.5 whitespace-pre-line break-words">{p.text}</div>
          {p.url
            ? <a href={p.url} target="_blank" rel="noopener noreferrer nofollow" className="text-xs text-cyanx hover:underline">open on Telegram →</a>
            : <div className="text-xs text-dim mt-0.5">♥ {fmtNum(p.likes)} · ⇄ {fmtNum(p.reposts)}</div>}
        </div>
      ))}
    </Panel>
  );
}
function RiskEventsPanel({ address }: { address: string }) {
  const { data } = useApi(`/tokens/${address}/risk`, 30000);
  const events = data?.events || [];
  const sev: Record<string, string> = { high: "badge-red", warn: "badge-amber", info: "badge-cyan" };
  return (
    <Panel title="Risk event log">
      {events.length === 0 && <Empty text="No risk events recorded for this token." />}
      {events.map((e: any, i: number) => (
        <div key={i} className="px-3.5 py-2 border-b border-line/50">
          <div className="flex items-center gap-2">
            <span className={`badge ${sev[e.severity] || "badge-gray"}`}>{e.severity}</span>
            <span className="text-xs uppercase tracking-wider text-dim">{e.category}</span>
            <span className="ml-auto text-xs text-dim">{timeAgo(e.ts)}</span>
          </div>
          <div className="text-sm text-ink/80 mt-0.5">{e.message}</div>
        </div>
      ))}
    </Panel>
  );
}

/* --------------------------------------------------------------- page */
export default function TokenPage({ address }: { address: string }) {
  const { data: d, error } = useApi(`/tokens/${address}`, 25000);
  const { data: sc } = useApi(`/tokens/${address}/scores`, 25000);
  if (error) return <Empty text={`Token not found: ${error}`} />;
  if (!d) return <Empty text="Loading token…" />;
  return (
    <div className="space-y-4">
      {/* header */}
      <div className="panel p-4">
        <div className="flex flex-wrap items-start gap-4">
          <TokenAvatar url={d.image_url} symbol={d.symbol} size={44} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-xl font-extrabold tracking-tight">${d.symbol}</h1>
              <span className="text-dim text-base">{d.name}</span>
              <Badge kind="gray">{d.chain}</Badge>
              {d.dex && <Badge kind="cyan">{d.dex}</Badge>}
              <RowBadges item={d} />
              <ConfChip level={d.confidence} />
            </div>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              {(d.narratives || []).map((n: string) => (
                <Link key={n} href="/narratives" className="badge badge-violet">🧠 {n}</Link>
              ))}
            </div>
            <div className="flex items-center gap-3 mt-2 text-xs text-dim font-mono flex-wrap">
              <span className="truncate max-w-[340px]">{d.address}</span>
              <button className="btn !py-0.5 !px-1.5 !text-2xs" onClick={() => navigator.clipboard?.writeText(d.address)}>copy</button>
              {d.url && <a className="text-cyanx hover:underline" href={d.url} target="_blank" rel="noreferrer">DexScreener ↗</a>}
              <span className="text-amberx/70">non-market data simulated</span>
            </div>
          </div>
          <div className="flex flex-col gap-2 w-[220px] shrink-0">
            <WatchButton address={d.address} />
            <WhyButton address={d.address} />
          </div>
        </div>
      </div>

      <MetricsStrip t={d} />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start">
        <div className="xl:col-span-2 space-y-4">
          {sc && <ScoresRow d={d} sc={sc} />}
          <AnalysisPanel address={d.address} />
          <TimeMachinePanel address={d.address} />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <PriceChart addr={d.address} />
            <VolumeChart addr={d.address} />
            <SocialChart addr={d.address} />
            <HoldersChart addr={d.address} />
            <LiquidityChart addr={d.address} />
            <ScoreChart addr={d.address} />
          </div>
        </div>
        <div className="space-y-4">
          {sc?.market_intel && <MarketIntelPanel mi={sc.market_intel} />}
          <Suspense fallback={null}><FeedWithFocus address={d.address} /></Suspense>
          {sc && <SignalsPanel sc={sc} />}
          {sc?.forensics && <ForensicsPanel forensics={sc.forensics} />}
          {d.symbol && <TokenNewsPanel symbol={d.symbol} />}
          {d.contract_facts && (() => {
            const cf = d.contract_facts;
            const real = cf.source === "helius";
            const badge = real ? "badge-green" : "badge-amber";
            const badgeText = real ? "real · helius" : cf.source === "unverified" ? "not verified yet" : "simulated";
            const flag = (v: boolean | null | undefined, on: string, off: string): [string, boolean] =>
              v == null ? ["unknown", true] : v ? [on, true] : [off, false];
            const rows: [string, string, boolean][] = [
              ["Mint authority", ...flag(cf.mint_authority, "ENABLED ⚠", "renounced ✓")],
              ["Freeze authority", ...flag(cf.freeze_authority, "ENABLED ⚠", "none ✓")],
              ["Token program", ...(cf.token_2022 == null ? ["unknown", true] as [string, boolean]
                : cf.token_2022 ? ["Token-2022 ⚠", true] as [string, boolean] : ["SPL Token ✓", false] as [string, boolean])],
              ["LP burned", cf.lp_burned_pct == null ? "unavailable" : `${cf.lp_burned_pct}%`,
                cf.lp_burned_pct != null && cf.lp_burned_pct < 30],
              ["Deployer prior launches", cf.deployer_prev_launches == null ? "unavailable" : `${cf.deployer_prev_launches}`,
                cf.deployer_prev_launches != null && cf.deployer_prev_launches >= 3],
            ];
            return (
              <Panel title={<span>Contract &amp; deployer <span className={`badge ${badge} !text-2xs ml-1`}>{badgeText}</span></span>}>
                <div className="p-4 space-y-1.5 text-sm">
                  {rows.map(([l, v, bad]) => (
                    <div key={l} className="flex justify-between">
                      <span className="text-dim">{l}</span>
                      <span className={v === "unknown" || v === "unavailable" ? "text-dim" : bad ? "text-redx font-semibold" : "text-greenx"}>{v}</span>
                    </div>
                  ))}
                  <div className="text-2xs text-dim pt-2 border-t border-line/60">
                    {real
                      ? <>Source: Helius on-chain check{cf.checked_at ? ` · ${new Date(cf.checked_at).toLocaleString()}` : ""}. LP lock and deployer history aren&apos;t checked yet.</>
                      : cf.source === "unverified"
                        ? <>No on-chain check yet — the risk score counts this as a partial risk until Helius verifies it.</>
                        : <>Source: {cf.source} (demo data).</>}
                  </div>
                </div>
              </Panel>
            );
          })()}
          <OnchainRiskPanel address={d.address} />
          <WhalesPanel address={d.address} />
          <PostsPanel address={d.address} />
          <RiskEventsPanel address={d.address} />
        </div>
      </div>
    </div>
  );
}

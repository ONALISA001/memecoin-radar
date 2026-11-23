"use client";
/* Priority coins: tracked coins that pass every quality gate right now (risk,
   liquidity, no distribution, no Avoid/Rug verdict, momentum or constructive
   events). A research shortlist with the reasons visible, not a buy signal. */
import Link from "next/link";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { fmtPct, fmtUsd, pctColor } from "@/lib/format";
import { Panel, SkeletonRows, TokenAvatar } from "./ui";
import { RatingBadge } from "./verdict";
import { BAND_COLORS, bandLabel } from "./marketintel";

export const WINDOWS = ["1h", "2h", "4h", "6h", "12h", "24h"] as const;
export type Window = (typeof WINDOWS)[number];

/** Pill group for picking a look-back window (1h … 24h). */
export function WindowPills({ value, onChange, options = WINDOWS as readonly string[], label }: {
  value: string; onChange: (w: string) => void; options?: readonly string[]; label?: string;
}) {
  return (
    <div className="flex items-center gap-0.5 flex-wrap" role="group" aria-label={label || "Time window"}>
      {label && <span className="text-2xs text-dim mr-1">{label}</span>}
      {options.map((w) => (
        <button key={w} onClick={() => onChange(w)} aria-pressed={w === value}
                className={`pill-mini ${w === value ? "!text-cyanx !border-cyanx/60" : "text-dim border-line"}`}>{w}</button>
      ))}
    </div>
  );
}

function ScoreRing({ v }: { v: number }) {
  const color = v >= 70 ? "#34D399" : v >= 55 ? "#22D3EE" : "#FBBF24";
  return (
    <div className="num text-sm font-bold w-11 h-11 rounded-full grid place-items-center shrink-0"
         style={{ border: `2px solid ${color}`, color }} title="Priority score (0–100)">{Math.round(v)}</div>
  );
}

function CoinRow({ c, open, onToggle, early }: { c: any; open: boolean; onToggle: () => void; early?: boolean }) {
  const slope = c.momentum_slope;
  return (
    <div className="border-b border-line/50 last:border-0">
      <div className="flex items-center gap-3 px-4 py-2.5">
        <ScoreRing v={c.score} />
        <Link href={`/token/${c.address}`} className="flex items-center gap-2.5 min-w-0 flex-1 hover:text-cyanx">
          <TokenAvatar url={c.image_url} symbol={c.symbol} size={26} />
          <div className="min-w-0">
            <div className="font-semibold text-sm truncate">${c.symbol}
              {c.age_hours != null && c.age_hours < 24 && (
                <span className="text-2xs text-dim font-normal ml-1.5">
                  {c.age_hours < 1 ? `${Math.round(c.age_hours * 60)}m` : `${c.age_hours.toFixed(1)}h`} old
                </span>
              )}
            </div>
            <div className="text-2xs text-dim num truncate">
              liq {fmtUsd(c.liquidity, 0)} · vol 1h {fmtUsd(c.volume_1h, 0)}
            </div>
          </div>
        </Link>
        <div className="hidden sm:flex flex-col items-end gap-0.5">
          <span className="text-2xs" style={{ color: BAND_COLORS[c.momentum_band] || "#8593A8" }}>
            {bandLabel(c.momentum_band)}
            {slope != null && (
              <span className={slope >= 0 ? "text-greenx" : "text-redx"}> {slope >= 0 ? "▲" : "▼"}{Math.abs(Math.round(slope))}</span>
            )}
          </span>
          {c.verdict ? <RatingBadge rating={c.verdict} /> : <span className="text-2xs text-dim">no verdict yet</span>}
        </div>
        <div className="text-right w-16">
          <div className={`num text-sm ${pctColor(c.price_change_h1)}`}>{fmtPct(c.price_change_h1)}</div>
          <div className="text-2xs text-dim num">{c.events_up}↑ {c.events_down}↓</div>
        </div>
        <button onClick={onToggle} className="text-2xs text-cyanx hover:underline w-10 text-right" aria-expanded={open}>
          {open ? "hide" : "why"}
        </button>
      </div>
      {early && c.early_reasons?.length > 0 && (
        <div className="px-4 pb-2 pl-[4.25rem] flex flex-wrap gap-1">
          {c.early_reasons.map((r: string) => <span key={r} className="badge badge-violet !text-2xs">{r}</span>)}
        </div>
      )}
      {c.flags?.length > 0 && (
        <div className="px-4 pb-2 pl-[4.25rem] flex flex-wrap gap-1">
          {c.flags.map((f: string) => <span key={f} className="badge badge-amber !text-2xs">⚠ {f}</span>)}
        </div>
      )}
      {open && (
        <div className="px-4 pb-3 pl-[4.25rem] space-y-2">
          <div className="grid grid-cols-1 gap-y-0.5">
            {(c.checks || []).map((k: any) => (
              <div key={k.id} className="text-xs flex gap-1.5 items-baseline min-w-0">
                <span className={k.passed ? "text-greenx" : "text-redx"}>{k.passed ? "✓" : "✗"}</span>
                <span className="text-ink/80 truncate">{k.label}</span>
                <span className="ml-auto text-2xs text-dim num truncate">{k.value}</span>
              </div>
            ))}
          </div>
          {c.factors?.length > 0 && (
            <div className="border-t border-line/40 pt-1.5">
              <div className="text-2xs font-bold tracking-wider text-dim mb-0.5">SCORE {Math.round(c.score)} =</div>
              {c.factors.map((f: any) => (
                <div key={f.id} className="text-2xs flex gap-2 num">
                  <span className={`w-10 text-right ${f.points < 0 ? "text-redx" : "text-ink/80"}`}>
                    {f.points > 0 ? "+" : ""}{f.points}
                  </span>
                  <span className="text-dim truncate">
                    {f.label}{f.weight ? ` (${Math.round(f.weight * 100)}% × ${Math.round(f.value)})` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TrackRecord({ t, kind }: { t: any; kind: "passing" | "early" }) {
  const s = t?.[kind];
  if (!s || !s.picks) return <span className="text-dim">Track record builds as picks age (each is judged 4h later).</span>;
  if (!s.judged_4h) return <span className="text-dim">{s.picks} picks in {t.days}d · first results 4h after each pick</span>;
  // "net" = after a ~3.6% round trip (fees, slippage, spread): what a real buyer would keep.
  // Rugged coins count as -100%; coverage says how many due picks have a result at all.
  const upNet = s.net_up_after_4h_pct ?? s.up_after_4h_pct;
  return (
    <span className="num" title={t.note}>
      Last {t.days}d: {s.judged_4h} picks judged ·{" "}
      <span className={(upNet ?? 0) >= 50 ? "text-greenx" : "text-amberx"}>{upNet}% up after 4h, net of costs</span>
      {" "}· median <span className={pctColor(s.net_median_ret_4h ?? s.median_ret_4h)}>{fmtPct(s.net_median_ret_4h ?? s.median_ret_4h)}</span>
      {" "}· avg <span className={pctColor(s.net_avg_ret_4h ?? s.avg_ret_4h)}>{fmtPct(s.net_avg_ret_4h ?? s.avg_ret_4h)}</span>
      {s.rugged > 0 && <> · <span className="text-redx">{s.rugged} rugged (−100%)</span></>}
      {s.coverage_pct != null && s.coverage_pct < 100 && <> · {s.coverage_pct}% of picks have a result</>}
    </span>
  );
}

const EMPTY_PASSING = (win: string) =>
  `No coin passes every gate in the last ${win}. The gates: risk under 50, liquidity of at least $15K, no distribution, ` +
  "no Avoid or Rug-risk verdict, momentum or constructive events, and no severe red flags.";
const EMPTY_EARLY =
  "No early candidates right now. A coin shows here when it is under 24h old, its momentum is climbing fast, " +
  "and buying, volume or holder growth confirm it, before it meets the full Priority bar.";

export function PriorityCoins({ limit = 30, compact = false, initialWindow = "4h" }: {
  limit?: number; compact?: boolean; initialWindow?: string;
}) {
  const [win, setWin] = useState(initialWindow);
  const [tab, setTab] = useState<"passing" | "early">("passing");
  const [open, setOpen] = useState<string | null>(null);
  const { data, loading } = useApi(`/signals/priority?window=${win}&limit=${limit}`, 30000);
  const items: any[] = (tab === "passing" ? data?.items : data?.early) || [];
  const near: any[] = data?.near_misses || [];
  const tabs: [typeof tab, string][] = [["passing", `Passing (${data?.passed ?? 0})`], ["early", `🌱 Early (${data?.early_count ?? 0})`]];
  return (
    <Panel
      title={<span className="whitespace-nowrap">⭐ Priority coins <span className="panel-sub">
        {data?.passed ?? 0}/{data?.checked ?? 0} pass</span></span>}
      right={<WindowPills value={win} onChange={setWin} options={compact ? ["1h", "4h", "24h"] : WINDOWS} />}>
      <div className="flex gap-1 px-4 pt-2" role="tablist">
        {tabs.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={`pill-mini ${tab === k ? "!text-cyanx !border-cyanx/60" : "text-dim border-line"}`}>{l}</button>
        ))}
      </div>
      <div className="px-4 py-1.5 text-2xs border-b border-line/50"><TrackRecord t={data?.track_record} kind={tab} /></div>
      {loading && !data ? <SkeletonRows rows={3} /> : items.length === 0 ? (
        <div className="px-4 py-3 text-sm text-dim leading-relaxed">{tab === "passing" ? EMPTY_PASSING(win) : EMPTY_EARLY}</div>
      ) : (
        <div className={`overflow-y-auto overscroll-contain ${compact ? "max-h-[380px]" : "max-h-[560px]"}`}
             aria-label={tab === "passing" ? "Coins passing every gate" : "Early candidates"}>
          {items.map((c) => (
            <CoinRow key={c.address} c={c} early={tab === "early"} open={open === c.address}
                     onToggle={() => setOpen(open === c.address ? null : c.address)} />
          ))}
        </div>
      )}
      {!compact && tab === "passing" && near.length > 0 && (
        <div className="px-4 py-2 border-t border-line/50 text-xs">
          <span className="text-dim">Near misses: </span>
          {near.map((c, i) => (
            <span key={c.address}>
              <Link href={`/token/${c.address}`} className="text-ink/80 hover:text-cyanx">${c.symbol}</Link>
              <span className="text-dim"> ({c.missed.toLowerCase()})</span>{i < near.length - 1 ? " · " : ""}
            </span>
          ))}
        </div>
      )}
      <div className="px-4 py-2 border-t border-line/50 text-2xs text-dim">
        {tab === "early"
          ? "Early means earlier but riskier: these have not met the full bar yet. Research, not a buy signal."
          : "Ranked by momentum and its trend, buying, volume, holders, risk, events and verdict, minus red flags. Not a buy signal."}
      </div>
    </Panel>
  );
}

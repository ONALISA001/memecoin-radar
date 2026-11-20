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

function CoinRow({ c, open, onToggle }: { c: any; open: boolean; onToggle: () => void }) {
  return (
    <div className="border-b border-line/50 last:border-0">
      <div className="flex items-center gap-3 px-4 py-2.5">
        <ScoreRing v={c.score} />
        <Link href={`/token/${c.address}`} className="flex items-center gap-2.5 min-w-0 flex-1 hover:text-cyanx">
          <TokenAvatar url={c.image_url} symbol={c.symbol} size={26} />
          <div className="min-w-0">
            <div className="font-semibold text-sm truncate">${c.symbol}</div>
            <div className="text-2xs text-dim num truncate">
              liq {fmtUsd(c.liquidity, 0)} · vol 1h {fmtUsd(c.volume_1h, 0)}
            </div>
          </div>
        </Link>
        <div className="hidden sm:flex flex-col items-end gap-0.5">
          <span className="text-2xs" style={{ color: BAND_COLORS[c.momentum_band] || "#8593A8" }}>
            {bandLabel(c.momentum_band)}
          </span>
          {c.verdict ? <RatingBadge rating={c.verdict} /> : <span className="text-2xs text-dim">no verdict yet</span>}
        </div>
        <div className="text-right w-16">
          <div className={`num text-sm ${pctColor(c.price_change_h1)}`}>{fmtPct(c.price_change_h1)}</div>
          <div className="text-2xs text-dim num">{c.events_up}↑ {c.events_down}↓</div>
        </div>
        <button onClick={onToggle} className="text-2xs text-cyanx hover:underline w-12 text-right" aria-expanded={open}>
          {open ? "hide" : "why"}
        </button>
      </div>
      {open && (
        <div className="px-4 pb-3 pl-[4.25rem] grid grid-cols-1 gap-y-0.5">
          {c.checks.map((k: any) => (
            <div key={k.id} className="text-xs flex gap-1.5 items-baseline min-w-0">
              <span className={k.passed ? "text-greenx" : "text-redx"}>{k.passed ? "✓" : "✗"}</span>
              <span className="text-ink/80 truncate">{k.label}</span>
              <span className="ml-auto text-2xs text-dim num truncate">{k.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function PriorityCoins({ limit = 8, compact = false, initialWindow = "4h" }: {
  limit?: number; compact?: boolean; initialWindow?: string;
}) {
  const [win, setWin] = useState(initialWindow);
  const [open, setOpen] = useState<string | null>(null);
  const { data, loading } = useApi(`/signals/priority?window=${win}&limit=${limit}`, 30000);
  const items: any[] = data?.items || [];
  const near: any[] = data?.near_misses || [];
  return (
    <Panel
      title={<span className="whitespace-nowrap">⭐ Priority coins <span className="panel-sub">
        {data?.passed ?? 0}/{data?.checked ?? 0} pass</span></span>}
      right={<WindowPills value={win} onChange={setWin} options={compact ? ["1h", "4h", "24h"] : WINDOWS} />}>
      {loading && !data ? <SkeletonRows rows={3} /> : items.length === 0 ? (
        <div className="px-4 py-3 text-sm text-dim leading-relaxed">
          No coin passes every gate in the last {win}. That is a real answer: the gates are risk under 50,
          liquidity ≥ $15K, no distribution, no Avoid / Rug verdict, and momentum or constructive events.
        </div>
      ) : (
        <div className={compact ? "max-h-[360px] overflow-y-auto" : ""}>
          {items.map((c) => (
            <CoinRow key={c.address} c={c} open={open === c.address}
                     onToggle={() => setOpen(open === c.address ? null : c.address)} />
          ))}
        </div>
      )}
      {!compact && near.length > 0 && (
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
        A research shortlist ranked by momentum, flow, risk, events and verdict. Not a buy signal.
      </div>
    </Panel>
  );
}

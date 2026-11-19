"use client";
import React from "react";
import { Badge, Panel } from "./ui";
import { fmtPct, timeAgo } from "@/lib/format";

function organicColor(v: number) {
  return v >= 70 ? "#34D399" : v >= 55 ? "#A3E635" : v >= 40 ? "#FBBF24" : v >= 25 ? "#FB923C" : "#F87171";
}

function Meter({ label, value, hint, invert = true }: { label: string; value: number | null; hint?: string; invert?: boolean }) {
  if (value === null || value === undefined) return null;
  const pct = Math.round(value * 100);
  const color = invert ? (pct >= 60 ? "#F87171" : pct >= 35 ? "#FBBF24" : "#34D399") : "#22D3EE";
  return (
    <div className="py-1.5">
      <div className="flex justify-between text-xs">
        <span className="text-ink/75">{label}</span>
        <span className="num font-bold" style={{ color }}>{pct}%</span>
      </div>
      <div className="scorebar mt-1"><div style={{ width: `${pct}%`, background: color }} /></div>
      {hint && <div className="text-2xs text-dim mt-0.5 leading-snug">{hint}</div>}
    </div>
  );
}

function Cell({ k, v, dim }: { k: string; v: React.ReactNode; dim?: boolean }) {
  return (
    <div className="flex justify-between text-xs py-0.5">
      <span className="text-dim">{k}</span>
      <span className={`num ${dim ? "text-dim" : "text-ink/85"}`}>{v}</span>
    </div>
  );
}

const REGIME_KIND: Record<string, string> = {
  markup: "green", accumulation: "cyan", distribution: "red",
  chop: "gray", dormant: "gray", unknown: "gray",
};

export default function ForensicsPanel({ forensics }: { forensics: any }) {
  if (!forensics || forensics.organic_score === undefined) {
    return <Panel title="Signal forensics"><div className="p-4 text-dim text-xs">Collecting…</div></Panel>;
  }
  const c = forensics.components || {};
  const ll = c.lead_lag || {};
  const exc = c.excitation || {};
  const reg = c.regime || {};
  const org = forensics.organic_score;
  const cps = forensics.changepoints_24h || [];
  return (
    <Panel title={<span>Signal forensics <span className="panel-sub">organic vs manufactured</span></span>}>
      <div className="p-4 space-y-3">
        {/* organic composite */}
        <div>
          <div className="flex items-baseline justify-between">
            <span className="text-xs font-bold tracking-widest text-dim uppercase">Organic score</span>
            <span className="num text-2xl font-extrabold" style={{ color: organicColor(org) }}>{Math.round(org)}</span>
          </div>
          <div className="scorebar !h-2 mt-1">
            <div style={{ width: `${org}%`, background: organicColor(org) }} />
          </div>
          <div className="flex justify-between items-center mt-1.5">
            <span className="text-sm font-semibold" style={{ color: organicColor(org) }}>{forensics.verdict}</span>
            <span className="text-2xs text-dim">0 = manufactured · 100 = organic</span>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-x-4 border-t border-line/60 pt-2">
          <div>
            <Meter label="Wash-trade suspicion" value={c.wash_trading?.score}
                   hint={c.wash_trading ? `${c.wash_trading.trades_per_wallet_24h} trades/wallet·24h · buy share ${fmtPct((c.wash_trading.buy_share_24h ?? 0.5) * 100, false)}` : ""} />
            <Meter label="Sybil pressure" value={c.sybil_pressure?.score}
                   hint={c.sybil_pressure ? `unique/mention ${(c.sybil_pressure.unique_ratio_1h * 100).toFixed(0)}% · low-quality ${(c.sybil_pressure.low_quality_share_24h * 100).toFixed(0)}%` : ""} />
            <Meter label="Coordination" value={c.coordination?.score}
                   hint={c.coordination ? `${c.coordination.posts_24h} posts/24h · ${c.coordination.cross_token_authors} cross-token authors` : ""} />
          </div>
          <div>
            <div className="text-xs font-bold tracking-widest text-dim uppercase mb-1">Structure</div>
            <Cell k="Regime" v={<Badge kind={REGIME_KIND[reg.regime] || "gray"}>{reg.regime || "?"}</Badge>} />
            <div className="text-2xs text-dim leading-snug mb-1">{reg.detail}</div>
            {reg.ret_6h !== undefined && <Cell k="Price 6h" v={fmtPct(reg.ret_6h)} />}
            {ll.best_lag !== null && ll.best_lag !== undefined ? (
              <Cell k={ll.best_lag > 0 ? "Social leads price by" : ll.best_lag < 0 ? "Price leads social by" : "Lead/lag"}
                    v={`${Math.abs(ll.lead_minutes)}m (r=${ll.strength})`} />
            ) : <Cell k="Lead/lag" v="insufficient data" dim />}
            {exc.index !== null && exc.index !== undefined && (
              <Cell k="Cascade excitation" v={`${(exc.index * 100).toFixed(0)}% · burst ${((exc.burst_share ?? 0) * 100).toFixed(0)}%`} />
            )}
            {c.holder_retention_6h !== undefined && (
              <Cell k="Holder retention 6h" v={fmtPct((c.holder_retention_6h - 1) * 100)} />
            )}
            {cps.length > 0 && (
              <div className="mt-1">
                <div className="text-xs font-bold tracking-widest text-dim uppercase">Change-points 24h</div>
                {cps.map((p: any, i: number) => (
                  <div key={i} className="text-2xs text-dim">
                    ▸ {p.side === "up" ? "▲" : "▼"} regime shift, {p.mag}σ
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="text-2xs text-dim leading-relaxed border-t border-line/60 pt-2">
          {(forensics.notes || []).map((n: string, i: number) => <div key={i}>· {n}</div>)}
        </div>
      </div>
    </Panel>
  );
}

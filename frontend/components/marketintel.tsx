"use client";
import React from "react";
import { useApi } from "@/lib/api";
import { Badge, Panel } from "./ui";

/* v2 market-intelligence building blocks (§4–§13, §15, §18).
   Every panel reflects exact API payload fields and exposes the
   explainability the scores carry (§30). */

export const BAND_COLORS: Record<string, string> = {
  extreme: "#22D3EE", strong: "#34D399", developing: "#FBBF24", weakening: "#FB923C", low: "#5A6B84",
  healthy_accumulation: "#34D399", neutral: "#A3A3A3", distribution: "#FB923C", heavy_distribution: "#F87171",
  healthy: "#34D399", moderate: "#FBBF24", thin: "#FB923C", critical: "#F87171",
  accumulating: "#34D399", distributing: "#FB923C",
  breaking_out: "#34D399", near_ath: "#A3E635", extended: "#FBBF24", overheated: "#FB923C",
  reversal_risk: "#F87171",
};

function bandColor(b?: string) { return BAND_COLORS[b || "neutral"] || "#A3A3A3"; }

export function IntelGauge({ label, score, band, invert = false, hint }:
  { label: string; score?: number; band?: string; invert?: boolean; hint?: string }) {
  if (score === null || score === undefined) return null;
  const color = invert
    ? (score >= 65 ? "#F87171" : score >= 45 ? "#FB923C" : score >= 25 ? "#A3A3A3" : "#34D399")
    : bandColor(band) ;
  return (
    <div className="py-1">
      <div className="flex justify-between text-xs">
        <span className="text-ink/75">{label}</span>
        <span className="num font-bold" style={{ color }}>{Math.round(score)}</span>
      </div>
      <div className="scorebar mt-1"><div style={{ width: `${score}%`, background: color }} /></div>
      {band && <div className="text-2xs mt-0.5 font-semibold" style={{ color }}>{bandLabel(band)}</div>}
      {hint && <div className="text-2xs text-dim leading-snug mt-0.5">{hint}</div>}
    </div>
  );
}

export function bandLabel(b?: string) {
  return { extreme: "Extreme Momentum", strong: "Strong Momentum", developing: "Developing",
    weakening: "Weakening", low: "Low Momentum", healthy_accumulation: "Healthy accumulation",
    neutral: "Neutral", distribution: "Distribution", heavy_distribution: "Heavy distribution",
    healthy: "Healthy", moderate: "Moderate", thin: "Thin", critical: "Critical",
    accumulating: "Whale accumulation", distributing: "Whale distribution" }[b || ""] || (b || "—");
}

const sevColor: Record<string, string> = {
  info: "#8A9BB4", warning: "#FBBF24", danger: "#F87171", positive: "#34D399" };

function FactorRow({ f }: { f: any }) {
  const arrow = f.direction > 0 ? "▲" : f.direction < 0 ? "▼" : "•";
  const color = f.direction > 0 ? "#34D399" : f.direction < 0 ? "#F87171" : "#8A9BB4";
  return (
    <div className="flex items-baseline gap-2 py-1 border-b border-line/40 last:border-0">
      <span style={{ color }} className="text-2xs">{arrow}</span>
      <span className="text-xs text-ink/80 w-[110px] shrink-0">{f.label}</span>
      <span className="num text-xs font-bold w-[26px] text-right" style={{ color: bandColor(null) === color ? "#8A9BB4" : color }}>{Math.round(f.score)}</span>
      <span className="text-2xs text-dim leading-snug flex-1">{f.detail}</span>
    </div>
  );
}

/* ---------- the full intelligence panel for token pages (§15) ---------- */
export function MarketIntelPanel({ mi }: { mi: any }) {
  const [open, setOpen] = React.useState(false);
  if (!mi || mi.momentum === undefined) return null;
  const mom = mi.momentum, sp = mi.sell_pressure || {}, lh = mi.liquidity_health || {};
  const ath = mi.ath || {}, va = mi.volume_accel || {}, whales = mi.whales || {};
  const conf = mi.confluence || {}, revs = mi.reversals || [], pr = mi.pressure || {};
  return (
    <Panel title={<span>Market intelligence <span className="panel-sub">multi-factor, fully explained</span></span>}>
      <div className="p-4 space-y-3">
        {/* composite confluence banner (§13) */}
        {conf.signal && conf.signal !== "none" && (
          <div className="rounded-lg px-3 py-2 text-sm font-semibold"
               style={{ background: conf.signal === "emerging_momentum" ? "#34D39912" : "#F8717112",
                        border: `1px solid ${conf.signal === "emerging_momentum" ? "#34D39955" : "#F8717155"}`,
                        color: conf.signal === "emerging_momentum" ? "#34D399" : "#F87171" }}>
            {conf.label} <span className="text-ink/60 font-normal">({conf.strength})</span>
            {conf.reasons?.length > 0 && (
              <div className="text-xs font-normal text-ink/60 mt-1 leading-snug">{conf.reasons.join(" · ")}</div>
            )}
          </div>
        )}
        {(revs || []).length > 0 && (
          <div className="space-y-1.5">
            {revs.map((r: any, i: number) => (
              <div key={i} className="rounded-lg px-3 py-2 text-xs"
                   style={{ background: "#F8717110", border: "1px solid #F8717140", color: "#F87171" }}>
                <b>{r.title}</b>
                <div className="text-ink/60 mt-0.5 leading-snug">{r.detail}</div>
              </div>
            ))}
          </div>
        )}

        <div className="grid grid-cols-3 gap-x-4 border-t border-line/60 pt-2">
          <IntelGauge label="Momentum" score={mom.score} band={mom.band} hint="price+volume+txns+holders+liquidity+attention" />
          <IntelGauge label="Sell pressure" score={sp.score} band={sp.band} invert hint="flow, whale net, holder & liquidity behavior (inferred)" />
          <IntelGauge label="Liquidity health" score={lh.score} band={lh.band} hint={lh.detail} />
        </div>

        {/* why this score (§5, §30) */}
        <button className="text-xs text-cyanx hover:underline" onClick={() => setOpen(!open)}>
          {open ? "▾ Hide score breakdown" : "▸ Why this momentum score?"}
        </button>
        {open && (
          <div>
            <div className="rounded-lg bg-bg/40 border border-line/60 px-2.5 py-1">
              {(mom.factors || []).map((f: any) => <FactorRow key={f.key} f={f} />)}
            </div>
            {(sp.factors || []).length > 0 && (
              <>
                <div className="text-xs font-bold tracking-widest text-dim uppercase mt-3 mb-1">Sell-pressure drivers</div>
                <div className="rounded-lg bg-bg/40 border border-line/60 px-2.5 py-1">
                  {sp.factors.map((f: any) => <FactorRow key={f.key} f={f} />)}
                </div>
              </>
            )}
            {sp.note && <div className="text-2xs text-dim mt-2 leading-snug">⛨ {sp.note}</div>}
          </div>
        )}

        {/* buy vs sell pressure (§11) + volume accel (§12) */}
        <div className="grid grid-cols-2 gap-x-4 border-t border-line/60 pt-2">
          <div>
            <div className="text-xs font-bold tracking-widest text-dim uppercase mb-1">Buy ↔ sell pressure</div>
            <PressureBar share={pr.buy_share_1h} buys={pr.buys_1h} sells={pr.sells_1h} period="1h" />
            <PressureBar share={pr.buy_share_24h} period="24h" />
          </div>
          <div>
            <div className="text-xs font-bold tracking-widest text-dim uppercase mb-1">Volume acceleration</div>
            {["m5", "m15", "h1", "h4"].map((k) => va[k] && (
              <div key={k} className="flex justify-between text-xs py-0.5">
                <span className="text-dim">{k}</span>
                <span className={`num ${va[k].growth_pct > 100 ? "text-cyanx font-bold" : va[k].growth_pct < -30 ? "text-redx" : "text-ink/80"}`}>
                  {va[k].growth_pct > 0 ? "+" : ""}{va[k].growth_pct}%
                </span>
              </div>
            ))}
            {va.abnormal && <div className="text-2xs text-amberx mt-0.5">⚡ abnormal volume spike</div>}
            {va.price_up_volume_down && <div className="text-2xs text-redx mt-0.5">⚠ price ↑ volume ↓ divergence</div>}
          </div>
        </div>

        {/* ATH position (§7) + whales (§8) */}
        <div className="grid grid-cols-2 gap-x-4 border-t border-line/60 pt-2">
          <div>
            <div className="text-xs font-bold tracking-widest text-dim uppercase mb-1">Price position</div>
            {ath.status && (
              <>
                <Badge kind={ath.status === "reversal_risk" ? "red" : ath.status === "breaking_out" || ath.status === "near_ath" ? "green" : ath.status === "neutral" ? "gray" : "amber"}>
                  {ath.status_label}
                </Badge>
                {ath.distance_pct !== undefined && (
                  <div className="text-xs text-dim mt-1.5 leading-snug">
                    {ath.distance_pct}% below tracked high · {ath.new_ath ? "new high this tick 🚩" : `${ath.time_since_ath_h}h since high`}
                  </div>
                )}
                <div className="text-2xs text-dim/70 mt-0.5">24h range {ath.low_24h?.toPrecision(3)} – {ath.high_24h?.toPrecision(3)}</div>
              </>
            )}
          </div>
          <div>
            <div className="text-xs font-bold tracking-widest text-dim uppercase mb-1">Whales <span className="normal-case font-normal">(inferred)</span></div>
            <Badge kind={whales.classification === "accumulating" ? "green" : whales.classification === "neutral" ? "gray" : "red"}>
              {whales.label}
            </Badge>
            <div className="text-xs text-dim mt-1.5 leading-snug">{whales.detail}</div>
          </div>
        </div>
      </div>
    </Panel>
  );
}

function PressureBar({ share, buys, sells, period }:
  { share?: number; buys?: number; sells?: number; period: string }) {
  if (share === undefined) return null;
  const pct = Math.round(share * 100);
  return (
    <div className="py-0.5">
      <div className="flex justify-between text-2xs text-dim">
        <span>{period}</span>
        <span className="num">{buys !== undefined ? `${Math.round(buys)} buys · ${Math.round(sells || 0)} sells` : ""} <b style={{ color: pct >= 52 ? "#34D399" : pct <= 46 ? "#F87171" : "#A3A3A3" }}>{pct}% buy</b></span>
      </div>
      <div className="flex h-[6px] rounded overflow-hidden mt-0.5 bg-bg">
        <div style={{ width: `${pct}%`, background: "#34D399", transition: "width .5s" }} />
        <div style={{ width: `${100 - pct}%`, background: "#F87171", transition: "width .5s" }} />
      </div>
    </div>
  );
}

/* ---------- signal history / live intelligence feed (§15, §20) ---------- */
export function SignalFeed({ address, limit = 12, title }: { address?: string; limit?: number; title?: React.ReactNode }) {
  const { data } = useApi(`/signals/feed?limit=${limit}${address ? `&address=${address}` : ""}`, 30000);
  const items = data?.items || [];
  return (
    <Panel title={title || <span>{address ? "Signal history" : "Live intelligence feed"}</span>}>
      <div className="max-h-[360px] overflow-y-auto">
        {items.length === 0 && <div className="p-4 text-dim text-xs">No notable events yet — transitions appear here as the engine detects them.</div>}
        {items.map((e: any, i: number) => (
          <div key={i} className="px-4 py-2 border-b border-line/40 last:border-0 flex gap-2.5 items-start">
            <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0" style={{ background: sevColor[e.severity] || sevColor.info }} />
            <div className="flex-1 min-w-0">
              <div className="text-sm">
                {!address && <a href={`/token/${e.address}`} className="font-bold text-cyanx hover:underline">${e.symbol}</a>}{" "}
                <span className="text-ink/85 font-semibold">{e.title}</span>
              </div>
              <div className="text-2xs text-dim leading-snug mt-0.5 line-clamp-2">{e.detail}</div>
            </div>
            <span className="text-2xs text-dim num shrink-0">{timeStr(e.ts)}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function timeStr(ts: number) {
  const s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
}

/* ---------- compact badges for cards/tables ---------- */
export function MiniIntel({ item }: { item: any }) {
  return (
    <div className="flex gap-1 flex-wrap">
      {typeof item.momentum === "number" && (
        <span className="pill-mini" style={{ color: bandColor(item.momentum_band), borderColor: bandColor(item.momentum_band) + "55",
              background: bandColor(item.momentum_band) + "12" }} title={`Momentum band: ${bandLabel(item.momentum_band)}`}>
          ⚡{Math.round(item.momentum)}
        </span>
      )}
      {typeof item.sellp === "number" && item.sellp >= 65 && (
        <span className="pill-mini" style={{ color: "#F87171", borderColor: "#F8717155", background: "#F8717112" }} title={`Sell pressure ${item.sellp}/100 — ${bandLabel(item.sellp_band)}`}>
          sellp {Math.round(item.sellp)}
        </span>
      )}
      {item.ath_status === "breaking_out" && <Badge kind="green">breakout</Badge>}
      {item.ath_status === "overheated" && <Badge kind="amber">overheated</Badge>}
      {item.ath_status === "reversal_risk" && <Badge kind="red">reversal risk</Badge>}
      {(item.reversals || 0) > 0 && <Badge kind="amber">divergence</Badge>}
      {item.confluence === "emerging_momentum" && <Badge kind="violet">confluence</Badge>}
      {item.whale_class === "distributing" && <Badge kind="red">whales distributing</Badge>}
      {item.whale_class === "accumulating" && <Badge kind="green">whales accumulating</Badge>}
    </div>
  );
}

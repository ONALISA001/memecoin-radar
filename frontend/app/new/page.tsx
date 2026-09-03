"use client";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { Badge, Empty, Panel, RowBadges, TokenAvatar } from "@/components/ui";
import { MiniIntel, bandLabel } from "@/components/marketintel";
import { fmtNum, fmtPct, timeAgo } from "@/lib/format";

const SORTS: [string, string][] = [
  ["newest", "Newest"], ["momentum", "Fastest momentum"], ["volume", "Highest volume"],
  ["liquidity", "Highest liquidity"], ["holder_growth", "Largest holder growth"],
  ["buy_pressure", "Strongest buy pressure"], ["price_increase", "Largest price increase"],
  ["vol_accel", "Biggest volume acceleration"], ["low_risk", "Lowest risk"],
  ["activity", "Most active"], ["discussed", "Most discussed"],
];
const AGES: [string, string][] = [["", "Any age"], ["1", "< 1h"], ["6", "< 6h"], ["24", "< 24h"], ["72", "< 3d"], ["168", "< 7d"]];

export default function NewCoinsPage() {
  const [sort, setSort] = useState("newest");
  const [age, setAge] = useState("");
  const { data, loading } = useApi(`/tokens/new?sort=${sort}&limit=48${age ? `&max_age_h=${age}` : ""}`, 30000);
  const items = data?.items || [];
  return (
    <div className="space-y-4">
      <div className="panel p-4 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-extrabold">🆕 New Memecoins</h1>
        <span className="text-[11px] text-dim">Recently launched/tracked tokens with momentum, sell-pressure and risk context.</span>
        <div className="flex-1" />
        <select className="btn !py-1 bg-panel" value={age} onChange={(e) => setAge(e.target.value)}>
          {AGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select className="btn !py-1 bg-panel" value={sort} onChange={(e) => setSort(e.target.value)}>
          {SORTS.map(([v, l]) => <option key={v} value={v}>Sort: {l}</option>)}
        </select>
      </div>
      {loading && !data && <Empty text="Scanning new launches…" />}
      {!loading && items.length === 0 && <Empty text="No tokens match — widen the age filter." />}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {items.map((t: any) => (
          <a key={t.address} href={`/token/${t.address}`}
             className="panel p-4 hover:border-line2 transition group">
            <div className="flex items-center gap-2.5">
              <TokenAvatar url={t.image_url} symbol={t.symbol} size={34} />
              <div className="min-w-0">
                <div className="font-bold text-[13px] truncate">${t.symbol} <span className="text-dim font-normal text-[10.5px]">{t.name}</span></div>
                <div className="text-[9.5px] text-dim num">{t.dex} · {ageStr(t.age_hours)} old</div>
              </div>
              <div className="ml-auto text-right">
                <div className="num text-[12.5px] font-bold">${priceStr(t.price)}</div>
                <div className={`num text-[10.5px] font-semibold ${(t.price_change_h24 || 0) >= 0 ? "text-greenx" : "text-redx"}`}>
                  {fmtPct(t.price_change_h24)} 24h
                </div>
              </div>
            </div>

            <div className="grid grid-cols-5 gap-1 mt-3 text-center">
              {[["5m", t.price_change_m5], ["15m", t.price_change_m15], ["1h", t.price_change_h1], ["4h", t.price_change_h4], ["24h", t.price_change_h24]].map(([k, v]: any) => (
                <div key={k} className="rounded-md bg-bg/50 py-1">
                  <div className="text-[8.5px] text-dim">{k}</div>
                  <div className={`num text-[10px] font-bold ${(v || 0) >= 0 ? "text-greenx" : "text-redx"}`}>{fmtPct(clampPct(v), false)}</div>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-3 gap-x-3 mt-3 text-[9.5px]">
              <div className="flex justify-between"><span className="text-dim">MCap</span><span className="num">${fmtNum(t.mcap)}</span></div>
              <div className="flex justify-between"><span className="text-dim">Liquidity</span><span className="num">${fmtNum(t.liquidity)}</span></div>
              <div className="flex justify-between"><span className="text-dim">Vol 24h</span><span className="num">${fmtNum(t.volume_24h)}</span></div>
              <div className="flex justify-between"><span className="text-dim">Holders</span><span className="num">{fmtNum(t.holders)}</span></div>
              <div className="flex justify-between"><span className="text-dim">Δ holders 1h</span><span className={`num ${(t.holder_growth || 0) >= 0 ? "text-greenx" : "text-redx"}`}>{fmtPct(t.holder_growth)}</span></div>
              <div className="flex justify-between"><span className="text-dim">Buy share 1h</span><span className="num">{Math.round((t.buy_share || 0.5) * 100)}%</span></div>
              <div className="flex justify-between"><span className="text-dim">Buys 1h</span><span className="num">{fmtNum(t.buys_1h)}</span></div>
              <div className="flex justify-between"><span className="text-dim">Sells 1h</span><span className="num">{fmtNum(t.sells_1h)}</span></div>
              <div className="flex justify-between"><span className="text-dim">Vol accel</span><span className={`num ${(t.vol_accel_h1 || 0) > 100 ? "text-cyanx font-bold" : ""}`}>{fmtPct(t.vol_accel_h1)}</span></div>
            </div>

            <div className="grid grid-cols-3 gap-x-3 mt-3">
              <Gauge t="Momentum" v={t.momentum} c={(t.momentum || 0) >= 65 ? "#34D399" : (t.momentum || 0) >= 50 ? "#FBBF24" : "#5A6B84"} s={bandLabel(t.momentum_band)} />
              <Gauge t="Sell pressure" v={t.sellp} c={(t.sellp || 0) >= 65 ? "#F87171" : (t.sellp || 0) >= 45 ? "#FB923C" : "#34D399"} s={bandLabel(t.sellp_band)} />
              <Gauge t="Risk" v={t.risk} c={(t.risk || 0) >= 70 ? "#F87171" : (t.risk || 0) >= 40 ? "#FBBF24" : "#34D399"} s={t.risk_class} />
            </div>
            <div className="mt-2 flex gap-1 flex-wrap"><RowBadges item={t} /><MiniIntel item={t} /></div>
          </a>
        ))}
      </div>
    </div>
  );
}

function Gauge({ t, v, c, s }: { t: string; v?: number; c: string; s?: string }) {
  return (
    <div>
      <div className="flex justify-between text-[9px]"><span className="text-dim uppercase tracking-wider">{t}</span><span className="num font-bold" style={{ color: c }}>{Math.round(v || 0)}</span></div>
      <div className="scorebar mt-0.5"><div style={{ width: `${v || 0}%`, background: c }} /></div>
      <div className="text-[8.5px] mt-0.5" style={{ color: c }}>{s}</div>
    </div>
  );
}
const clampPct = (v?: number) => v === undefined || v === null ? undefined : Math.max(-99.9, Math.min(999, v));
function priceStr(p?: number) {
  if (!p) return "—";
  if (p >= 1) return p.toFixed(4);
  if (p >= 0.0001) return p.toPrecision(3);
  return p.toExponential(2);
}
function ageStr(h?: number) {
  if (h === undefined || h === null || h >= 900) return "age unknown";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 48) return `${Math.round(h)}h`;
  return `${Math.round(h / 24)}d`;
}

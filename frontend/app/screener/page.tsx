"use client";
import { useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import { Badge, Empty, Panel, TokenAvatar } from "@/components/ui";
import { MiniIntel, bandLabel } from "@/components/marketintel";
import { fmtNum, fmtPct } from "@/lib/format";

const STATUS_OPTS = ["emerging", "trending", "breakout", "near_ath", "extended", "distribution", "declining", "newly_launched"];
const WHALE_OPTS = ["accumulating", "neutral", "distributing", "heavy_distribution"];

function NumInput({ label, value, set, placeholder }: any) {
  return (
    <label className="block">
      <span className="text-[9.5px] text-dim uppercase tracking-wider">{label}</span>
      <input type="number" value={value} placeholder={placeholder || "—"}
             onChange={(e) => set(e.target.value)}
             className="mt-0.5 w-full bg-bg/60 border border-line rounded-md px-2 py-1 text-[11px] num outline-none focus:border-cyanx" />
    </label>
  );
}

export default function ScreenerPage() {
  const [f, setF] = useState<Record<string, string>>({});
  const [statusSel, setStatusSel] = useState<string[]>([]);
  const [whaleSel, setWhaleSel] = useState<string[]>([]);
  const [sort, setSort] = useState("momentum");
  const set = (k: string) => (v: string) => setF((p) => ({ ...p, [k]: v }));

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    Object.entries(f).forEach(([k, v]) => { if (v !== "" && v !== undefined) p.set(k, v); });
    if (statusSel.length) p.set("status", statusSel.join(","));
    if (whaleSel.length) p.set("whale", whaleSel.join(","));
    p.set("sort", sort); p.set("limit", "80");
    return p.toString();
  }, [f, statusSel, whaleSel, sort]);

  const { data, loading } = useApi(`/screener?${qs}`, 30000);
  const items = data?.items || [];
  const active = Object.values(f).filter(Boolean).length + statusSel.length + whaleSel.length;

  return (
    <div className="space-y-4">
      <div className="panel p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-extrabold">🔎 Memecoin Screener</h1>
          <Badge kind="cyan">{data?.total_matching ?? "…"} matching</Badge>
          {active > 0 && <button className="btn !py-1" onClick={() => { setF({}); setStatusSel([]); setWhaleSel([]); }}>clear {active} filters</button>}
          <div className="flex-1" />
          <select className="btn !py-1 bg-panel" value={sort} onChange={(e) => setSort(e.target.value)}>
            {[["momentum", "Momentum"], ["newest", "Newest"], ["volume", "Volume"], ["liquidity", "Liquidity"],
              ["buy_pressure", "Buy pressure"], ["holder_growth", "Holder growth"], ["vol_accel", "Vol acceleration"],
              ["low_risk", "Lowest risk"], ["price_increase", "Price increase"]].map(([v, l]) => <option key={v} value={v}>Sort: {l}</option>)}
          </select>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-x-3 gap-y-2 mt-3">
          <NumInput label="min mcap $" value={f.min_mcap || ""} set={set("min_mcap")} />
          <NumInput label="max mcap $" value={f.max_mcap || ""} set={set("max_mcap")} />
          <NumInput label="min liquidity $" value={f.min_liquidity || ""} set={set("min_liquidity")} />
          <NumInput label="min 1h volume $" value={f.min_volume_1h || ""} set={set("min_volume_1h")} />
          <NumInput label="min vol accel %" value={f.min_vol_accel || ""} set={set("min_vol_accel")} />
          <NumInput label="min momentum" value={f.min_momentum || ""} set={set("min_momentum")} />
          <NumInput label="max momentum" value={f.max_momentum || ""} set={set("max_momentum")} />
          <NumInput label="min holder growth %" value={f.min_holder_growth || ""} set={set("min_holder_growth")} />
          <NumInput label="min buy share %" value={f.min_buy_share || ""} set={set("min_buy_share")} />
          <NumInput label="max sell pressure" value={f.max_sellp || ""} set={set("max_sellp")} />
          <NumInput label="max risk" value={f.max_risk || ""} set={set("max_risk")} />
          <NumInput label="min liquidity health" value={f.min_liq_health || ""} set={set("min_liq_health")} />
          <NumInput label="max age (h)" value={f.max_age_h || ""} set={set("max_age_h")} />
          <NumInput label="min age (h)" value={f.min_age_h || ""} set={set("min_age_h")} />
          <div className="col-span-2">
            <span className="text-[9.5px] text-dim uppercase tracking-wider">whale flow</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {WHALE_OPTS.map((w) => (
                <button key={w} onClick={() => setWhaleSel((p) => p.includes(w) ? p.filter((x) => x !== w) : [...p, w])}
                        className={`btn !py-0.5 !text-[9.5px] ${whaleSel.includes(w) ? "btn-primary" : ""}`}>{w.replace("_", " ")}</button>
              ))}
            </div>
          </div>
          <div className="col-span-2">
            <span className="text-[9.5px] text-dim uppercase tracking-wider">status (multi)</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {STATUS_OPTS.map((s) => (
                <button key={s} onClick={() => setStatusSel((p) => p.includes(s) ? p.filter((x) => x !== s) : [...p, s])}
                        className={`btn !py-0.5 !text-[9.5px] ${statusSel.includes(s) ? "btn-primary" : ""}`}>{s.replace("_", " ")}</button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {loading && !data && <Empty text="Screening…" />}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {items.map((t: any) => (
          <a key={t.address} href={`/token/${t.address}`} className="panel p-3.5 hover:border-line2 transition">
            <div className="flex items-center gap-2.5">
              <TokenAvatar url={t.image_url} symbol={t.symbol} size={30} />
              <div className="min-w-0 flex-1">
                <div className="font-bold text-[13px]">${t.symbol} <span className="text-dim font-normal text-[10px]">{t.name}</span></div>
                <div className="text-[9.5px] text-dim num">${fmtNum(t.mcap)} mcap · ${fmtNum(t.liquidity)} liq · {t.age_hours !== undefined && t.age_hours < 900 ? `${Math.round(t.age_hours)}h old` : "older"}</div>
              </div>
              <div className="text-right">
                <div className={`num text-[11px] font-bold ${(t.price_change_h24 || 0) >= 0 ? "text-greenx" : "text-redx"}`}>{fmtPct(t.price_change_h24)} 24h</div>
                <div className="text-[9px] text-dim num">1h {fmtPct(t.price_change_h1)}</div>
              </div>
            </div>
            <div className="grid grid-cols-4 gap-x-2 mt-2.5 text-[9px]">
              <Mini t="Mom" v={t.momentum} good={1} />
              <Mini t="SellP" v={t.sellp} good={-1} />
              <Mini t="LiqH" v={t.liq_health} good={1} />
              <Mini t="Risk" v={t.risk} good={-1} />
            </div>
            <div className="mt-2"><MiniIntel item={t} /></div>
          </a>
        ))}
        {!loading && items.length === 0 && <div className="col-span-3"><Empty text="No tokens pass all filters — loosen a constraint." /></div>}
      </div>
    </div>
  );
}

function Mini({ t, v, good }: { t: string; v?: number; good: 1 | -1 }) {
  const x = v || 0;
  const c = good > 0 ? (x >= 65 ? "#34D399" : x >= 45 ? "#FBBF24" : "#5A6B84")
                     : (x >= 65 ? "#F87171" : x >= 45 ? "#FBBF24" : "#34D399");
  return (
    <div>
      <div className="flex justify-between"><span className="text-dim">{t}</span><span className="num font-bold" style={{ color: c }}>{Math.round(x)}</span></div>
      <div className="scorebar mt-0.5"><div style={{ width: `${x}%`, background: c }} /></div>
    </div>
  );
}

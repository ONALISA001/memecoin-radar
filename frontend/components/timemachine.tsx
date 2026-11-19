"use client";
import React, { useState } from "react";
import { Area, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip,
         XAxis, YAxis } from "recharts";
import { useApi } from "@/lib/api";
import { fmtPct, fmtPrice } from "@/lib/format";
import { Empty, Panel } from "./ui";

const tooltipStyle = {
  background: "#121722", border: "1px solid #252D3D", borderRadius: 8,
  fontSize: 11, color: "#DCE4EE",
};

const KIND_LABEL: Record<string, string> = {
  momentum_acceleration: "Momentum acceleration", unusual_volume: "Unusual volume",
  buy_pressure_increasing: "Buy pressure", holder_growth: "Holder growth",
  liquidity_increase: "Liquidity increase", new_high: "New high",
  whale_accumulation: "Whale accumulation", pattern_emerging: "Emerging pattern",
};

const verdictCls = (label?: string) =>
  !label ? "text-dim"
    : label.includes("target") ? "text-greenx"
    : label.includes("stop") ? "text-redx"
    : "text-amberx";

function OddsBar({ t, s, n }: { t: number; s: number; n: number }) {
  return (
    <div>
      <div className="flex h-2.5 rounded overflow-hidden bg-line/40">
        <div className="bg-greenx/80" style={{ width: `${t}%` }} />
        <div className="bg-dim/40" style={{ width: `${n}%` }} />
        <div className="bg-redx/80" style={{ width: `${s}%` }} />
      </div>
      <div className="flex justify-between text-xs mt-1 num">
        <span className="text-greenx">Target first {t.toFixed(1)}%</span>
        <span className="text-dim">Neither {n.toFixed(1)}%</span>
        <span className="text-redx">Stop first {s.toFixed(1)}%</span>
      </div>
    </div>
  );
}

function Fan({ fan, stop, target }: { fan: any[]; stop: number; target: number }) {
  // Stacked areas draw the 10–90 and 25–75 bands; the line is the median.
  const rows = fan.map((f) => ({
    min: f.min, base: f.p10, outer: f.p90 - f.p10, inner_base: f.p25, inner: f.p75 - f.p25, p50: f.p50,
  }));
  const hrs = (m: number) => (m >= 60 ? `${(m / 60).toFixed(m % 60 ? 1 : 0)}h` : `${m}m`);
  return (
    <div className="h-[180px]">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 6, right: 8, left: 4, bottom: 0 }}>
          <XAxis dataKey="min" tickFormatter={hrs} tick={{ fontSize: 10, fill: "#7C8BA1" }} />
          <YAxis width={44} tickFormatter={(v) => `${Math.round(v)}%`} tick={{ fontSize: 10, fill: "#7C8BA1" }}
                 domain={[(d: number) => Math.min(d, -stop * 1.2), (d: number) => Math.max(d, target * 1.1)]} />
          <Tooltip contentStyle={tooltipStyle} labelFormatter={(m) => `after ${hrs(Number(m))}`}
                   formatter={(v: any, k: any) => k === "p50" ? [fmtPct(Number(v)), "median"] : [null, null]} />
          <Area dataKey="base" stackId="o" stroke="none" fill="transparent" isAnimationActive={false} />
          <Area dataKey="outer" stackId="o" stroke="none" fill="#22D3EE" fillOpacity={0.12} isAnimationActive={false} />
          <Area dataKey="inner_base" stackId="i" stroke="none" fill="transparent" isAnimationActive={false} />
          <Area dataKey="inner" stackId="i" stroke="none" fill="#22D3EE" fillOpacity={0.22} isAnimationActive={false} />
          <Line dataKey="p50" stroke="#22D3EE" dot={false} strokeWidth={1.5} isAnimationActive={false} />
          <ReferenceLine y={target} stroke="#34D399" strokeDasharray="4 3" />
          <ReferenceLine y={0} stroke="#7C8BA1" strokeOpacity={0.4} />
          <ReferenceLine y={-stop} stroke="#F87171" strokeDasharray="4 3" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function TimeMachinePanel({ address }: { address: string }) {
  const [form, setForm] = useState({ stop: "", target: "", hours: "" });
  const [query, setQuery] = useState("");
  const { data, error, loading } = useApi(`/tokens/${address}/timemachine${query}`, 0);

  function run(e: React.FormEvent) {
    e.preventDefault();
    const q = new URLSearchParams();
    if (form.stop) q.set("stop_pct", form.stop);
    if (form.target) q.set("target_pct", form.target);
    if (form.hours) q.set("hours", form.hours);
    const s = q.toString();
    setQuery(s ? `?${s}` : "");
  }

  const p = data?.plan;
  const sim = data?.simulation;
  const h = data?.history;
  return (
    <Panel title={<span>Time Machine <span className="badge badge-green !text-2xs ml-1">real prices · replay</span></span>}>
      <form onSubmit={run} className="px-3.5 pt-3 flex flex-wrap items-end gap-2 text-xs">
        {([["stop", "Stop %", p?.stop_pct], ["target", "Target %", p?.target_pct],
           ["hours", "Hold (h)", p?.hours]] as const).map(([k, label, cur]) => (
          <label key={k} className="text-dim">
            {label}
            <input className="inp w-20 mt-1 num block" inputMode="decimal" placeholder={cur != null ? String(cur) : ""}
                   value={(form as any)[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
          </label>
        ))}
        <button className="btn btn-primary" type="submit" disabled={loading}>Replay</button>
        {p && <span className="text-dim ml-auto">{p.r_multiple.toFixed(2)}R · break-even {p.breakeven_pct}%</span>}
      </form>

      {!data && !error && <Empty text="Replaying…" />}
      {error && <Empty text={`Couldn't run: ${error}`} />}
      {data && (
        <div className="px-3.5 py-3 space-y-3 text-sm">
          {sim ? (<>
            <div>
              <div className="flex items-baseline justify-between">
                <span className={`font-bold ${verdictCls(sim.verdict.label)}`}>{sim.verdict.label}</span>
                <span className="text-2xs text-dim">{sim.replays.toLocaleString()} replays from entry {fmtPrice(data.entry_price)}</span>
              </div>
              <div className="text-xs text-dim mt-0.5">{sim.verdict.text}</div>
            </div>
            <OddsBar t={sim.target_first_pct} s={sim.stop_first_pct} n={sim.neither_pct} />
            <Fan fan={sim.fan} stop={p.stop_pct} target={p.target_pct} />
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
              <div><div className="text-dim">Avg R</div><div className={`num font-bold ${sim.avg_r >= 0 ? "text-greenx" : "text-redx"}`}>{sim.avg_r.toFixed(2)}R</div></div>
              <div><div className="text-dim">Green at exit</div><div className="num font-bold">{sim.green_pct.toFixed(1)}%</div></div>
              <div><div className="text-dim">Median exit</div><div className="num font-bold">{fmtPct(sim.exit_return_pct.p50)}</div></div>
              <div><div className="text-dim">Typical time to hit</div><div className="num font-bold">{sim.median_minutes_to_exit != null ? `${sim.median_minutes_to_exit} min` : "—"}</div></div>
            </div>
            <div className="text-2xs text-dim">
              Exit return range: 5% {fmtPct(sim.exit_return_pct.p5)} · 25% {fmtPct(sim.exit_return_pct.p25)} · 75% {fmtPct(sim.exit_return_pct.p75)} · 95% {fmtPct(sim.exit_return_pct.p95)}.
              Pool: {data.pool_hours}h of unbroken 5-min prices from this token.
            </div>
          </>) : (
            <div className="text-dim text-xs">{data.reason}</div>
          )}

          <div className="border-t border-line/60 pt-2.5">
            <div className="flex items-baseline justify-between">
              <span className="font-semibold">What really happened</span>
              <span className="text-2xs text-dim">{data.kind ? KIND_LABEL[data.kind] || data.kind : "All bullish signals"}</span>
            </div>
            {h && h.counted > 0 ? (<>
              <div className={`text-xs mt-1 font-semibold ${verdictCls(h.verdict?.label)}`}>{h.verdict?.label}</div>
              <div className="text-xs text-dim">
                {h.counted} past signal{h.counted === 1 ? "" : "s"}: {h.target} hit the target, {h.stop} the stop, {h.neither} neither
                {h.avg_r != null && <> · avg {h.avg_r.toFixed(2)}R</>}.
                {h.data_gap > 0 && <> {h.data_gap} skipped for data gaps.</>}
              </div>
              <div className="text-2xs text-dim">{h.scope}.</div>
            </>) : (
              <div className="text-xs text-dim mt-1">
                No finished past signals to re-run yet{h?.data_gap ? ` (${h.data_gap} skipped for data gaps)` : ""}.
              </div>
            )}
          </div>
          <div className="text-2xs text-dim">{data.caveat}</div>
        </div>
      )}
    </Panel>
  );
}

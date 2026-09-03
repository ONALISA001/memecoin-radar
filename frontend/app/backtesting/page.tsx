"use client";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line,
         ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "@/lib/api";
import { Badge, Empty, Panel } from "@/components/ui";

const tooltipStyle = { background: "#0C1017", border: "1px solid #1B2331", borderRadius: 8, fontSize: 11 };

function CurveChart({ title, data, yLabel }: { title: string; data: any[]; yLabel: string }) {
  const usable = (data || []).filter((d) => d.n > 0);
  if (!usable.length) return (
    <div className="h-[210px] flex items-center justify-center text-dim text-xs">No labeled outcomes yet.</div>
  );
  return (
    <div>
      <div className="text-[11px] font-bold tracking-wider text-dim uppercase mb-2">{title} <span className="normal-case font-normal">({yLabel})</span></div>
      <div className="h-[210px]">
        <ResponsiveContainer>
          <ComposedChart data={usable}>
            <CartesianGrid stroke="#141B27" vertical={false} />
            <XAxis dataKey="bucket" stroke="#3B4A61" tickLine={false} axisLine={false} />
            <YAxis yAxisId="l" domain={[0, 100]} stroke="#3B4A61" tickLine={false} axisLine={false}
                   tickFormatter={(v) => `${v}%`} width={44} />
            <YAxis yAxisId="r" orientation="right" stroke="#3B4A61" tickLine={false} axisLine={false}
                   tickFormatter={(v) => `${v}%`} width={46} />
            <Tooltip contentStyle={tooltipStyle}
                     formatter={(v: any, name: any) => [`${v}%`, name]} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <Bar yAxisId="l" dataKey="continuation_rate" name="continued %" fill="#22D3EE" radius={[2, 2, 0, 0]} />
            <Bar yAxisId="l" dataKey="reversal_rate" name="reversal/rug %" fill="#F87171" radius={[2, 2, 0, 0]} />
            <Line yAxisId="r" type="monotone" dataKey="median_fwd_ret" name="median fwd return" stroke="#FBBF24" strokeWidth={1.6} dot />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="text-[9.5px] text-dim mt-1">
        n per bucket: {usable.map((d) => d.n).join(" / ")}
      </div>
    </div>
  );
}

export default function BacktestingPage() {
  const { data, loading } = useApi("/calibration/overview", 60000);
  const [horizon, setHorizon] = useState<"3600" | "14400">("3600");
  if (loading && !data) return <Empty text="Loading calibration data…" />;
  const hz = data?.horizons?.[horizon];
  const rules = (data?.signal_rules || []).filter((r: any) => String(r.horizon_secs) === horizon);
  const suff = data?.data_sufficiency;
  return (
    <div className="space-y-4">
      <div className="panel p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-extrabold">🧪 Signal Calibration & Backtesting</h1>
          <Badge kind={suff === "GOOD" ? "green" : suff === "BUILDING" ? "amber" : "gray"}>
            {suff || "?"}: {data?.total_outcomes ?? 0} labeled outcomes
          </Badge>
          {(data?.retro_share ?? 0) > 0.5 && (
            <Badge kind="amber">{(Math.round((data.retro_share ?? 0) * 100))}% retro-scored history</Badge>
          )}
          <div className="flex-1" />
          <div className="flex gap-1">
            {[["3600", "+1h horizon"], ["14400", "+4h horizon"]].map(([v, l]) => (
              <button key={v} onClick={() => setHorizon(v as any)}
                      className={`btn !py-1 ${horizon === v ? "btn-primary" : ""}`}>{l}</button>
            ))}
          </div>
        </div>
        <p className="text-[11px] text-dim mt-2 leading-relaxed max-w-4xl">
          For every signal snapshot, we look at what actually happened over the next horizon and label the
          outcome (continued / faded / reversal / rug). These are <b>historical relationships, not predictions</b> —
          they exist to tell you how much trust each score band has earned, and they update continuously.
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Panel title="Trend Score reliability">
          <div className="p-4"><CurveChart title="By Trend band" data={hz?.trend_curve} yLabel="outcome share per band" /></div>
        </Panel>
        <Panel title="FOMO reliability">
          <div className="p-4"><CurveChart title="By FOMO band" data={hz?.fomo_curve} yLabel="does hype continue?" /></div>
        </Panel>
        <Panel title="Risk Score reliability">
          <div className="p-4"><CurveChart title="By Risk band" data={hz?.risk_curve} yLabel="adverse events by risk level" /></div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start">
        <Panel title="Signal rule hit-rates" className="xl:col-span-2">
          <div className="overflow-x-auto">
            <table className="tbl">
              <thead><tr><th>RULE</th><th>HORIZON</th><th>N</th><th>HIT RATE</th><th>ADVERSE RATE</th><th>READ</th></tr></thead>
              <tbody>
                {rules.length === 0 && <tr><td colSpan={6}><Empty text="Rules accumulate as outcomes are labeled." /></td></tr>}
                {rules.map((r: any, i: number) => (
                  <tr key={i}>
                    <td className="!text-left font-semibold">{r.rule}</td>
                    <td className="num">+{r.horizon_secs / 3600}h</td>
                    <td className="num">{r.n}</td>
                    <td className={`num font-bold ${r.hit_rate_pct >= 50 ? "text-greenx" : r.hit_rate_pct >= 30 ? "text-amberx" : "text-redx"}`}>
                      {r.hit_rate_pct}%
                    </td>
                    <td className="num text-redx/90">{r.adverse_rate_pct}%</td>
                    <td className="text-[10px] text-dim max-w-[240px] whitespace-normal text-left">
                      {r.rule.startsWith("risk") || r.rule.includes("&")
                        ? "adverse outcome rate among flagged tokens"
                        : "share of signals that continued vs faded/reversed"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="How to read this page">
          <div className="p-4 space-y-2 text-[11px] text-ink/80 leading-relaxed">
            <p>· <b>Continuation rate</b> — % of snapshots in that score band that kept momentum (fwd return ≥ +10% without a &gt;30% drawdown).</p>
            <p>· <b>Reversal rate</b> — % that suffered a &gt;45% drawdown or liquidity collapse within the horizon.</p>
            <p>· A signal <b>earns trust only through measured hit-rates</b>. A band with n&lt;10 is anecdote, not evidence.</p>
            <p>· Retro-scored rows replay the engines over stored (partly simulated) history; live rows replace them as time passes.</p>
            <p className="text-amberx/90">· Past relationships break in new regimes. Small samples lie. This page exists to keep the platform honest (§21), not to promise returns.</p>
          </div>
        </Panel>
      </div>
    </div>
  );
}

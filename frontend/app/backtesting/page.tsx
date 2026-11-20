"use client";
import Link from "next/link";
import React, { useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Legend, Line,
         ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "@/lib/api";
import { SignalQualityPanel } from "@/components/intelfeed";
import { Badge, Empty, Panel } from "@/components/ui";
import { RatingBadge } from "@/components/verdict";
import { fmtPct } from "@/lib/format";

const tooltipStyle = { background: "#121722", border: "1px solid #252D3D", borderRadius: 8, fontSize: 11 };
const HORIZON_LABEL: Record<string, string> = { "3600": "+1h", "7200": "+2h", "14400": "+4h" };
const LOOKBACK: [string, number | null][] = [["24h", 1], ["3d", 3], ["7d", 7], ["30d", 30], ["All", null]];

function CurveChart({ data }: { data: any[] }) {
  const usable = (data || []).filter((d) => d.n > 0);
  if (!usable.length) return (
    <div className="h-[210px] flex items-center justify-center text-dim text-xs">No labeled outcomes in this window yet.</div>
  );
  return (
    <div>
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
            <Legend wrapperStyle={{ fontSize: 10.5 }} />
            <Bar yAxisId="l" dataKey="continuation_rate" name="continued %" fill="#22D3EE" radius={[2, 2, 0, 0]} />
            <Bar yAxisId="l" dataKey="reversal_rate" name="reversal/rug %" fill="#F87171" radius={[2, 2, 0, 0]} />
            <Line yAxisId="r" type="monotone" dataKey="median_fwd_ret" name="median fwd return" stroke="#FBBF24" strokeWidth={1.6} dot />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="text-2xs text-dim mt-1">
        n per bucket: {usable.map((d) => `${d.bucket}: ${d.n}`).join(" · ")}
        {usable.some((d) => d.n < 10) && <span className="text-amberx"> · buckets under 10 are anecdote</span>}
      </div>
    </div>
  );
}

function Tile({ label, value, cls = "", sub }: { label: string; value: React.ReactNode; cls?: string; sub?: React.ReactNode }) {
  return (
    <div className="panel px-4 py-3">
      <div className="text-2xs text-dim uppercase tracking-wider">{label}</div>
      <div className={`num text-xl font-bold mt-0.5 ${cls}`}>{value}</div>
      {sub && <div className="text-2xs text-dim mt-0.5">{sub}</div>}
    </div>
  );
}

function OutcomeMix({ outcomes, n }: { outcomes?: Record<string, number>; n: number }) {
  if (!outcomes || !n) return null;
  const order: [string, string][] = [["continued", "#22D3EE"], ["neutral", "#64748B"], ["faded", "#FBBF24"],
    ["reversal", "#FB923C"], ["rug", "#F87171"]];
  return (
    <div>
      <div className="h-2.5 rounded-full overflow-hidden flex bg-line/30">
        {order.map(([k, c]) => (outcomes[k] || 0) > 0 && (
          <div key={k} style={{ width: `${(outcomes[k] / n) * 100}%`, background: c }} title={`${k}: ${outcomes[k]}`} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 text-2xs text-dim mt-1">
        {order.map(([k, c]) => <span key={k}><span style={{ color: c }}>■</span> {k} {Math.round(((outcomes[k] || 0) / n) * 100)}%</span>)}
      </div>
    </div>
  );
}

/** How the verdict ratings aged (same scorecard as /verdicts, compact). */
function VerdictAccuracy({ days }: { days: number | null }) {
  const { data: sc } = useApi(`/verdicts/scorecard?days=${Math.min(days ?? 30, 30)}`, 120000);
  const rows = Object.entries(sc?.by_rating || {}).filter(([, s]: any) => s.calls > 0);
  return (
    <Panel title={<span>Verdict accuracy <span className="panel-sub">— avg return after each rating</span></span>}
           right={<Link href="/verdicts" className="text-xs text-cyanx hover:underline">all verdicts →</Link>}>
      {!sc ? <Empty text="Loading…" /> : rows.length === 0 ? <Empty text="No verdicts in this window yet." /> : (
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead><tr><th>RATING</th><th>CALLS</th><th>+1H</th><th>+6H</th><th>+24H</th><th>HIT RATE</th><th>RUGGED</th></tr></thead>
            <tbody>
              {rows.map(([r, s]: [string, any]) => (
                <tr key={r}>
                  <td className="!text-left"><RatingBadge rating={r} /></td>
                  <td className="num">{s.calls}</td>
                  {[s.avg_ret_1h, s.avg_ret_6h, s.avg_ret_24h].map((v: number | null, i: number) => (
                    <td key={i} className={`num ${v == null ? "text-dim" : v >= 0 ? "text-greenx" : "text-redx"}`}>{v == null ? "—" : fmtPct(v)}</td>
                  ))}
                  <td className="num font-bold">{s.hit_rate == null ? "—" : `${Math.round(s.hit_rate * 100)}%`}</td>
                  <td className="num text-redx/90">{s.rug_rate == null ? "—" : `${Math.round(s.rug_rate * 100)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-4 py-2 text-2xs text-dim">A good rating system shows Strong watch / Watch above Avoid / Rug risk on average return. Small samples mislead.</div>
        </div>
      )}
    </Panel>
  );
}

export default function BacktestingPage() {
  const [days, setDays] = useState<number | null>(null);
  const { data, loading } = useApi(`/calibration/overview${days ? `?days=${days}` : ""}`, 60000);
  const [horizon, setHorizon] = useState("3600");
  const [ruleKind, setRuleKind] = useState<"all" | "signal" | "risk">("all");
  if (loading && !data) return <Empty text="Loading calibration data…" />;
  const horizons: string[] = (data?.horizon_list || [3600, 14400]).map(String);
  const hz = data?.horizons?.[horizon];
  const sm = hz?.summary || { n: 0 };
  const rules = (data?.signal_rules || []).filter((r: any) => String(r.horizon_secs) === horizon
    && (ruleKind === "all" || (r.kind || (r.target === "rug" ? "risk" : "signal")) === ruleKind));
  const suff = data?.data_sufficiency;
  const hl = HORIZON_LABEL[horizon] || `+${Number(horizon) / 3600}h`;
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
          <div className="flex items-center gap-0.5" role="group" aria-label="Look-back window">
            <span className="text-2xs text-dim mr-1">History</span>
            {LOOKBACK.map(([l, d]) => (
              <button key={l} onClick={() => setDays(d)} aria-pressed={days === d}
                      className={`pill-mini ${days === d ? "!text-cyanx !border-cyanx/60" : "text-dim border-line"}`}>{l}</button>
            ))}
          </div>
          <div className="flex gap-1" role="group" aria-label="Horizon">
            {horizons.map((v) => (
              <button key={v} onClick={() => setHorizon(v)} aria-pressed={horizon === v}
                      className={`btn !py-1 ${horizon === v ? "btn-primary" : ""}`}>{HORIZON_LABEL[v] || v} horizon</button>
            ))}
          </div>
        </div>
        <p className="text-sm text-dim mt-2 leading-relaxed max-w-4xl">
          For every signal snapshot, we look at what actually happened over the next horizon and label the
          outcome (continued / faded / reversal / rug). These are <b>historical relationships, not predictions</b> —
          they exist to tell you how much trust each score band has earned, and they update continuously.
          To replay a full trading strategy instead, use the <Link href="/paper/backtest" className="text-cyanx hover:underline">paper-trading backtester</Link>.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Tile label={`Outcomes ${hl}`} value={sm.n ?? 0} sub={days ? `last ${LOOKBACK.find(([, d]) => d === days)?.[0]}` : "all history"} />
        <Tile label="Continued" value={sm.n ? `${sm.continuation_rate}%` : "—"} cls="text-cyanx" sub="≥ +10% without a 30% dip" />
        <Tile label="Reversal / rug" value={sm.n ? `${sm.reversal_rate}%` : "—"} cls="text-redx" sub="> 45% drawdown or pool collapse" />
        <Tile label="Median return" value={sm.median_fwd_ret == null ? "—" : fmtPct(sm.median_fwd_ret)}
              cls={(sm.median_fwd_ret ?? 0) >= 0 ? "text-greenx" : "text-redx"}
              sub={sm.p25_fwd_ret != null ? `middle half ${fmtPct(sm.p25_fwd_ret)} to ${fmtPct(sm.p75_fwd_ret)}` : undefined} />
        <Tile label="Median max dip" value={sm.median_max_dd == null ? "—" : fmtPct(sm.median_max_dd)} cls="text-amberx"
              sub="worst point inside the horizon" />
      </div>
      {sm.n > 0 && <div className="panel px-4 py-3"><OutcomeMix outcomes={sm.outcomes} n={sm.n} /></div>}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Panel title={<span>Trend Score reliability <span className="panel-sub">{hl}</span></span>}>
          <div className="p-4"><CurveChart data={hz?.trend_curve} /></div>
        </Panel>
        <Panel title={<span>FOMO reliability <span className="panel-sub">does hype continue? {hl}</span></span>}>
          <div className="p-4"><CurveChart data={hz?.fomo_curve} /></div>
        </Panel>
        <Panel title={<span>Risk Score reliability <span className="panel-sub">adverse events by risk {hl}</span></span>}>
          <div className="p-4"><CurveChart data={hz?.risk_curve} /></div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start">
        <Panel title={<span>Signal rule hit-rates <span className="panel-sub">{hl}</span></span>} className="xl:col-span-2"
               right={<div className="flex gap-0.5" role="group" aria-label="Rule type">
                 {(["all", "signal", "risk"] as const).map((k) => (
                   <button key={k} onClick={() => setRuleKind(k)} aria-pressed={ruleKind === k}
                           className={`pill-mini ${ruleKind === k ? "!text-cyanx !border-cyanx/60" : "text-dim border-line"}`}>{k}</button>
                 ))}</div>}>
          <div className="overflow-x-auto">
            <table className="tbl">
              <thead><tr><th>RULE</th><th>N</th><th>HIT RATE</th><th>ADVERSE</th><th>MEDIAN RET</th><th>READ</th></tr></thead>
              <tbody>
                {rules.length === 0 && <tr><td colSpan={6}><Empty text="Rules accumulate as outcomes are labeled." /></td></tr>}
                {rules.map((r: any, i: number) => {
                  const risk = (r.kind || (r.target === "rug" ? "risk" : "signal")) === "risk";
                  return (
                    <tr key={i}>
                      <td className="!text-left font-semibold">{r.rule}{r.n < 10 && <span className="text-2xs text-amberx ml-1">small n</span>}</td>
                      <td className="num">{r.n}</td>
                      <td className={`num font-bold ${risk ? (r.hit_rate_pct >= 30 ? "text-greenx" : "text-amberx")
                        : r.hit_rate_pct >= 50 ? "text-greenx" : r.hit_rate_pct >= 30 ? "text-amberx" : "text-redx"}`}>
                        {r.hit_rate_pct}%
                      </td>
                      <td className="num text-redx/90">{r.adverse_rate_pct}%</td>
                      <td className={`num ${r.median_fwd_ret == null ? "text-dim" : r.median_fwd_ret >= 0 ? "text-greenx" : "text-redx"}`}>
                        {r.median_fwd_ret == null ? "—" : fmtPct(r.median_fwd_ret)}
                      </td>
                      <td className="text-xs text-dim max-w-[240px] whitespace-normal text-left">
                        {risk ? "how often the warning came true (adverse outcome)" : "share of signals that continued vs faded/reversed"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
        <SignalQualityPanel />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start">
        <div className="xl:col-span-2"><VerdictAccuracy days={days} /></div>
        <Panel title="How to read this page">
          <div className="p-4 space-y-2 text-sm text-ink/80 leading-relaxed">
            <p>· <b>Continuation rate</b> — % of snapshots in that score band that kept momentum (fwd return ≥ +10% without a &gt;30% drawdown).</p>
            <p>· <b>Reversal rate</b> — % that suffered a &gt;45% drawdown or liquidity collapse within the horizon.</p>
            <p>· Switch <b>History</b> to see whether a signal still works in the current market, not just on average.</p>
            <p>· A signal <b>earns trust only through measured hit-rates</b>. A band with n&lt;10 is anecdote, not evidence.</p>
            <p>· Retro-scored rows replay the engines over stored (partly simulated) history; live rows replace them as time passes.</p>
            <p className="text-amberx/90">· Past relationships break in new regimes. Small samples lie. This page exists to keep the platform honest (§21), not to promise returns.</p>
          </div>
        </Panel>
      </div>
    </div>
  );
}

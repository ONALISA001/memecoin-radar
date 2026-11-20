"use client";
import Link from "next/link";
import { useState } from "react";
import { apiPost, useApi } from "@/lib/api";
import { Empty, Panel } from "@/components/ui";
import { SimStrip, usd, pnlCls } from "@/components/paper";
import { SupervisorPanel } from "@/components/supervisor";

const pct = (v?: number | null) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`);
const EXIT_LABEL: Record<string, string> = {
  STOP_LOSS: "stop", TAKE_PROFIT: "target", TRAILING_STOP: "trail", BREAKEVEN_STOP: "break-even",
  SELL_SIGNAL: "sell signal", CONFIDENCE_COLLAPSE: "momentum fade", RISK_EXIT: "sell pressure",
  LIQUIDITY_EXIT: "liquidity", MAX_HOLD: "time", MANUAL: "manual",
  MOMENTUM_EXIT: "momentum fade", REGIME_EXIT: "regime flip", TIME_STOP: "time stop", VERDICT_EXIT: "verdict",
};

function Row({ r, best, onApply }: { r: any; best: boolean; onApply?: () => void }) {
  return (
    <tr className={best ? "bg-greenx/5" : ""}>
      <td className="!py-2.5">
        <div className="font-semibold text-sm">{r.name} {best && <span className="badge badge-green !text-2xs ml-1">leading</span>}
          {r.halt?.active && <span className="badge badge-amber !text-2xs ml-1">paused</span>}
          {r.retired && <span className="badge badge-gray !text-2xs ml-1" title="Retired by an approved supervisor proposal">retired</span>}</div>
        <div className="text-xs text-dim max-w-[420px] min-w-[260px] leading-snug whitespace-normal">{r.summary}</div>
      </td>
      <td className={`num ${pnlCls(r.return_pct)}`}>{r.running === false ? "—" : pct(r.return_pct)}</td>
      <td className="num">{r.running === false ? "—" : usd(r.equity, 0)}</td>
      <td className="num">{r.trades ?? "—"}{r.open ? <span className="text-dim"> +{r.open} open</span> : null}
        {r.trades != null && !r.enough_trades && r.key !== "main" && <div className="text-2xs text-dim">too few to judge</div>}</td>
      <td className="num">{r.win_rate_pct != null ? `${r.win_rate_pct}%` : "—"}</td>
      <td className="num">{r.profit_factor ?? "—"}</td>
      <td className="num">
        {r.avg_trade_pct != null ? pct(r.avg_trade_pct) : "—"}
        {r.avg_trade_ci95 && <div className={`text-2xs ${r.edge === "positive" ? "text-greenx" : r.edge === "negative" ? "text-redx" : "text-dim"}`}>
          {pct(r.avg_trade_ci95[0])} to {pct(r.avg_trade_ci95[1])} · {r.edge === "positive" ? "edge" : r.edge === "negative" ? "losing" : "could be luck"}</div>}
      </td>
      <td className="num text-dim">{r.avg_win != null ? usd(r.avg_win, 0) : "—"} / {r.avg_loss != null ? usd(r.avg_loss, 0) : "—"}</td>
      <td className={`num ${(r.max_drawdown_pct ?? 0) > 15 ? "text-amberx" : ""}`}>{r.max_drawdown_pct != null ? `${r.max_drawdown_pct}%` : "—"}</td>
      <td className="text-xs text-dim">
        {r.exits ? Object.entries(r.exits).sort((a: any, b: any) => b[1] - a[1]).slice(0, 3)
          .map(([k, v]: any) => `${EXIT_LABEL[k] || k} ${v}`).join(" · ") || "—" : "—"}
      </td>
      <td>{onApply && <button className="pill-mini" onClick={onApply}>Use for main</button>}</td>
    </tr>
  );
}

const STATUS: Record<string, [string, string]> = {
  proven_edge: ["proven edge", "badge-green"], promising: ["promising", "badge-cyan"],
  underperforming: ["flat", "badge-gray"], losing: ["losing", "badge-red"], too_early: ["too early", "badge-gray"],
};

/** AI verdict on the lab: ranks the strategy accounts and compares how they trade. */
function LabVerdict({ k }: { k: number }) {
  const { data: rv, loading } = useApi(`/paper/lab/review?k=${k}`, 120000);
  if (loading && !rv) return <Panel title="AI verdict on the strategies"><Empty text="Reviewing strategies…" /></Panel>;
  if (!rv?.available) return null;
  return (
    <Panel title={<span>🤖 AI verdict on the strategies <span className="panel-sub">— {rv.source === "rules+llm" ? "AI write-up from the lab stats" : "rule-based review of the lab stats"}</span></span>}>
      <div className="p-4 space-y-3">
        <div className="text-base font-semibold">{rv.verdict}</div>
        {rv.ai_text && <p className="text-sm leading-relaxed text-ink/85 border-l-2 border-cyanx/60 pl-3">{rv.ai_text}</p>}
        <ul className="space-y-1">
          {(rv.insights || []).map((t: string, i: number) => <li key={i} className="text-sm text-ink/80">• {t}</li>)}
        </ul>
        {rv.head_to_head && (
          <div className="rounded-lg border border-line/60 px-3 py-2 text-xs flex flex-wrap gap-x-4 gap-y-1">
            <span className="text-dim">AI Verdict Follower vs Baseline</span>
            <span className={`num font-semibold ${pnlCls(rv.head_to_head.return_diff_pp)}`}>{pct(rv.head_to_head.return_diff_pp)} return</span>
            {rv.head_to_head.win_rate_diff_pp != null && <span className="num">{pct(rv.head_to_head.win_rate_diff_pp)} win rate</span>}
            <span className="text-dim">{rv.head_to_head.read}</span>
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          {(rv.strategies || []).map((s: any) => (
            <span key={s.key} className={`badge ${(STATUS[s.status] || STATUS.too_early)[1]} !text-2xs`} title={s.why}>
              #{s.rank} {s.name} · {(STATUS[s.status] || STATUS.too_early)[0]}
            </span>
          ))}
        </div>
        <div className="text-2xs text-dim">{rv.disclaimer}{rv.ai_error ? ` (AI write-up unavailable: ${rv.ai_error})` : ""}</div>
      </div>
    </Panel>
  );
}

export default function StrategyLabPage() {
  const [k, setK] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);
  const { data, loading } = useApi(`/paper/lab?k=${k}`, 30000);
  const act = async (path: string, done: string) => {
    try { await apiPost(path); setMsg(done); setK(k + 1); } catch (e: any) { setMsg(`Failed: ${e?.message || e}`); }
  };
  if (loading && !data) return <Empty text="Loading Strategy Lab…" />;
  const rows: any[] = data?.presets || [];
  const ranked = rows.filter((r) => r.running && r.proven_edge).sort((a, b) => b.return_pct - a.return_pct);
  const leader = ranked[0]?.key;
  const notRunning = rows.filter((r) => !r.running && !r.retired);

  return (
    <div className="space-y-3">
      <SimStrip />
      <Panel title={<span>Strategy Lab <span className="panel-sub">— each strategy trades its own $10k virtual account on the same live market</span></span>}
             right={<div className="flex gap-1">
               {!data?.running && <button className="btn !py-1" onClick={() => act("/paper/lab/start", "Lab started — strategies trade from the next signal.")}>▶ Start lab</button>}
               {data?.running && notRunning.length > 0 && <button className="btn !py-1" onClick={() => act("/paper/lab/start", `Started ${notRunning.length} new strateg${notRunning.length > 1 ? "ies" : "y"} — they trade from the next signal.`)}>▶ Start {notRunning.length} new</button>}
               {data?.running && <button className="pill-mini" onClick={() => { if (confirm("Reset every lab account to $10k and clear its history?")) act("/paper/lab/reset", "Lab accounts reset."); }}>Reset lab</button>}
               <Link href="/paper" className="pill-mini">← Paper</Link>
             </div>}>
        {msg && <div className="px-4 py-2 text-sm text-cyanx border-b border-line/50">{msg}</div>}
        {!data?.running && (
          <div className="px-4 py-3 text-sm text-dim">
            Start the lab to run all {rows.length} strategies side by side. Your main account isn&apos;t changed. Compare after
            at least {data?.min_trades_for_verdict ?? 30} closed trades each — fewer is mostly luck.
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="tbl w-full">
            <thead><tr>
              <th>Strategy</th><th>Return</th><th>Equity</th><th>Trades</th><th>Win rate</th>
              <th>Profit factor</th><th>Avg trade (95% range)</th><th>Avg win / loss</th><th>Max DD</th><th>Exits</th><th></th>
            </tr></thead>
            <tbody>
              {(["core", "momentum", "challenger"] as const).map((fam) => {
                const group = rows.filter((r) => (r.family || "core") === fam);
                if (!group.length) return null;
                return [
                  fam === "momentum" && (
                    <tr key="hdr-momentum"><td colSpan={11} className="!text-left !py-2 bg-cyanx/5">
                      <span className="text-xs font-bold text-cyanx">⚡ Momentum family (A–F)</span>
                      <span className="text-2xs text-dim ml-2">built on strong momentum, volume and bullish regime ·</span>
                      <Link href="/paper/backtest" className="text-2xs text-cyanx hover:underline ml-1">compare them on past data →</Link>
                    </td></tr>),
                  fam === "challenger" && (
                    <tr key="hdr-challenger"><td colSpan={11} className="!text-left !py-2 bg-violetx/5">
                      <span className="text-xs font-bold text-violetx">🧪 Challengers</span>
                      <span className="text-2xs text-dim ml-2">copies of a strategy with one fix learned from its losing trades — started by your approval</span>
                    </td></tr>),
                  ...group.map((r) => (
                    <Row key={r.key} r={r} best={r.key === leader}
                         onApply={r.running ? () => { if (confirm(`Use "${r.name}" rules on your main account? Its history is kept.`)) act(`/paper/lab/apply/${r.key}`, `Main account now uses "${r.name}".`); } : undefined} />
                  )),
                ];
              })}
              {data?.main && <Row r={data.main} best={false} />}
            </tbody>
          </table>
        </div>
        <div className="px-4 py-2 text-xs text-dim border-t border-line/50">
          Simulation with realistic memecoin costs (~3.6% per round trip: fees, slippage, spread) — not financial advice.
          A strategy is only &quot;leading&quot; once it has {data?.min_trades_for_verdict ?? 30}+ closed trades AND the 95% range of its average trade is above 0.
          Profit factor = gross wins ÷ gross losses (above 1 = profitable).
        </div>
      </Panel>
      {data?.running && <SupervisorPanel onChanged={() => setK(k + 1)} />}
      {data?.running && <LabVerdict k={k} />}
    </div>
  );
}

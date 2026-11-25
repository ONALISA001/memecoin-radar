"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { runJob, useRole } from "@/lib/api";
import { Panel } from "@/components/ui";
import { EquityCurve, fmtUtc, holdStr, pnlCls, SimStrip, StatTile, usd } from "@/components/paper";

const PARAM_FIELDS: [string, string, string][] = [
  ["min_confidence", "Min AI confidence", "60"],
  ["stop_loss_pct", "Stop-loss %", "12"],
  ["take_profit_pct", "Take-profit %", "35"],
  ["trailing_stop_pct", "Trailing stop % (0=off)", "0"],
  ["max_position_usd", "Max position $", "500"],
  ["max_position_pct", "Max position % equity", "5"],
];

type BTResult = any;

const EXIT_SHORT: Record<string, string> = {
  STOP_LOSS: "stop", TAKE_PROFIT: "target", TRAILING_STOP: "trail", BREAKEVEN_STOP: "break-even",
  SELL_SIGNAL: "sell signal", MOMENTUM_EXIT: "momentum fade", REGIME_EXIT: "regime flip", TIME_STOP: "time stop",
  VERDICT_EXIT: "verdict", MAX_HOLD: "max hold", NO_PRICE_EXIT: "no price", END_OF_WINDOW: "window end",
};
const pctS = (v?: number | null) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`);

/** Baseline + strategies A–F replayed over the same window, side by side. */
function FamilyCompare() {
  const [hours, setHours] = useState("168");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [r, setR] = useState<any>(null);
  const [elapsed, setElapsed] = useState(0);
  const owner = useRole() === "owner";
  const run = async () => {
    setBusy(true); setErr(""); setElapsed(0);
    try { setR(await runJob("/paper/backtest/compare", { hours: parseInt(hours) || 168 }, (s) => setElapsed(s))); }
    catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  };
  const rows: any[] = r?.strategies || [];
  const best = (f: string, hi = true) => {
    const ok = rows.filter((x) => (x.trades || 0) >= 1 && x[f] != null);
    if (!ok.length) return null;
    return ok.reduce((a, b) => ((hi ? b[f] > a[f] : b[f] < a[f]) ? b : a)).key;
  };
  const bWin = best("win_rate_pct"), bRet = best("total_return_pct"), bPf = best("profit_factor"), bDd = best("max_drawdown_pct", false);
  const hl = (k: string, b: string | null) => (k === b ? "text-greenx font-bold" : "");
  return (
    <Panel title={<span>⚖️ Momentum strategies, side by side <span className="panel-sub">— Baseline + A–F replayed over the same history and costs</span></span>}
           right={<div className="flex items-center gap-1">
             <select className="sel !w-auto !py-1 !text-xs" aria-label="History window" value={hours} onChange={(e) => setHours(e.target.value)}>
               {[["24", "last 24h"], ["72", "last 3 days"], ["168", "last 7 days"], ["720", "last 30 days"]].map(([v, l]) =>
                 <option key={v} value={v}>{l}</option>)}
             </select>
             <button onClick={run} disabled={busy || !owner} title={owner ? undefined : "Log in as owner to run"}
                     className="pill-mini !text-cyanx !border-cyanx/60 !py-1.5 disabled:opacity-40">
               {busy ? `running… ${Math.round(elapsed)}s` : "▶ Compare all"}</button>
           </div>}>
      {err && <div className="px-3 py-2 text-xs text-redx">{err}</div>}
      {!r && !err && <div className="px-4 py-3 text-xs text-dim">Runs every strategy over the chosen window with identical data, then ranks them.
        {owner ? " Long windows take a little while; it runs in the background." : <span className="text-amberx"> Log in as owner (sidebar) to run it.</span>}</div>}
      {r && (
        <div>
          <div className="px-4 py-2 text-sm border-b border-line/50">
            {r.leader ? <>Leader: <b className="text-greenx">{rows.find((x) => x.key === r.leader)?.name}</b> (best profit factor with 10+ trades)</>
              : <span className="text-amberx">No strategy has 10+ trades in this window yet — treat every number as noise. Try a longer window.</span>}
            <span className="text-dim text-xs"> · {r.signals_seen} signals in window</span>
          </div>
          <div className="overflow-x-auto">
            <table className="tbl w-full">
              <thead><tr><th className="!text-left">Strategy</th><th>Trades</th><th>Win rate</th><th>Return</th><th>vs base</th>
                <th>Profit factor</th><th>Avg trade (95%)</th><th>Max DD</th><th className="!text-left">Exits</th><th className="!text-left">Main filter</th></tr></thead>
              <tbody>
                {rows.map((x) => (
                  <tr key={x.key} className={x.key === r.leader ? "bg-greenx/5" : ""}>
                    <td className="!text-left !py-2"><div className="font-semibold text-sm whitespace-nowrap">{x.name}</div></td>
                    <td className="num">{x.trades}{(x.trades || 0) < 10 && <div className="text-2xs text-dim">few</div>}</td>
                    <td className={`num ${hl(x.key, bWin)}`}>{x.win_rate_pct != null ? `${x.win_rate_pct}%` : "—"}</td>
                    <td className={`num ${pnlCls(x.total_return_pct)} ${hl(x.key, bRet)}`}>{pctS(x.total_return_pct)}</td>
                    <td className={`num ${pnlCls(x.vs_baseline_pp)}`}>{x.vs_baseline_pp == null ? "—" : `${x.vs_baseline_pp >= 0 ? "+" : ""}${x.vs_baseline_pp} pp`}</td>
                    <td className={`num ${hl(x.key, bPf)}`}>{x.profit_factor ?? "—"}</td>
                    <td className="num">{pctS(x.avg_trade_pct)}
                      {x.avg_trade_ci95 && <div className={`text-2xs ${x.edge === "positive" ? "text-greenx" : x.edge === "negative" ? "text-redx" : "text-dim"}`}>
                        {pctS(x.avg_trade_ci95[0])} to {pctS(x.avg_trade_ci95[1])}</div>}</td>
                    <td className={`num ${hl(x.key, bDd)}`}>{x.max_drawdown_pct != null ? `${x.max_drawdown_pct}%` : "—"}</td>
                    <td className="!text-left text-2xs text-dim whitespace-normal min-w-[140px]">
                      {Object.entries(x.exits || {}).sort((a: any, b: any) => b[1] - a[1]).slice(0, 3)
                        .map(([k, v]: any) => `${EXIT_SHORT[k] || k} ${v}`).join(" · ") || "—"}</td>
                    <td className="!text-left text-2xs text-dim whitespace-normal min-w-[140px]">
                      {x.own_top_rejection ? <span className="text-amberx">{x.own_top_rejection[0]} ×{x.own_top_rejection[1]}</span>
                        : (x.top_rejections || [])[0] ? `${x.top_rejections[0][0]} ×${x.top_rejections[0][1]}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-4 py-2 text-2xs text-dim border-t border-line/50">{r.note} Green = best in column.</div>
        </div>
      )}
    </Panel>
  );
}

function ResultView({ r }: { r: BTResult }) {
  const s = r.summary;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
        <StatTile label="Ending Capital" value={usd(s.ending_equity)} cls={pnlCls(s.ending_equity - s.starting_capital)} />
        <StatTile label="Total Return" value={`${s.total_return_pct > 0 ? "+" : ""}${s.total_return_pct}%`} cls={pnlCls(s.total_return_pct)} />
        <StatTile label="Trades" value={s.trades} sub={`${s.signals_seen} signals seen`} />
        <StatTile label="Win Rate" value={s.win_rate_pct != null ? `${s.win_rate_pct}%` : "—"} sub={`${s.wins}W/${s.losses}L`} />
        <StatTile label="Profit Factor" value={s.profit_factor ?? "—"} />
        <StatTile label="Max Drawdown" value={`${s.max_drawdown_pct}%`} cls="text-amberx" />
      </div>
      <Panel title="Backtest Equity Curve"><div className="p-3"><EquityCurve points={r.equity_curve || []} base={s.starting_capital} /></div></Panel>
      <Panel title="Largest Trades">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-2xs text-dim uppercase tracking-wider border-b border-line/60">
              <th className="text-left py-1.5 pl-3">Token</th><th className="text-left">Signal</th>
              <th className="text-right">Conf</th><th className="text-right">Entry</th><th className="text-right">Exit</th>
              <th className="text-left pl-2">Reason</th><th className="text-right">P&L</th><th className="text-right pr-3">Hold</th>
            </tr></thead>
            <tbody>
              {(r.trades || []).slice(0, 15).map((t: any, i: number) => (
                <tr key={i} className="border-b border-line/30 last:border-0">
                  <td className="py-1.5 pl-3 font-bold text-cyanx">${t.symbol}</td>
                  <td className="text-dim text-2xs">{t.kind?.replace(/_/g, " ")}</td>
                  <td className="num text-right">{Math.round(t.confidence ?? 0)}%</td>
                  <td className="num text-right text-2xs">{fmtUtc(t.entry_ts).slice(5, -4)}</td>
                  <td className="num text-right text-2xs">{t.exit_ts ? fmtUtc(t.exit_ts).slice(5, -4) : "—"}</td>
                  <td className="pl-2 text-dim">{t.exit_reason?.replace(/_/g, " ")}</td>
                  <td className={`num text-right font-bold ${pnlCls(t.pnl)}`}>{t.pnl != null ? `${t.pnl >= 0 ? "+" : ""}${usd(t.pnl)} (${t.pnl_pct}%)` : "—"}</td>
                  <td className="num text-right pr-3">{holdStr(t.hold_secs)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <div className="text-2xs text-dim px-1 leading-relaxed">{s.note}</div>
    </div>
  );
}

/* §21 replay: step through backtest frames with play/pause/speed */
function ReplayPlayer({ r }: { r: BTResult }) {
  const frames: any[] = r.frames || [];
  const times: number[] = r.frame_times || [];
  const [idx, setIdx] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const timer = useRef<any>(null);

  useEffect(() => {
    if (!playing) return;
    timer.current = setInterval(() => setIdx((i) => Math.min(i + 1, times.length - 1)), 900 / speed);
    return () => clearInterval(timer.current);
  }, [playing, speed, times.length]);
  useEffect(() => { if (idx >= times.length - 1 && times.length) setPlaying(false); }, [idx, times.length]);
  if (!times.length) return null;

  const simTs = times[Math.min(idx, times.length - 1)];
  const visible = frames.filter((f) => f.ts <= simTs);
  const open: Record<string, any> = {};
  let cash = (r.summary?.starting_capital ?? 10000);
  for (const f of visible) {
    if (f.type === "BUY") { open[f.token] = f; cash -= f.size; }
    else if (f.type === "SELL") { delete open[f.token]; cash = f.cash ?? cash; }
  }
  const equityAt = (r.equity_curve || []).filter((e: any) => e.ts <= simTs).pop();
  return (
    <Panel title={<span>⏯ Historical Replay <span className="panel-sub">— watch the strategy operate (§21)</span></span>}
           right={
             <div className="flex gap-1 items-center">
               <button className="pill-mini" onClick={() => setPlaying(!playing)}>{playing ? "⏸ pause" : "▶ play"}</button>
               {[1, 2, 5, 10].map((s) => (
                 <button key={s} className={`pill-mini ${speed === s ? "!text-cyanx !border-cyanx/60" : ""}`}
                         onClick={() => setSpeed(s)}>{s}x</button>
               ))}
               <button className="pill-mini" onClick={() => { setIdx(0); setPlaying(false); }}>↻</button>
             </div>
           }>
      <div className="p-3 space-y-2">
        <div className="flex items-center gap-3 text-xs">
          <span className="num font-bold text-cyanx">{fmtUtc(simTs)}</span>
          <span className="text-dim">equity <b className={`num ${pnlCls((equityAt?.equity ?? 10000) - 10000)}`}>{usd(equityAt?.equity ?? 10000)}</b></span>
          <span className="text-dim">cash <b className="num">{usd(cash)}</b></span>
          <span className="ml-auto text-dim">{Math.round(100 * idx / Math.max(times.length - 1, 1))}%</span>
        </div>
        <input type="range" min={0} max={times.length - 1} value={idx}
               onChange={(e) => setIdx(Number(e.target.value))} className="w-full accent-[#22D3EE]" />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <div>
            <div className="text-2xs font-bold tracking-wider text-dim mb-1">OPEN POSITIONS NOW</div>
            <div className="space-y-1 min-h-[40px]">
              {Object.values(open).length === 0 && <div className="text-xs text-dim">none at this moment</div>}
              {Object.values(open).map((f: any) => (
                <div key={f.token} className="text-xs flex justify-between border border-line/40 rounded px-2 py-1">
                  <span className="font-bold text-greenx">${f.token}</span>
                  <span className="text-dim">{f.kind?.replace(/_/g, " ")} · conf {Math.round(f.conf)}</span>
                  <span className="num">{usd(f.size)}</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="text-2xs font-bold tracking-wider text-dim mb-1">RECENT EVENTS</div>
            <div className="space-y-1 max-h-36 overflow-y-auto">
              {visible.slice(-8).reverse().map((f, i) => (
                <div key={i} className="text-xs flex gap-2 items-baseline">
                  <span className="num text-dim text-2xs w-[92px] shrink-0">{fmtUtc(f.ts).slice(5, -4)}</span>
                  <span className={f.type === "BUY" ? "text-greenx font-bold" : "text-redx font-bold"}>{f.type}</span>
                  <span>${f.token}</span>
                  <span className="text-dim truncate">{f.type === "SELL" ? `${f.reason?.replace(/_/g, " ")} · P&L ${f.pnl >= 0 ? "+" : ""}${f.pnl}` : f.kind?.replace(/_/g, " ")}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
}

export default function BacktestPage() {
  const [hours, setHours] = useState("168");
  const [capital, setCapital] = useState("10000");
  const [params, setParams] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<"backtest" | "replay">("backtest");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState<BTResult | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const owner = useRole() === "owner";

  const run = async () => {
    setBusy(true); setErr(""); setElapsed(0);
    try {
      const strat: Record<string, number> = {};
      for (const [k, v] of Object.entries(params)) {
        const n = parseFloat(v);
        if (!isNaN(n)) strat[k] = n;
      }
      const r = await runJob("/paper/backtest", {
        hours: parseInt(hours) || 168, capital: parseFloat(capital) || 10000,
        strategy: strat, mode,
      }, (s) => setElapsed(s));
      setResult(r);
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-3">
      <SimStrip compact />
      <FamilyCompare />
      <Panel title={<span>▶ Strategy Backtester <span className="panel-sub">— runs the same strategy core over recorded signal & price history only (no look-ahead, §20)</span></span>}>
        <div className="p-3 grid grid-cols-2 md:grid-cols-4 gap-2">
          <div>
            <label className="text-2xs font-bold tracking-wider text-dim">HISTORY WINDOW</label>
            <select className="sel w-full mt-1" value={hours} onChange={(e) => setHours(e.target.value)}>
              {[["24", "last 24h"], ["72", "last 3 days"], ["168", "last 7 days"], ["720", "last 30 days"]].map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-2xs font-bold tracking-wider text-dim">INITIAL CAPITAL</label>
            <input className="inp w-full mt-1" value={capital} onChange={(e) => setCapital(e.target.value)} />
          </div>
          <div>
            <label className="text-2xs font-bold tracking-wider text-dim">MODE</label>
            <select className="sel w-full mt-1" value={mode} onChange={(e) => setMode(e.target.value as any)}>
              <option value="backtest">Backtest</option>
              <option value="replay">Backtest + Replay frames</option>
            </select>
          </div>
          <div className="flex items-end">
            <button onClick={run} disabled={busy || !owner} title={owner ? undefined : "Log in as owner to run"}
                    className="pill-mini !text-cyanx !border-cyanx/60 w-full !py-2 disabled:opacity-40">
              {busy ? `running… ${Math.round(elapsed)}s` : "▶ Run"}
            </button>
          </div>
        </div>
        <div className="px-3 pb-3 grid grid-cols-2 md:grid-cols-6 gap-2">
          {PARAM_FIELDS.map(([k, l, dflt]) => (
            <div key={k}>
              <label className="text-2xs text-dim">{l}</label>
              <input className="inp w-full mt-0.5" placeholder={dflt}
                     value={params[k] ?? ""} onChange={(e) => setParams({ ...params, [k]: e.target.value })} />
            </div>
          ))}
        </div>
        {err && <div className="px-3 pb-2 text-xs text-redx">{err}</div>}
        {!owner && <div className="px-3 pb-2 text-xs text-amberx">Read-only: log in as owner (sidebar) to run backtests.</div>}
        <div className="px-3 pb-3 text-2xs text-dim">
          Note: backtesting is bounded by recorded history — the intel event feed exists since the v3 engine shipped;
          window data before that is marked as data gaps, never filled with fabricated signals (§34).
        </div>
      </Panel>
      {result?.mode === "replay" || result?.frames ? <ReplayPlayer r={result} /> : null}
      {result && <ResultView r={result} />}
    </div>
  );
}

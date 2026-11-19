"use client";
import Link from "next/link";
import { useState } from "react";
import { apiPost, useApi } from "@/lib/api";
import { Empty, Panel } from "@/components/ui";
import { SimStrip, usd, pnlCls } from "@/components/paper";

const pct = (v?: number | null) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`);
const EXIT_LABEL: Record<string, string> = {
  STOP_LOSS: "stop", TAKE_PROFIT: "target", TRAILING_STOP: "trail", BREAKEVEN_STOP: "break-even",
  SELL_SIGNAL: "sell signal", CONFIDENCE_COLLAPSE: "momentum fade", RISK_EXIT: "sell pressure",
  LIQUIDITY_EXIT: "liquidity", MAX_HOLD: "time", MANUAL: "manual",
};

function Row({ r, best, onApply }: { r: any; best: boolean; onApply?: () => void }) {
  return (
    <tr className={best ? "bg-greenx/5" : ""}>
      <td className="!py-2.5">
        <div className="font-semibold text-[12px]">{r.name} {best && <span className="badge badge-green !text-[8px] ml-1">leading</span>}
          {r.halt?.active && <span className="badge badge-amber !text-[8px] ml-1">paused</span>}</div>
        <div className="text-[10px] text-dim max-w-[420px] leading-snug">{r.summary}</div>
      </td>
      <td className={`num ${pnlCls(r.return_pct)}`}>{r.running === false ? "—" : pct(r.return_pct)}</td>
      <td className="num">{r.running === false ? "—" : usd(r.equity, 0)}</td>
      <td className="num">{r.trades ?? "—"}{r.open ? <span className="text-dim"> +{r.open} open</span> : null}
        {r.trades != null && !r.enough_trades && r.key !== "main" && <div className="text-[9px] text-dim">too few to judge</div>}</td>
      <td className="num">{r.win_rate_pct != null ? `${r.win_rate_pct}%` : "—"}</td>
      <td className="num">{r.profit_factor ?? "—"}</td>
      <td className="num text-dim">{r.avg_win != null ? usd(r.avg_win, 0) : "—"} / {r.avg_loss != null ? usd(r.avg_loss, 0) : "—"}</td>
      <td className={`num ${(r.max_drawdown_pct ?? 0) > 15 ? "text-amberx" : ""}`}>{r.max_drawdown_pct != null ? `${r.max_drawdown_pct}%` : "—"}</td>
      <td className="text-[10px] text-dim">
        {r.exits ? Object.entries(r.exits).sort((a: any, b: any) => b[1] - a[1]).slice(0, 3)
          .map(([k, v]: any) => `${EXIT_LABEL[k] || k} ${v}`).join(" · ") || "—" : "—"}
      </td>
      <td>{onApply && <button className="pill-mini" onClick={onApply}>Use for main</button>}</td>
    </tr>
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
  const ranked = rows.filter((r) => r.running && r.enough_trades).sort((a, b) => b.return_pct - a.return_pct);
  const leader = ranked[0]?.key;

  return (
    <div className="space-y-3">
      <SimStrip />
      <Panel title={<span>🧪 Strategy Lab <span className="text-dim font-normal text-[10px]">— each strategy trades its own $10k virtual account on the same live market</span></span>}
             right={<div className="flex gap-1">
               {!data?.running && <button className="btn !py-1" onClick={() => act("/paper/lab/start", "Lab started — strategies trade from the next signal.")}>▶ Start lab</button>}
               {data?.running && <button className="pill-mini" onClick={() => { if (confirm("Reset every lab account to $10k and clear its history?")) act("/paper/lab/reset", "Lab accounts reset."); }}>Reset lab</button>}
               <Link href="/paper" className="pill-mini">← Paper</Link>
             </div>}>
        {msg && <div className="px-4 py-2 text-[11px] text-cyanx border-b border-line/50">{msg}</div>}
        {!data?.running && (
          <div className="px-4 py-3 text-[11.5px] text-dim">
            Start the lab to run all four strategies side by side. Your main account isn&apos;t changed. Compare after
            at least {data?.min_trades_for_verdict ?? 30} closed trades each — fewer is mostly luck.
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="tbl w-full">
            <thead><tr>
              <th>Strategy</th><th>Return</th><th>Equity</th><th>Trades</th><th>Win rate</th>
              <th>Profit factor</th><th>Avg win / loss</th><th>Max DD</th><th>Exits</th><th></th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <Row key={r.key} r={r} best={r.key === leader}
                     onApply={r.running ? () => { if (confirm(`Use "${r.name}" rules on your main account? Its history is kept.`)) act(`/paper/lab/apply/${r.key}`, `Main account now uses "${r.name}".`); } : undefined} />
              ))}
              {data?.main && <Row r={data.main} best={false} />}
            </tbody>
          </table>
        </div>
        <div className="px-4 py-2 text-[10px] text-dim border-t border-line/50">
          Simulation with modeled fees and slippage — not financial advice. A strategy is only &quot;leading&quot; once it has {data?.min_trades_for_verdict ?? 30}+ closed trades.
          Profit factor = gross wins ÷ gross losses (above 1 = profitable).
        </div>
      </Panel>
    </div>
  );
}

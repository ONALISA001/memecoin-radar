"use client";
import { useApi } from "@/lib/api";
import { Panel } from "@/components/ui";
import { Bar, fmtUtc, holdStr, pnlCls, SimStrip, StatTile, usd } from "@/components/paper";

function DailyChart({ daily }: { daily: { day: string; pnl: number }[] }) {
  if (!daily?.length) return <div className="p-4 text-dim text-xs">No daily data yet.</div>;
  const max = Math.max(...daily.map((d) => Math.abs(d.pnl)), 1);
  return (
    <div className="flex items-end gap-1 h-24 px-3 pb-3 pt-2">
      {daily.slice(-30).map((d) => (
        <div key={d.day} className="flex-1 min-w-[4px] rounded-sm" title={`${d.day}: ${d.pnl >= 0 ? "+" : ""}$${d.pnl}`}
             style={{ height: `${Math.abs(d.pnl) / max * 100}%`, background: d.pnl >= 0 ? "#34D399" : "#F87171", minHeight: 2 }} />
      ))}
    </div>
  );
}

export default function AnalyticsPage() {
  const { data: a } = useApi("/paper/analytics", 30000);
  const { data: cal } = useApi("/paper/analytics/calendar", 30000);
  const { data: eq } = useApi("/paper/equity?hours=168", 30000);
  const { data: sigStats } = useApi("/paper/signals/stats", 30000);

  return (
    <div className="space-y-3">
      <SimStrip compact />
      {a?.empty && (
        <Panel title="Strategy Analytics"><div className="p-6 text-dim text-xs">{a.note}</div></Panel>
      )}
      {!a?.empty && a && (
        <>
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
            <StatTile label="Trades" value={a.trades} sub={`${a.wins}W / ${a.losses}L`} />
            <StatTile label="Win Rate" value={`${a.win_rate_pct}%`} cls={a.win_rate_pct >= 50 ? "text-greenx" : "text-amberx"} />
            <StatTile label="Profit Factor" value={a.profit_factor != null ? a.profit_factor : "—"}
                      cls={(a.profit_factor ?? 0) >= 1 ? "text-greenx" : "text-redx"} />
            <StatTile label="Total P&L" value={usd(a.total_pnl)} cls={pnlCls(a.total_pnl)} />
            <StatTile label="Avg / Median" value={`${usd(a.median_pnl)}`} sub={`avg ${usd(a.avg_trade ?? (a.total_pnl / Math.max(a.trades, 1)))}`} />
            <StatTile label="Fees + Slip" value={usd(-(a.total_fees + a.est_slippage_cost))} sub={`fees ${usd(a.total_fees)} · slip ~${usd(a.est_slippage_cost)}`} />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            <Panel title="📅 Daily Realized P&L">
              <DailyChart daily={cal?.daily || []} />
              <div className="px-3 pb-2 flex gap-4 text-[10px] text-dim">
                <span>today <b className="num">{usd(cal?.today)}</b></span>
                <span>7d <b className="num">{usd(cal?.weekly)}</b></span>
                <span>30d <b className="num">{usd(cal?.monthly)}</b></span>
              </div>
            </Panel>
            <Panel title="📉 Trade Stats">
              <div className="p-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[10.5px]">
                {[
                  ["avg win", usd(a.avg_win)], ["avg loss", usd(a.avg_loss)],
                  ["largest win", usd(a.largest_win)], ["largest loss", usd(a.largest_loss)],
                  ["avg return/trade", `${a.avg_pnl_pct}%`],
                  ["avg hold", holdStr(a.avg_hold_secs)], ["median hold", holdStr(a.median_hold_secs)],
                  ["net after costs", usd(a.net_pnl_after_costs)],
                ].map(([l, v]: any) => (
                  <div key={l} className="flex justify-between border-b border-line/30 py-1">
                    <span className="text-dim">{l}</span><span className="num font-bold">{v}</span>
                  </div>
                ))}
              </div>
              {a.exit_reasons && (
                <div className="px-3 pb-3">
                  <div className="text-[9px] font-bold tracking-wider text-dim mb-1">EXIT REASONS</div>
                  <div className="flex gap-1 flex-wrap">
                    {Object.entries(a.exit_reasons).map(([r, n]: any) => (
                      <span key={r} className="pill-mini" title={`${n} exits`}>{r.replace(/_/g, " ").toLowerCase()} ×{n}</span>
                    ))}
                  </div>
                </div>
              )}
            </Panel>
          </div>

          {/* §17 confidence analysis */}
          <Panel title={<span>🎯 Does AI confidence actually matter? <span className="text-dim font-normal text-[10px]">— outcomes grouped by entry confidence (§17)</span></span>}>
            <div className="overflow-x-auto">
              <table className="w-full text-[10.5px]">
                <thead><tr className="text-[8.5px] text-dim uppercase tracking-wider border-b border-line/60">
                  <th className="text-left py-1.5 pl-3">Confidence</th><th className="text-right">Trades</th>
                  <th className="text-right">Win rate</th><th className="text-right">Avg return</th>
                  <th className="text-right">Avg loss</th><th className="text-right">Profit factor</th>
                  <th className="text-right pr-3">Total P&L</th>
                </tr></thead>
                <tbody>
                  {(a.confidence_analysis || []).map((c: any) => (
                    <tr key={c.bucket} className="border-b border-line/30 last:border-0">
                      <td className="py-1.5 pl-3 font-bold">{c.bucket}</td>
                      <td className="num text-right">{c.trades}</td>
                      <td className="num text-right">{c.win_rate != null ? `${c.win_rate}%` : "—"}</td>
                      <td className={`num text-right ${pnlCls(c.avg_return)}`}>{c.avg_return != null ? `${c.avg_return > 0 ? "+" : ""}${c.avg_return}%` : "—"}</td>
                      <td className="num text-right text-redx/90">{c.avg_loss != null ? `${c.avg_loss}%` : "—"}</td>
                      <td className="num text-right">{c.profit_factor ?? "—"}</td>
                      <td className={`num text-right pr-3 font-bold ${pnlCls(c.total_pnl)}`}>{usd(c.total_pnl)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="px-3 py-2 text-[9px] text-dim border-t border-line/40">
              Small samples lie — read this only as observed evidence, never as a promise.
            </div>
          </Panel>

          {/* §18 signal effectiveness */}
          <Panel title="📡 Signal Effectiveness (§18) — which signals actually worked">
            <div className="overflow-x-auto">
              <table className="w-full text-[10.5px]">
                <thead><tr className="text-[8.5px] text-dim uppercase tracking-wider border-b border-line/60">
                  <th className="text-left py-1.5 pl-3">Signal</th><th className="text-right">Trades</th>
                  <th className="text-right">Win rate</th><th className="text-right">Avg return</th>
                  <th className="text-right pr-3">Total P&L</th>
                </tr></thead>
                <tbody>
                  {(a.signal_effectiveness || []).filter((e: any) => e.trades > 0).map((e: any) => (
                    <tr key={e.label} className="border-b border-line/30 last:border-0">
                      <td className="py-1.5 pl-3">{e.label}</td>
                      <td className="num text-right">{e.trades}</td>
                      <td className="num text-right">
                        <div className="flex items-center gap-2 justify-end">
                          <span className="w-20"><Bar pct={e.win_rate ?? 0} color={(e.win_rate ?? 0) >= 50 ? "#34D399" : "#F59E0B"} h={5} /></span>
                          {e.win_rate != null ? `${e.win_rate}%` : "—"}
                        </div>
                      </td>
                      <td className={`num text-right ${pnlCls(e.avg_return)}`}>{e.avg_return != null ? `${e.avg_return > 0 ? "+" : ""}${e.avg_return}%` : "—"}</td>
                      <td className={`num text-right pr-3 font-bold ${pnlCls(e.total_pnl)}`}>{usd(e.total_pnl)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          {/* §25 rejection analytics */}
          <Panel title="🛡 Risk Engine Audit (§25) — AI wanted vs risk allowed">
            <div className="p-3 grid grid-cols-2 md:grid-cols-4 gap-2">
              <StatTile label="Total signals" value={sigStats?.total_signals ?? 0} />
              <StatTile label="Executed" value={sigStats?.executed ?? 0} cls="text-greenx" />
              <StatTile label="Rejected" value={sigStats?.rejected ?? 0} cls="text-amberx" />
              <StatTile label="Rejection rate" value={sigStats?.rejection_rate_pct != null ? `${sigStats.rejection_rate_pct}%` : "—"} />
            </div>
            <div className="px-3 pb-3">
              <div className="text-[9px] font-bold tracking-wider text-dim mb-1">TOP REJECTION REASONS</div>
              <div className="space-y-1">
                {(sigStats?.top_rejection_reasons || []).map(([r, n]: any) => (
                  <div key={r} className="flex justify-between text-[10px] border-b border-line/30 py-0.5">
                    <span className="text-ink/75">{r}</span><span className="num text-dim">×{n}</span>
                  </div>
                ))}
              </div>
            </div>
          </Panel>
        </>
      )}
      <div className="text-[9px] text-dim px-1">
        All analytics are computed from the virtual trade ledger only. Simulated results on live data —
        helpful for evaluating strategy logic, not a forecast of real outcomes.
      </div>
    </div>
  );
}

"use client";
import { use } from "react";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { Badge, Panel } from "@/components/ui";
import { Bar, fmtUtc, holdStr, px, pnlCls, SimStrip, usd } from "@/components/paper";
import { ReviewCard } from "@/components/postmortem";

const METRICS: [string, string, number][] = [
  ["momentum", "⚡ Momentum", 100], ["sell_pressure", "🔻 Sell pressure", 100],
  ["liq_health", "💧 Liquidity health", 100], ["buy_share", "🟢 Buy share %", 100],
  ["holders_g_1h", "👥 Holder growth 1h %", 40], ["volume_g_1h", "📈 Volume growth 1h %", 200],
];

export default function TradeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: d } = useApi(`/paper/trades/${id}`, 20000);
  if (!d) return <div className="p-8 text-dim text-xs">loading trade…</div>;
  const t = d.trade;
  const m = d.signal_metrics || {};
  return (
    <div className="max-w-4xl mx-auto space-y-3">
      <SimStrip />
      <Panel title={<span>Trade Analysis <span className="panel-sub">#{t.id} ${d.symbol}</span></span>}
             right={<Badge kind={t.status === "open" ? "green" : (t.realized_pnl ?? 0) >= 0 ? "violet" : "red"}>
               {t.status === "open" ? "OPEN" : t.exit_reason?.replace(/_/g, " ")}</Badge>}>
        <div className="p-4 grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-2 text-xs">
          {[
            ["Token", `$${d.symbol}`, null], ["Strategy", d.strategy?.name ?? "—", null],
            ["Market regime", t.market_regime, null], ["Trade ID", `#${t.id}`, null],
            ["Entry time", fmtUtc(t.entry_ts), null], ["Exit time", t.exit_ts ? fmtUtc(t.exit_ts) : "—", null],
            ["Signal price", px(t.signal_price), null], ["Execution price", px(t.entry_price), null],
            ["Exit price", t.exit_price ? px(t.exit_price) : "—", null], ["Position size", usd(t.size_usd, 2), null],
            ["AI confidence", `${Math.round(t.ai_confidence ?? 0)}%`, null], ["Risk score", t.risk_score != null ? `${Math.round(t.risk_score)}` : "—", null],
            ["Stop loss", px(t.stop_price), null], ["Take profit", px(t.target_price), null],
            ["Slippage", `${t.slippage_pct}%`, null],
            ["Fees", usd((t.entry_fee || 0) + (t.exit_fee || 0)), null],
            ["Holding time", holdStr(t.hold_secs), null],
            ["P&L", t.realized_pnl != null ? `${t.realized_pnl >= 0 ? "+" : ""}${usd(t.realized_pnl)}` : "open", pnlCls(t.realized_pnl)],
            ["Return", t.pnl_pct != null ? `${t.pnl_pct >= 0 ? "+" : ""}${t.pnl_pct}%` : "—", pnlCls(t.realized_pnl)],
            ["Exit reason", t.exit_reason?.replace(/_/g, " ") ?? "—", null],
          ].map(([l, v, cls]) => (
            <div key={l as string}>
              <div className="text-2xs text-dim uppercase tracking-wider">{l}</div>
              <div className={`num font-bold ${cls || "text-ink/90"}`}>{v}</div>
            </div>
          ))}
        </div>
        {d.address && (
          <div className="px-4 pb-3 text-2xs text-dim">
            contract <span className="num">{d.address}</span> ·{" "}
            <Link href={`/token/${d.address}`} className="text-cyanx hover:underline">token intelligence →</Link>
          </div>
        )}
      </Panel>

      <ReviewCard review={d.review} open={t.status === "open"} />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Panel title="SIGNAL ANALYSIS — metrics at decision">
          <div className="p-4 space-y-2.5">
            {METRICS.map(([k, label, max]) => (
              <div key={k}>
                <div className="flex justify-between text-xs mb-0.5">
                  <span className="text-ink/75">{label}</span>
                  <span className="num text-dim">{m[k] != null ? (typeof m[k] === "number" ? m[k].toFixed(1) : m[k]) : "unavailable"}</span>
                </div>
                {m[k] != null ? <Bar pct={100 * m[k] / max} color={k === "sell_pressure" ? "#F87171" : "#34D399"} /> : <div className="text-2xs text-dim">DATA UNAVAILABLE</div>}
              </div>
            ))}
            <div className="text-2xs text-dim border-t border-line/40 pt-2">
              regime: {t.market_regime} · source signal: {d.signal?.kind?.replace(/_/g, " ")} at {fmtUtc(d.signal?.ts)}
            </div>
          </div>
        </Panel>
        <Panel title="DECISION EXPLANATION — why the AI traded">
          <div className="p-4 space-y-1.5 text-xs">
            {(d.why?.length ?? 0) === 0 && <div className="text-dim text-xs">No structured explanation stored for this trade.</div>}
            {(d.why || []).map((w: string, i: number) => (
              <div key={i} className={`leading-snug ${w.startsWith("✓") ? "text-greenx/90" : "text-amberx/90"}`}>{w}</div>
            ))}
            {t.risk_score != null && (
              <div className="text-amberx/90">⚠ Risk level at entry: {Math.round(t.risk_score)}/100</div>
            )}
            {d.signal?.concern && <div className="text-dim italic border-t border-line/40 pt-1.5">watch: {d.signal.concern}</div>}
            <div className="text-2xs text-dim border-t border-line/40 pt-1.5">
              Explanations summarize observable signals used by the decision — no hidden internal reasoning (§5).
            </div>
          </div>
        </Panel>
      </div>

      <Panel title="Risk engine decision">
        <div className="p-3 grid grid-cols-2 md:grid-cols-3 gap-x-3 gap-y-1">
          {(d.signal?.risk_decision?.checks || []).map((c: any, i: number) => (
            <div key={i} className="text-xs flex gap-1 min-w-0">
              <span className={c.passed ? "text-greenx" : "text-redx"}>{c.passed ? "✓" : "✗"}</span>
              <span className="text-ink/70 truncate">{c.name}</span>
              <span className="text-dim truncate ml-auto">{c.passed ? "" : c.detail}</span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Trade Timeline">
        <div className="divide-y divide-line/40">
          {(d.events || []).map((e: any) => (
            <div key={e.id} className="px-4 py-2 flex gap-3 items-baseline">
              <span className="text-2xs text-dim num shrink-0 w-[140px]">{fmtUtc(e.ts)}</span>
              <div className="min-w-0">
                <div className="text-xs font-bold text-ink/85">{e.title}</div>
                <div className="text-2xs text-dim leading-snug">{e.detail}</div>
              </div>
              <Badge kind={e.kind === "PROFIT" ? "green" : e.kind === "LOSS" || e.kind === "RISK" ? "red" : "gray"}>{e.kind}</Badge>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

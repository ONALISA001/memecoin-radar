"use client";
import { useState } from "react";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { Badge, Panel } from "@/components/ui";
import { fmtUtc, pnlCls, usd } from "@/components/paper";

const pct = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}%`;

const SETTING_LABELS: Record<string, string> = {
  stop_loss_pct: "Stop loss %", breakeven_at_pct: "Move stop to break-even at %",
  max_chase_pct_1h: "Max 1h run before entry %", require_verified_contract: "Require verified contract",
  sell_pressure_exit: "Sell-pressure exit level", confidence_collapse_pts: "Confidence-collapse exit (pts)",
  liquidity_exit_pct: "Liquidity exit (3h %)", max_hold_hours: "Max hold (hours)",
  min_liquidity_usd: "Min liquidity $", take_profit_pct: "Take profit % (0 = let it run)",
  max_top10_wallet_pct: "Max top-10 wallet %", trailing_stop_pct: "Trailing stop %",
  trail_activate_pct: "Trailing stop arms at %",
};
const fmtVal = (v: any) => (typeof v === "boolean" ? (v ? "on" : "off") : String(v));

/** Post-mortem of one trade (trade detail page). */
export function ReviewCard({ review, open }: { review: any; open: boolean }) {
  if (open) return null;
  if (!review) {
    return (
      <Panel title="Post-mortem">
        <div className="p-4 text-dim text-xs">Not reviewed yet — reviews are written within a minute of a trade closing.</div>
      </Panel>
    );
  }
  const r = review;
  const good = r.outcome === "win" || r.cause === "SAVED_BY_EXIT";
  return (
    <Panel title={<span>Post-mortem <span className="panel-sub">— why it {r.outcome === "win" ? "won" : "lost"}</span></span>}
           right={<Badge kind={good ? "green" : "red"}>{r.label}</Badge>}>
      <div className="p-4 space-y-3">
        <div className="text-sm text-ink/90 leading-snug">{r.lesson}</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-2 text-xs">
          {[
            ["Best while open", pct(r.mfe_pct, 1), pnlCls(r.mfe_pct)],
            ["Worst while open", pct(r.mae_pct, 1), pnlCls(r.mae_pct)],
            ["Run before entry (1h)", pct(r.entry_chg_1h), null],
            ["Blame", r.blame, null],
            [`Best after exit (${r.window_h}h)`, pct(r.post_max_pct), pnlCls(r.post_max_pct)],
            ["Worst after exit", pct(r.post_min_pct), pnlCls(r.post_min_pct)],
            ["Hit target after exit", r.reached_target_after ? "yes" : "no", null],
            ["Rugged", r.rugged ? "yes" : "no", r.rugged ? "text-redx" : null],
          ].map(([l, v, cls]) => (
            <div key={l as string}>
              <div className="text-2xs text-dim uppercase tracking-wider">{l}</div>
              <div className={`num font-bold ${cls || "text-ink/90"}`}>{v}</div>
            </div>
          ))}
        </div>
        <div className="text-2xs text-dim border-t border-line/40 pt-2">
          After-exit figures are vs the entry price, from real 5-minute snapshots ({r.post_hours ?? 0}h of data seen).{" "}
          {r.status === "provisional"
            ? `Provisional — final once ${r.window_h}h of post-exit prices are in.`
            : `Final, reviewed ${fmtUtc(r.reviewed_ts)}.`}
        </div>
      </div>
    </Panel>
  );
}

/** "Why we lost" roll-up (analytics page). */
export function WhyWeLost() {
  const [account, setAccount] = useState("main");
  const { data: lab } = useApi("/paper/lab", 60000);
  const { data: s } = useApi(`/paper/postmortems?account=${account}&days=30`, 30000);
  const running = (lab?.presets || []).filter((p: any) => p.running);
  const lossTotal = (s?.loss_causes || []).reduce((a: number, c: any) => a + Math.min(0, c.pnl_usd), 0);

  return (
    <Panel
      title={<span>Why we lost <span className="panel-sub">— one cause per closed trade, last 30 days</span></span>}
      right={running.length > 0 && (
        <select value={account} onChange={(e) => setAccount(e.target.value)}
                className="bg-transparent border border-line/60 rounded text-2xs px-1.5 py-0.5 text-ink/80">
          <option value="main">Main account</option>
          {running.map((p: any) => <option key={p.key} value={p.key}>Lab · {p.name}</option>)}
        </select>
      )}>
      {!s && <div className="p-4 text-dim text-xs">loading…</div>}
      {s && s.reviewed === 0 && (
        <div className="p-4 text-dim text-xs">No closed trades to review yet.</div>
      )}
      {s && s.reviewed > 0 && (
        <div className="p-3 space-y-4">
          <div className="text-xs text-dim">
            {s.reviewed} trades reviewed ({s.wins} won, {s.losses} lost) · {s.final} final, the rest wait for {s.window_h}h of
            after-exit prices.{!s.enough_data && " Small sample: treat patterns as hints until there are 30+ trades."}
          </div>

          <div className="space-y-1.5">
            {(s.loss_causes || []).map((c: any) => (
              <div key={c.cause} className="text-xs">
                <div className="flex justify-between gap-2">
                  <span className={c.cause === "SAVED_BY_EXIT" ? "text-greenx/90" : "text-ink/85"}>
                    {c.label} <span className="text-dim">· {c.blame}</span>
                  </span>
                  <span className="num shrink-0">
                    <span className="text-dim">{c.trades} · {c.share_pct}% of losses · </span>
                    <b className={pnlCls(c.pnl_usd)}>{usd(c.pnl_usd)}</b>
                  </span>
                </div>
                <div className="h-1.5 bg-line/30 rounded mt-0.5">
                  <div className="h-1.5 rounded" style={{
                    width: `${lossTotal < 0 ? Math.min(100, 100 * Math.min(0, c.pnl_usd) / lossTotal) : 0}%`,
                    background: c.cause === "SAVED_BY_EXIT" ? "#34D399" : "#F87171" }} />
                </div>
              </div>
            ))}
            {(s.win_causes || []).map((c: any) => (
              <div key={c.cause} className="flex justify-between text-xs">
                <span className="text-greenx/90">{c.label}</span>
                <span className="num text-dim">{c.trades} · {c.share_pct}% of wins · <b className={pnlCls(c.pnl_usd)}>{usd(c.pnl_usd)}</b></span>
              </div>
            ))}
          </div>

          <div>
            <div className="text-2xs font-bold tracking-wider text-dim mb-1">SUGGESTED CHANGES (OPT-IN)</div>
            {(s.suggestions || []).length === 0 && (
              <div className="text-xs text-dim">No cause repeats often enough yet to justify a change.</div>
            )}
            <div className="space-y-2">
              {(s.suggestions || []).map((g: any, i: number) => (
                <div key={i} className="border border-line/50 rounded p-2 text-xs space-y-0.5">
                  {g.setting && (
                    <div className="font-bold text-ink/90">
                      {SETTING_LABELS[g.setting] || g.setting}: <span className="num">{fmtVal(g.current)}</span> →{" "}
                      <span className="num text-cyanx">{fmtVal(g.suggested)}</span>
                      {g.extra && Object.entries(g.extra).map(([k, v]: any) => (
                        <span key={k} className="text-dim font-normal"> · {SETTING_LABELS[k] || k} <span className="num">{fmtVal(v)}</span></span>
                      ))}
                    </div>
                  )}
                  <div className="text-ink/75">{g.why}</div>
                  <div className="text-dim">Trade-off: {g.tradeoff}</div>
                </div>
              ))}
            </div>
            <div className="text-2xs text-dim mt-1">{s.note}</div>
          </div>

          {(s.by_signal_kind || []).length > 0 && (
            <div>
              <div className="text-2xs font-bold tracking-wider text-dim mb-1">BY ENTRY SIGNAL</div>
              <div className="flex gap-1 flex-wrap">
                {s.by_signal_kind.map((k: any) => (
                  <span key={k.kind} className="pill-mini" title={`${k.trades} trades, ${k.win_rate_pct}% won`}>
                    {k.kind.replace(/_/g, " ")} · {k.trades} · <span className={pnlCls(k.avg_pnl_pct)}>{pct(k.avg_pnl_pct, 1)}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          <div>
            <div className="text-2xs font-bold tracking-wider text-dim mb-1">RECENT REVIEWS</div>
            <div className="divide-y divide-line/30">
              {(s.recent || []).slice(0, 10).map((r: any) => (
                <Link key={r.position_id} href={`/paper/trades/${r.position_id}`}
                      className="flex gap-2 py-1 text-xs hover:bg-line/20 items-baseline">
                  <span className="text-2xs text-dim num w-[120px] shrink-0">{fmtUtc(r.exit_ts)}</span>
                  <span className="font-bold w-[70px] shrink-0 truncate">${r.symbol}</span>
                  <span className={`num w-[52px] shrink-0 text-right ${pnlCls(r.pnl_pct)}`}>{pct(r.pnl_pct, 1)}</span>
                  <span className="text-dim truncate">{r.label}{r.status === "provisional" ? " (provisional)" : ""}</span>
                </Link>
              ))}
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

"use client";
import Link from "next/link";
import { useState } from "react";
import { apiPost, useApi } from "@/lib/api";
import { Panel } from "@/components/ui";
import { EquityCurve, ErrStrip, PositionsTable, SignalsPanel, SimStrip, StatTile,
         TradeTimeline, usd, pnlCls, pnlSign } from "@/components/paper";

function usePaperSummary() { return useApi("/paper/summary", 10000); }

function HaltBanner({ halt }: { halt: { reason: string; until: number; seconds_left: number } }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const why = halt.reason === "loss_streak" ? "after a losing streak" : "after hitting the drawdown limit";
  const mins = Math.max(1, Math.round(halt.seconds_left / 60));
  const left = mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
  const at = new Date(halt.until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (done) return null;
  return (
    <div className="panel !border-amberx/50 px-4 py-3 flex flex-wrap items-center gap-3 text-sm">
      <span className="text-amberx font-semibold">⏸️ New buys paused {why}</span>
      <span className="text-dim">Resumes automatically in {left} (at {at}). Open positions are still managed.</span>
      <div className="flex-1" />
      <button className="btn !py-1" disabled={busy}
              onClick={async () => { setBusy(true); try { await apiPost("/paper/resume"); setDone(true); } finally { setBusy(false); } }}>
        {busy ? "Resuming…" : "▶ Resume now"}
      </button>
    </div>
  );
}

export default function PaperDashboard() {
  const [hours, setHours] = useState(24);
  const [posKey, setPosKey] = useState(0);
  const { data: s, error: sErr } = usePaperSummary();
  const { data: eq } = useApi(`/paper/equity?hours=${hours}`, 20000);
  const { data: positions, error: pErr } = useApi(`/paper/positions?k=${posKey}`, 15000);
  const { data: cal } = useApi("/paper/analytics/calendar", 30000);
  const { data: sigStats } = useApi("/paper/signals/stats", 20000);

  return (
    <div className="space-y-3">
      <SimStrip />
      <ErrStrip error={sErr} label="Account summary" />
      {s?.halt?.active && <HaltBanner halt={s.halt} />}
      {/* TOP (§28): equity, pnl, return, win rate, drawdown, open positions */}
      <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
        <StatTile label="Virtual Equity" value={usd(s?.virtual_equity)} cls={pnlCls(s ? s.total_pnl : null)} />
        <StatTile label="Available Cash" value={usd(s?.available_cash)} sub={`invested ${usd(s?.invested, 0)}`} />
        <StatTile label="Total P&L" value={s ? `${s.total_pnl >= 0 ? "+" : ""}${usd(s.total_pnl)}` : "—"}
                  cls={pnlCls(s?.total_pnl)} sub={s ? `${s.return_pct >= 0 ? "+" : ""}${s.return_pct}%` : ""} />
        <StatTile label="Win Rate" value={s?.win_rate_pct != null ? `${s.win_rate_pct}%` : "—"} sub={`${s?.closed_trades ?? 0} closed`} />
        <StatTile label="Drawdown" value={`${s?.drawdown_pct ?? 0}%`} cls={(s?.drawdown_pct ?? 0) > 10 ? "text-amberx" : "text-ink"} />
        <StatTile label="Open Positions" value={s?.open_positions ?? 0}
                  sub={s?.strategy?.name} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3 items-start">
        {/* CENTER+LEFT: equity curve over open positions */}
        <div className="xl:col-span-2 space-y-3">
          <Panel title={<span>Equity Curve <span className="panel-sub">— mark-to-market every tick</span></span>}
                 right={<div className="flex gap-0.5">{[24, 72, 168].map((h) => (
                   <button key={h} onClick={() => setHours(h)}
                           className={`pill-mini ${hours === h ? "!text-cyanx !border-cyanx/60" : ""}`}>{h}h</button>))}</div>}>
            <div className="p-3">
              <EquityCurve points={eq?.items || []} base={eq?.starting ?? 10000} />
            </div>
          </Panel>
          <Panel title={<span>Open Positions <span className="panel-sub">{positions?.items?.length ?? 0} live</span></span>}
                 right={<Link href="/paper/trades?status=open" className="text-xs text-cyanx hover:underline">all →</Link>}>
            <PositionsTable items={positions?.items || []} onClosed={() => setPosKey(posKey + 1)} />
          </Panel>
          <Panel title="Trade Timeline — exact UTC timestamps">
            <TradeTimeline limit={60} />
          </Panel>
        </div>

        {/* RIGHT: live AI signals + regime + rejected */}
        <div className="space-y-3">
          <Panel title={<span>Live AI Signals <span className="panel-sub">pipeline: signal → risk → execution</span></span>}>
            <SignalsPanel />
          </Panel>
          <Panel title="Risk Engine">
            <div className="p-3 space-y-2 text-xs">
              <div className="flex justify-between"><span className="text-dim">Signals seen</span><span className="num font-bold">{sigStats?.total_signals ?? 0}</span></div>
              <div className="flex justify-between"><span className="text-dim">Executed</span><span className="num font-bold text-greenx">{sigStats?.executed ?? 0}</span></div>
              <div className="flex justify-between"><span className="text-dim">Rejected</span><span className="num font-bold text-amberx">{sigStats?.rejected ?? 0}</span></div>
              <div className="flex justify-between"><span className="text-dim">Rejection rate</span><span className="num">{sigStats?.rejection_rate_pct != null ? `${sigStats.rejection_rate_pct}%` : "—"}</span></div>
              {(sigStats?.top_rejection_reasons || []).slice(0, 3).map(([r, n]: any) => (
                <div key={r} className="text-2xs text-dim leading-snug">
                  <span className="text-amberx">✕</span> {r} <span className="num">×{n}</span>
                </div>
              ))}
              <div className="border-t border-line/40 pt-2 flex justify-between">
                <span className="text-dim">Daily realized</span>
                <span className={`num font-bold ${pnlCls(s?.day_realized)}`}>{s ? pnlSign(s.day_realized) : "—"}</span>
              </div>
              <div className="flex justify-between"><span className="text-dim">Weekly P&L</span><span className="num">{usd(cal?.weekly)}</span></div>
              <div className="flex justify-between"><span className="text-dim">Monthly P&L</span><span className="num">{usd(cal?.monthly)}</span></div>
            </div>
          </Panel>
          <Panel title="Quick Links">
            <div className="p-3 grid grid-cols-2 gap-2 text-xs">
              <Link href="/paper/trades" className="pill-mini !text-center !py-2">📜 Trade History</Link>
              <Link href="/paper/lab" className="pill-mini !text-center !py-2 !text-cyanx">🧪 Strategy Lab</Link>
              <Link href="/paper/analytics" className="pill-mini !text-center !py-2">📊 Strategy Analytics</Link>
              <Link href="/paper/backtest" className="pill-mini !text-center !py-2">▶ Backtester / Replay</Link>
              <Link href="/paper/settings" className="pill-mini !text-center !py-2">⚙ Settings</Link>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

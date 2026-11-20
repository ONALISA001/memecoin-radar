"use client";
import { useEffect, useState } from "react";
import { apiPost, useApi } from "@/lib/api";
import { Panel } from "@/components/ui";
import { SimStrip } from "@/components/paper";

const GROUPS: { title: string; fields: [string, string, string][] }[] = [
  {
    title: "Position Management",
    fields: [
      ["max_position_usd", "Max position size ($)", "hard cap per trade"],
      ["max_position_pct", "Max % of equity per trade", "percentage cap"],
      ["max_open_positions", "Max simultaneous positions", "count"],
      ["max_exposure_pct", "Max portfolio exposure (%)", "of equity"],
      ["min_cash_reserve_pct", "Minimum cash reserve (%)", "kept in cash"],
    ],
  },
  {
    title: "Strategy Filters",
    fields: [
      ["min_confidence", "Minimum AI confidence", "0–100"],
      ["min_liquidity_usd", "Minimum liquidity ($)", "pool size"],
      ["min_volume_24h_usd", "Minimum 24h volume ($)", ""],
      ["min_mcap_usd", "Minimum market cap ($)", ""],
      ["max_risk_score", "Maximum risk score", "0–100"],
      ["min_bullish_signals", "Required bullish signals", "supporting checks"],
    ],
  },
  {
    title: "Memecoin Safety Rules (0 = off)",
    fields: [
      ["min_token_age_h", "Min coin age (h)", "skip launch-day coins"],
      ["max_token_age_h", "Max coin age (h)", "0 = no limit"],
      ["max_top10_wallet_pct", "Max top-10 wallets (%)", "needs Helius data"],
      ["max_vol_liq_ratio", "Max volume / liquidity", "wash-trading guard"],
      ["max_chase_pct_1h", "Don't buy after 1h pump > (%)", "anti-chasing"],
      ["min_buy_share_pct", "Min buy share (%)", "buys / 1h trades"],
    ],
  },
  {
    title: "AI Verdict Rules (0 = off)",
    fields: [
      ["verdict_filter", "AI verdict filter", "0 off · 1 Watch or better · 2 Strong watch only"],
      ["verdict_max_age_h", "Max verdict age (h)", "older verdicts don't count"],
      ["block_bad_verdicts", "Block Avoid / Rug risk", "1 = never buy coins rated Avoid or Rug risk"],
      ["verdict_sizing", "Size by AI conviction", "1 = Strong/high full size … Watch/low 40%"],
      ["verdict_exit", "Exit on AI downgrade", "1 = sell if re-judged Avoid / Rug risk"],
    ],
  },
  {
    title: "Momentum Confluence (0 = off · switches: 1 on / 0 off)",
    fields: [
      ["min_momentum", "Min momentum at entry", "e.g. 65"],
      ["momentum_confirm_readings", "Momentum held for N readings", "5-min readings, e.g. 2"],
      ["momentum_rising", "Momentum must be rising", "1 = latest reading above previous"],
      ["min_volume_accel_pct", "Min 1h volume growth (%)", "e.g. 40"],
      ["require_bullish_regime", "Bullish regime only", "1 = coin regime must be BULLISH"],
      ["block_distribution_regime", "Block distribution regime", "1 = never buy while distributing"],
      ["max_entry_sell_pressure", "Max sell pressure at entry", "e.g. 40"],
      ["min_market_breadth_pct", "Min market breadth (%)", "coins up 24h, e.g. 50"],
      ["confluence_sizing", "Size by confluence", "1 = 4 factors full, 3 → 60%, fewer skip"],
      ["confluence_min_factors", "Min factors to trade", "with sizing on"],
      ["momentum_exit_below", "Exit if momentum below", "e.g. 50"],
      ["regime_exit", "Exit on distribution regime", "1 = on"],
      ["time_stop_hours", "Time stop (h)", "close if not up enough by then"],
      ["time_stop_min_gain_pct", "Time stop: min gain (%)", "e.g. 2"],
      ["reentry_cooldown_h", "Re-entry cooldown (h)", "per coin"],
    ],
  },
  {
    title: "Risk Management",
    fields: [
      ["stop_loss_pct", "Stop-loss (%)", "from entry fill"],
      ["take_profit_pct", "Take-profit (%)", "from entry fill"],
      ["trailing_stop_pct", "Trailing stop (%)", "0 = disabled"],
      ["max_daily_loss_pct", "Max daily loss (%)", "of equity, realized"],
      ["max_drawdown_pct", "Max portfolio drawdown (%)", "pauses until next UTC day"],
      ["max_loss_per_trade_pct", "Max loss per trade (%)", "of equity — shrinks size"],
      ["max_consecutive_losses", "Max consecutive losses", "pauses new buys"],
      ["loss_streak_cooldown_hours", "Loss-streak cool-off (h)", "then auto-resume"],
      ["max_hold_hours", "Max holding period (h)", "time stop"],
      ["breakeven_at_pct", "Break-even stop after (%)", "0 = off"],
      ["trail_activate_pct", "Trailing stop arms after (%)", "0 = from +2%"],
      ["confidence_collapse_pts", "Confidence collapse exit (pts)", "momentum drop vs entry"],
      ["sell_pressure_exit", "Sell-pressure exit threshold", "0–100"],
      ["liquidity_exit_pct", "Liquidity exit (3h %≤)", "e.g. -20"],
    ],
  },
  {
    title: "Execution Simulation",
    fields: [
      ["execution.fee_pct", "Trading fee (%)", "per side"],
      ["execution.base_slippage_pct", "Base slippage (%)", "high-liquidity floor"],
      ["execution.impact_weight", "Impact weight", "× size/pool"],
      ["execution.impact_cap_pct", "Impact cap (%)", ""],
      ["execution.vol_cap_pct", "Volatility slip cap (%)", ""],
      ["execution.min_order_usd", "Minimum order ($)", ""],
    ],
  },
];

function getPath(obj: any, path: string) {
  return path.split(".").reduce((o, k) => o?.[k], obj);
}

export default function PaperSettingsPage() {
  const { data, mutate } = useApi("/paper/settings", 60000) as any;
  const [form, setForm] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [newCapital, setNewCapital] = useState("10000");

  useEffect(() => {
    if (data?.effective && !dirty) {
      const f: Record<string, string> = {};
      for (const g of GROUPS) for (const [k] of g.fields) {
        const v = getPath(data.effective, k);
        if (v != null) f[k] = typeof v === "boolean" ? (v ? "1" : "0") : String(v);
      }
      setForm(f);
    }
  }, [data]);

  const save = async () => {
    setMsg("");
    const overrides: Record<string, any> = {};
    for (const g of GROUPS) for (const [k] of g.fields) {
      const raw = form[k];
      const n = parseFloat(raw);
      if (raw === "" || isNaN(n)) continue;
      const def = getPath(data.defaults, k);
      const val: number | boolean = typeof def === "boolean" ? n !== 0 : n;   // on/off switches
      if (val === def) continue;
      const parts = k.split(".");
      if (parts.length === 1) overrides[k] = val;
      else {
        overrides[parts[0]] = overrides[parts[0]] || {};
        overrides[parts[0]][parts[1]] = n;
      }
    }
    const r = await apiPost("/paper/settings", overrides);
    setDirty(false);
    setMsg(`saved — strategy version: ${r.strategy}`);
    mutate?.();
  };

  const reset = async () => {
    const n = parseFloat(newCapital);
    await apiPost(`/paper/reset?starting_cash=${isNaN(n) ? 10000 : n}`);
    setConfirmReset(false);
    location.reload();
  };

  return (
    <div className="max-w-4xl mx-auto space-y-3">
      <SimStrip />
      <Panel title={<span>Demo Account Settings <span className="panel-sub">— all strategy, risk and execution knobs; no code changes needed (§3)</span></span>}
             right={<span className="pill-mini !text-violetx !border-violetx/50">{data?.strategy?.name}</span>}>
        {GROUPS.map((g) => (
          <div key={g.title} className="border-b border-line/40 last:border-0">
            <div className="px-4 pt-3 text-2xs font-bold tracking-widest text-dim uppercase">{g.title}</div>
            <div className="p-4 grid grid-cols-2 md:grid-cols-3 gap-3">
              {g.fields.map(([k, label, hint]) => (
                <div key={k}>
                  <label className="text-xs text-ink/70">{label}</label>
                  <input className="inp w-full mt-1 num" value={form[k] ?? ""}
                         onChange={(e) => { setForm({ ...form, [k]: e.target.value }); setDirty(true); }} />
                  {hint && <div className="text-2xs text-dim mt-0.5">{hint} · default {String(getPath(data?.defaults, k) === false ? 0 : getPath(data?.defaults, k) === true ? 1 : getPath(data?.defaults, k))}</div>}
                </div>
              ))}
            </div>
          </div>
        ))}
        <div className="p-4 flex items-center gap-3">
          <button disabled={!dirty} onClick={save}
                  className="pill-mini !text-cyanx !border-cyanx/60 !py-2 !px-4">
            Save settings
          </button>
          {msg && <span className="text-xs text-greenx">{msg}</span>}
          <span className="text-2xs text-dim ml-auto">Strategy/risk changes create a new strategy version — historical trades keep their original config (§22).</span>
        </div>
      </Panel>

      <Panel title={<span>Export (§24)</span>}>
        <div className="p-4 flex gap-2 flex-wrap text-xs">
          {[
            ["/api/paper/export?what=trades&fmt=csv", "Trades CSV"],
            ["/api/paper/export?what=trades&fmt=json", "Trades JSON"],
            ["/api/paper/export?what=signals&fmt=csv", "Signals CSV (incl. rejected)"],
            ["/api/paper/export?what=events&fmt=csv", "Timeline events CSV"],
            ["/api/paper/export?what=metrics&fmt=json", "Performance metrics JSON"],
          ].map(([href, l]) => (
            <a key={href} href={href} className="pill-mini !py-2">{l}</a>
          ))}
        </div>
      </Panel>

      <Panel title={<span className="text-redx">☠ Danger Zone — Reset Demo Account (§23)</span>}>
        {!confirmReset ? (
          <div className="p-4 flex items-center gap-3">
            <button className="pill-mini !text-redx !border-redx/60 !py-2 !px-4" onClick={() => setConfirmReset(true)}>
              Reset demo account
            </button>
            <span className="text-2xs text-dim">Export first if you want to keep the history.</span>
          </div>
        ) : (
          <div className="p-4 space-y-3">
            <div className="text-sm text-amberx leading-snug">
              This will permanently clear the current demo portfolio, open positions, trade history and
              performance statistics. This cannot be undone. (Virtual funds only — nothing real is affected.)
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs text-dim">New starting capital ($)</label>
              <input className="inp w-28 num" value={newCapital} onChange={(e) => setNewCapital(e.target.value)} />
            </div>
            <div className="flex gap-2">
              <button className="pill-mini !text-redx !border-redx/60 !py-2 !px-4" onClick={reset}>Yes, reset</button>
              <button className="pill-mini !py-2" onClick={() => setConfirmReset(false)}>Cancel</button>
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}

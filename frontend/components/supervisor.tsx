"use client";
/* Strategy supervisor: the AI reviews the Strategy Lab and PROPOSES changes —
   retire a losing strategy, test a challenger built from its losing trades,
   promote a challenger that won, or fix the main account. Nothing changes
   until the owner approves. */
import { useState } from "react";
import { apiPost, useApi, useRole } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Empty, Panel } from "./ui";

const KIND: Record<string, { label: string; badge: string; icon: string }> = {
  retire: { label: "Retire", badge: "badge-red", icon: "⏹️" },
  challenger: { label: "Challenger", badge: "badge-cyan", icon: "🧪" },
  promote: { label: "Promote", badge: "badge-green", icon: "🏆" },
  fix_main: { label: "Main account fix", badge: "badge-violet", icon: "🛠️" },
};
const STATUS: Record<string, string> = { approved: "badge-green", rejected: "badge-gray", expired: "badge-gray" };

function Stats({ s, label }: { s: any; label?: string }) {
  if (!s) return null;
  const ret = s.return_pct ?? s.total_return_pct;
  return (
    <span className="num">
      {label && <span className="text-dim">{label}: </span>}
      {s.trades ?? 0} trades · {ret == null ? "—" : `${ret >= 0 ? "+" : ""}${Number(ret).toFixed(1)}%`}
      {s.profit_factor != null && ` · PF ${s.profit_factor}`}
      {s.win_rate_pct != null && ` · win ${s.win_rate_pct}%`}
    </span>
  );
}

function Evidence({ p }: { p: any }) {
  const e = p.evidence || {};
  const bt = e.backtest;
  return (
    <div className="text-2xs text-dim space-y-0.5">
      {e.stats && <div><Stats s={e.stats} label="Record" /></div>}
      {e.parent_stats && <div><Stats s={e.parent_stats} label="Parent so far" /></div>}
      {e.challenger && <div><Stats s={e.challenger} label="Challenger" /> vs <Stats s={e.parent} label="parent" /></div>}
      {p.change && <div>Change: <span className="text-ink/80 num">{Object.entries(p.change).map(([k, v]) => `${k.replace(/_/g, " ")} → ${v}`).join(", ")}</span></div>}
      {bt && (
        <div className={bt.enough_data ? (bt.not_worse ? "text-greenx" : "text-redx") : ""}>
          Past {Math.round(bt.hours / 24)} days on the backtester: {bt.read}
          {bt.enough_data && <> (<Stats s={bt.challenger} /> vs <Stats s={bt.parent} />)</>}
        </div>
      )}
    </div>
  );
}

export function SupervisorPanel({ onChanged }: { onChanged?: () => void }) {
  const [k, setK] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const role = useRole();
  const owner = role === "owner";
  const { data } = useApi(`/paper/supervisor?k=${k}`, 60000);
  const pending: any[] = data?.pending || [];
  const log: any[] = data?.log || [];

  const act = async (path: string, key: string, done: string) => {
    setBusy(key); setMsg(null);
    try { await apiPost(path); setMsg(done); setK(k + 1); onChanged?.(); }
    catch (e: any) { setMsg(`Failed: ${e?.message || e}`); }
    finally { setBusy(null); }
  };

  return (
    <Panel title={<span>🧠 Strategy supervisor <span className="panel-sub">— the AI proposes, you approve. Nothing changes until you click Approve.</span></span>}
           right={owner && (
             <button className="pill-mini !text-cyanx !border-cyanx/60" disabled={busy === "review"}
                     onClick={() => act("/paper/supervisor/review", "review", "Review finished.")}>
               {busy === "review" ? "reviewing…" : "↻ Review now"}
             </button>)}>
      {msg && <div className="px-4 py-2 text-sm text-cyanx border-b border-line/50">{msg}</div>}
      {!data ? <Empty text="Loading…" /> : pending.length === 0 ? (
        <div className="px-4 py-3 text-sm text-dim leading-relaxed">
          No proposals right now. The supervisor reviews every few hours
          {data.last_review_ts ? ` (last ${timeAgo(data.last_review_ts)})` : ""}. It only proposes retiring a strategy after
          {" "}{data.rules?.min_trades ?? 30} trades with a clearly losing record, and only builds a challenger from a strategy
          with {data.rules?.parent_min_trades ?? 20}+ profitable trades.
        </div>
      ) : (
        <div className="divide-y divide-line/50">
          {pending.map((p) => {
            const kd = KIND[p.kind] || { label: p.kind, badge: "badge-gray", icon: "•" };
            return (
              <div key={p.id} className="px-4 py-3 space-y-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`badge ${kd.badge}`}>{kd.icon} {kd.label}</span>
                  <span className="font-semibold text-sm">{p.title}</span>
                  <span className="text-2xs text-dim ml-auto">{timeAgo(p.created_ts)}</span>
                </div>
                <p className="text-sm text-ink/80 leading-snug">{p.reason}</p>
                {p.ai_note && <p className="text-xs text-ink/75 border-l-2 border-cyanx/50 pl-2">🤖 {p.ai_note}</p>}
                <Evidence p={p} />
                {owner ? (
                  <div className="flex gap-2 pt-1">
                    <button className="btn btn-primary !py-1 !text-xs" disabled={!!busy}
                            onClick={() => act(`/paper/supervisor/proposals/${p.id}/approve`, `a${p.id}`, `Approved: ${p.title}`)}>
                      {busy === `a${p.id}` ? "applying…" : "✓ Approve"}</button>
                    <button className="btn !py-1 !text-xs" disabled={!!busy}
                            onClick={() => act(`/paper/supervisor/proposals/${p.id}/reject`, `r${p.id}`, `Rejected: ${p.title}`)}>
                      ✕ Reject</button>
                  </div>
                ) : <div className="text-2xs text-dim">Only the owner can approve.</div>}
              </div>
            );
          })}
        </div>
      )}
      {log.length > 0 && (
        <div className="border-t border-line/50">
          <button className="w-full text-left px-4 py-2 text-xs text-dim hover:text-ink" onClick={() => setShowLog(!showLog)}
                  aria-expanded={showLog}>
            {showLog ? "▾" : "▸"} Decision log ({log.length})
          </button>
          {showLog && (
            <div className="px-4 pb-3 space-y-1">
              {log.map((p) => (
                <div key={p.id} className="text-xs flex items-center gap-2 flex-wrap">
                  <span className={`badge ${STATUS[p.status] || "badge-gray"} !text-2xs`}>{p.status}</span>
                  <span className="text-ink/80">{p.title}</span>
                  <span className="text-2xs text-dim ml-auto">{p.decided_ts ? timeAgo(p.decided_ts) : ""}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="px-4 py-2 text-2xs text-dim border-t border-line/50">
        Proposals expire after {data?.rules?.expire_h ?? 48}h; rejected ones aren&apos;t proposed again for {data?.rules?.repropose_after_h ?? 72}h.
        Challengers are built from what a strategy&apos;s losing trades had in common (post-mortems) and checked on past data first. Virtual funds only.
      </div>
    </Panel>
  );
}

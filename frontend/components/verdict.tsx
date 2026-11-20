"use client";
import React, { useState } from "react";
import { useApi } from "@/lib/api";
import { fmtPct, timeAgo } from "@/lib/format";
import { Empty, Panel } from "./ui";

/* Backend timestamps are naive UTC ("2026-11-20T08:15:00"); without a zone the
   browser would read them as local time. */
export function utcSeconds(iso?: string | null): number | null {
  if (!iso) return null;
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const t = new Date(s).getTime();
  return Number.isFinite(t) ? Math.floor(t / 1000) : null;
}

export const RATING: Record<string, { label: string; emoji: string; cls: string; badge: string }> = {
  STRONG_WATCH: { label: "Strong watch", emoji: "🟢", cls: "text-greenx", badge: "badge-green" },
  WATCH: { label: "Watch", emoji: "🟡", cls: "text-amberx", badge: "badge-amber" },
  NEUTRAL: { label: "Neutral", emoji: "⚪", cls: "text-dim", badge: "badge-gray" },
  AVOID: { label: "Avoid", emoji: "🟠", cls: "text-amberx", badge: "badge-amber" },
  RUG_RISK: { label: "Rug risk", emoji: "🔴", cls: "text-redx", badge: "badge-red" },
  REVIEW: { label: "Review", emoji: "⏳", cls: "text-dim", badge: "badge-gray" },
};
const SOURCE: Record<string, string> = { llm: "AI debate", rules: "rules", gate: "safety gate" };
const RESULT: Record<string, string> = { right: "badge-green", wrong: "badge-red", mixed: "badge-gray" };

export function RatingBadge({ rating }: { rating?: string | null }) {
  const r = RATING[rating || ""] || RATING.REVIEW;
  return <span className={`badge ${r.badge}`}>{r.emoji} {r.label}</span>;
}

export function OutcomeChips({ o }: { o: any }) {
  if (!o) return <span className="text-2xs text-dim">not tracked</span>;
  if (o.status === "no_data") return <span className="text-2xs text-dim">no price data</span>;
  const chip = (label: string, v: number | null | undefined) => v == null ? null : (
    <span key={label} className={`num text-2xs ${v >= 0 ? "text-greenx" : "text-redx"}`}>{label} {fmtPct(v)}</span>
  );
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      {chip("1h", o.ret_1h)}{chip("6h", o.ret_6h)}{chip("12h", o.ret_12h)}{chip("24h", o.ret_24h)}
      {o.rugged && <span className="badge badge-red !text-2xs">rugged</span>}
      {o.result
        ? <span className={`badge ${RESULT[o.result] || "badge-gray"} !text-2xs`}>{o.result}</span>
        : <span className="text-2xs text-dim">scoring…</span>}
    </span>
  );
}

/* ------------------------------------------------------------ AI verdict */
export function VerdictPanel({ address }: { address: string }) {
  const { data } = useApi(`/tokens/${address}/verdict`, 60000);
  const [debate, setDebate] = useState(false);
  const v = data?.latest;
  const r = RATING[v?.rating || ""] || RATING.REVIEW;
  const when = utcSeconds(v?.created_at);
  return (
    <Panel title={<span>FOMO verdict <span className="panel-sub">safety gate → bull vs bear → judge</span></span>}
      right={v && <span className="badge badge-gray !text-2xs">{SOURCE[v.source] || v.source}</span>}>
      {!data && <Empty text="Loading…" />}
      {data && !data.available && <Empty text="No verdict yet: it appears once the on-chain checks for this coin are done." />}
      {v && (
        <div className="p-4 space-y-3">
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className={`text-2xl font-extrabold ${r.cls}`}>{r.emoji} {r.label}</span>
            {v.confidence && <span className="text-xs text-dim">{v.confidence} confidence</span>}
            {when && <span className="text-xs text-dim">· {timeAgo(when)}</span>}
          </div>
          <p className="text-sm leading-relaxed">{v.reason}</p>
          {v.judge_raw_rating && v.judge_raw_rating !== v.rating && (
            <div className="text-xs text-amberx">
              The AI judge said {RATING[v.judge_raw_rating]?.label || v.judge_raw_rating}; the safety rules lowered it.
            </div>
          )}
          {(v.positives?.length > 0 || v.risks?.length > 0) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
              <div>{(v.positives || []).map((p: string, i: number) => <div key={i} className="text-greenx">➕ <span className="text-ink">{p}</span></div>)}</div>
              <div>{(v.risks || []).map((p: string, i: number) => <div key={i} className="text-redx">➖ <span className="text-ink">{p}</span></div>)}</div>
            </div>
          )}
          {(v.bull || v.bear) && (
            <div>
              <button className="btn !py-0.5 !px-2 !text-2xs" onClick={() => setDebate(!debate)}>
                {debate ? "Hide the debate" : "Show the bull vs bear debate"}
              </button>
              {debate && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
                  <div className="rounded border border-greenx/30 bg-greenx/5 p-3 text-xs leading-relaxed">
                    <div className="font-semibold text-greenx mb-1">🐂 Bull researcher</div>{v.bull}</div>
                  <div className="rounded border border-redx/30 bg-redx/5 p-3 text-xs leading-relaxed">
                    <div className="font-semibold text-redx mb-1">🐻 Bear researcher</div>{v.bear}</div>
                </div>
              )}
            </div>
          )}
          <div className="flex items-center gap-2 flex-wrap text-xs border-t border-line/60 pt-2">
            <span className="text-dim">Since this call:</span><OutcomeChips o={v.outcome} />
          </div>
          {data.history?.length > 0 && (
            <div className="text-xs space-y-1">
              <div className="text-dim">Earlier calls</div>
              {data.history.slice(0, 5).map((h: any, i: number) => {
                const t = utcSeconds(h.created_at);
                return (
                  <div key={i} className="flex items-center gap-2 flex-wrap">
                    <RatingBadge rating={h.rating} />
                    <span className="text-dim">{t ? timeAgo(t) : ""}</span>
                    <OutcomeChips o={h.outcome} />
                  </div>
                );
              })}
            </div>
          )}
          <div className="text-2xs text-dim">{data.disclaimer}</div>
        </div>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------ launch wallets */
const ROLE: Record<string, string> = { creator: "badge-violet", bundle: "badge-red", sniper: "badge-amber", early: "badge-gray" };
const short = (a?: string | null) => (a ? `${a.slice(0, 4)}…${a.slice(-4)}` : "—");

function Stat({ label, value, bad, warn }: { label: string; value: React.ReactNode; bad?: boolean; warn?: boolean }) {
  return (
    <div className="px-3 py-2">
      <div className="text-2xs text-dim">{label}</div>
      <div className={`num text-base font-semibold ${bad ? "text-redx" : warn ? "text-amberx" : ""}`}>{value}</div>
    </div>
  );
}

export function LaunchWalletsPanel({ address }: { address: string }) {
  const { data } = useApi(`/tokens/${address}/launch-forensics`, 120000);
  const [all, setAll] = useState(false);
  const sevCls: Record<string, string> = { high: "text-redx", medium: "text-amberx" };
  const d = data;
  const pct = (v: number | null | undefined) => (v == null ? "—" : fmtPct(v, false));
  const when = utcSeconds(d?.analysed_at);
  const wallets = d?.wallets || [];
  return (
    <Panel title={<span>Launch wallets <span className="badge badge-green !text-2xs ml-1">real · Helius</span></span>}>
      {!d && <Empty text="Loading…" />}
      {d && !d.available && (
        <Empty text={d.status === "too_active" ? "Too many transactions before the check; the launch couldn't be reached."
          : d.status === "error" ? "Launch analysis failed; it will retry."
          : d.status === "no_data" ? "No launch transactions found."
          : "Not analysed yet. Runs once for fresh launches that pass the safety checks."} />
      )}
      {d?.available && (
        <div>
          <div className="grid grid-cols-2 sm:grid-cols-3 divide-x divide-y divide-line/60 border-b border-line/60">
            <Stat label={`Bundled (${d.bundle_wallets ?? 0} wallets, launch block)`} value={pct(d.bundle_pct)}
              bad={d.bundle_pct >= 15} warn={d.bundle_pct >= 5} />
            <Stat label={`Snipers (${d.sniper_wallets ?? 0} wallets, first ~1s)`} value={pct(d.sniper_pct)}
              bad={d.sniper_pct >= 25} warn={d.sniper_pct >= 10} />
            <Stat label={`First ${d.early_buyers ?? 0} buyers`} value={pct(d.early_pct)} />
            <Stat label="Dev bought → holds" value={<>{pct(d.dev_buy_pct)} → {pct(d.dev_holds_pct)}</>}
              warn={d.dev_buy_pct >= 1 && d.dev_holds_pct != null && d.dev_holds_pct < d.dev_buy_pct * 0.2} />
            <Stat label="Buyers funded by creator" value={d.funded_by_creator ?? "—"} bad={(d.funded_by_creator || 0) >= 1} />
            <Stat label="Largest shared-funder group" value={d.shared_funder_wallets ?? "—"}
              bad={(d.shared_funder_wallets || 0) >= 3} warn={d.shared_funder_wallets === 2} />
          </div>
          <div className="px-3.5 py-2 space-y-1">
            {(d.flags || []).map((f: any) => (
              <div key={f.id} className={`text-xs ${sevCls[f.severity] || ""}`}>⚠ {f.message}</div>
            ))}
            {!d.flags?.length && <div className="text-xs text-greenx">No launch manipulation flags.</div>}
            {d.funder_checked && !d.flags?.length ? (
              <div className="text-2xs text-dim">
                {d.fresh_early_wallets ?? 0} of {d.funder_checked} biggest early buyers are brand-new wallets ·
                snipers still hold {pct(d.snipers_hold_pct)}
              </div>
            ) : null}
          </div>
          {wallets.length > 0 && (
            <div className="border-t border-line/60">
              <table className="w-full text-xs">
                <thead><tr className="text-dim text-2xs">
                  <th className="text-left px-3.5 py-1 font-normal">Wallet</th>
                  <th className="text-left font-normal">Role</th>
                  <th className="text-right font-normal">Slot</th>
                  <th className="text-right font-normal">Bought</th>
                  <th className="text-right px-3.5 font-normal">Funded by</th>
                </tr></thead>
                <tbody>
                  {(all ? wallets : wallets.slice(0, 8)).map((w: any) => (
                    <tr key={w.wallet} className="border-t border-line/40">
                      <td className="px-3.5 py-1 font-mono">
                        <a className="text-cyanx hover:underline" href={`https://solscan.io/account/${w.wallet}`} target="_blank" rel="noreferrer">{short(w.wallet)}</a>
                        {w.fresh && <span className="badge badge-amber !text-2xs ml-1">new</span>}
                      </td>
                      <td><span className={`badge ${ROLE[w.role] || "badge-gray"} !text-2xs`}>{w.role}</span></td>
                      <td className="text-right num text-dim">+{w.slot_offset}</td>
                      <td className="text-right num">{pct(w.pct)}</td>
                      <td className="text-right px-3.5 font-mono">
                        {w.funder ? (
                          <a className={`hover:underline ${w.funder === d.creator ? "text-redx font-semibold" : "text-dim"}`}
                            href={`https://solscan.io/account/${w.funder}`} target="_blank" rel="noreferrer">
                            {w.funder === d.creator ? "CREATOR" : short(w.funder)}
                          </a>
                        ) : <span className="text-dim">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {wallets.length > 8 && (
                <button className="btn !py-0.5 !px-2 !text-2xs m-2" onClick={() => setAll(!all)}>
                  {all ? "Show fewer" : `Show all ${wallets.length}`}
                </button>
              )}
            </div>
          )}
          <div className="text-2xs text-dim px-3.5 py-2 border-t border-line/60">
            {d.caveat} {when ? `Analysed ${timeAgo(when)}.` : ""}
          </div>
        </div>
      )}
    </Panel>
  );
}

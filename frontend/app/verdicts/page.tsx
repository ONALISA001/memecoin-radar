"use client";
import Link from "next/link";
import React, { useState } from "react";
import { useApi } from "@/lib/api";
import { Empty, Panel, TokenAvatar } from "@/components/ui";
import { OutcomeChips, RATING, RatingBadge, utcSeconds } from "@/components/verdict";
import { fmtPct, timeAgo } from "@/lib/format";

const FILTERS: [string, string][] = [["", "All ratings"], ["STRONG_WATCH", "🟢 Strong watch"], ["WATCH", "🟡 Watch"],
  ["NEUTRAL", "⚪ Neutral"], ["AVOID", "🟠 Avoid"], ["RUG_RISK", "🔴 Rug risk"], ["REVIEW", "⏳ Review"]];
const SOURCE: Record<string, string> = { llm: "AI debate", rules: "Rules", gate: "Safety gate" };
/* look-back windows: [label, hours] */
const WINDOWS: [string, number][] = [["2h", 2], ["4h", 4], ["6h", 6], ["12h", 12], ["24h", 24],
  ["3d", 72], ["7d", 168], ["14d", 336], ["30d", 720]];
const MIX_ORDER = ["STRONG_WATCH", "WATCH", "NEUTRAL", "AVOID", "RUG_RISK", "REVIEW"];
const MIX_COLOR: Record<string, string> = { STRONG_WATCH: "#34D399", WATCH: "#FBBF24", NEUTRAL: "#64748B",
  AVOID: "#FB923C", RUG_RISK: "#F87171", REVIEW: "#334155" };
const hit = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function ScoreRow({ name, s }: { name: React.ReactNode; s: any }) {
  return (
    <tr className="border-t border-line/40">
      <td className="px-3.5 py-1.5">{name}</td>
      <td className="text-right num">{s.scored}<span className="text-dim">/{s.calls}</span></td>
      <td className="text-right num text-greenx">{s.right}</td>
      <td className="text-right num text-redx">{s.wrong}</td>
      <td className="text-right num font-semibold">{hit(s.hit_rate)}</td>
      <td className={`text-right num ${(s.avg_ret_1h ?? 0) >= 0 ? "text-greenx" : "text-redx"}`}>{s.avg_ret_1h == null ? "—" : fmtPct(s.avg_ret_1h)}</td>
      <td className={`text-right num ${(s.avg_ret_judged ?? 0) >= 0 ? "text-greenx" : "text-redx"}`}>{s.avg_ret_judged == null ? "—" : fmtPct(s.avg_ret_judged)}</td>
      <td className="text-right px-3.5 num">{hit(s.rug_rate)}</td>
    </tr>
  );
}

/** The market's current verdict at a glance: share of coins per rating. */
function RatingMix({ counts, onPick, active }: { counts: Record<string, number>; onPick: (r: string) => void; active: string }) {
  const total = MIX_ORDER.reduce((a, r) => a + (counts[r] || 0), 0);
  if (!total) return null;
  return (
    <div className="px-3.5 py-3 space-y-2">
      <div className="h-3 rounded-full overflow-hidden flex bg-line/30" role="img"
           aria-label={MIX_ORDER.map((r) => `${RATING[r].label} ${counts[r] || 0}`).join(", ")}>
        {MIX_ORDER.map((r) => (counts[r] || 0) > 0 && (
          <div key={r} style={{ width: `${(counts[r] / total) * 100}%`, background: MIX_COLOR[r] }}
               title={`${RATING[r].label}: ${counts[r]}`} />
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {MIX_ORDER.map((r) => (
          <button key={r} onClick={() => onPick(active === r ? "" : r)} aria-pressed={active === r}
                  className={`badge ${RATING[r].badge} ${active === r ? "ring-1 ring-cyanx" : ""} ${(counts[r] || 0) === 0 ? "opacity-40" : ""}`}>
            {RATING[r].emoji} {RATING[r].label} <span className="num ml-1">{counts[r] || 0}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export default function VerdictsPage() {
  const [hours, setHours] = useState(168);
  const [rating, setRating] = useState("");
  const winLabel = WINDOWS.find(([, h]) => h === hours)?.[0] || `${hours}h`;
  const { data: sc } = useApi(`/verdicts/scorecard?hours=${hours}`, 120000);
  const { data: list } = useApi(`/verdicts?limit=100&hours=${hours}${rating ? `&rating=${rating}` : ""}`, 60000);
  const o = sc?.overall;
  const counts: Record<string, number> = list?.counts || {};
  const live = MIX_ORDER.filter((r) => r !== "REVIEW").reduce((a, r) => a + (counts[r] || 0), 0);
  return (
    <div className="space-y-4">
      <div className="panel p-4 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-extrabold">🧠 FOMO verdicts</h1>
        <span className="text-sm text-dim">Every call is tracked with real prices at 1h, 6h, 12h and 24h and judged right or wrong at {sc?.judge_h ?? 12}h.</span>
        <div className="flex-1" />
        <div className="flex items-center gap-0.5 flex-wrap" role="group" aria-label="Time window">
          {WINDOWS.map(([l, h]) => (
            <button key={l} onClick={() => setHours(h)} aria-pressed={hours === h}
                    className={`pill-mini ${hours === h ? "!text-cyanx !border-cyanx/60" : "text-dim border-line"}`}>{l}</button>
          ))}
        </div>
      </div>

      <Panel title={<span>Verdict mix <span className="panel-sub">— latest call per coin, last {winLabel} · {live} rated, {counts.REVIEW || 0} waiting for market data</span></span>}>
        {!list && <Empty text="Loading…" />}
        {list && !Object.keys(counts).length && <Empty text={`No coin has been judged in the last ${winLabel}.`} />}
        <RatingMix counts={counts} onPick={setRating} active={rating} />
      </Panel>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start">
        <Panel className="xl:col-span-2" title={<span>Scorecard <span className="panel-sub">calls from the last {winLabel} · judged at {sc?.judge_h ?? 12}h</span></span>}>
          {!sc && <Empty text="Loading…" />}
          {sc && !o?.calls && <Empty text={`No verdicts in the last ${winLabel} yet.`} />}
          {sc && o?.calls > 0 && (
            <div>
              <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-line/60 border-b border-line/60">
                {[["Calls scored", `${o.scored}`], ["Hit rate", hit(o.hit_rate)],
                  ["Right / wrong / mixed", `${o.right} / ${o.wrong} / ${o.mixed}`], [`Waiting for ${sc.judge_h ?? 12}h`, `${o.pending}`]]
                  .map(([l, v]) => (
                    <div key={l} className="px-3.5 py-2.5"><div className="text-2xs text-dim">{l}</div>
                      <div className="text-xl font-extrabold num">{v}</div></div>
                  ))}
              </div>
              {hours < (sc.judge_h ?? 12) && (
                <div className="px-3.5 py-2 text-xs text-amberx border-b border-line/60">
                  Calls this recent can&apos;t be judged yet (that happens at {sc.judge_h ?? 12}h). Use the 1h average return for an early read.
                </div>
              )}
              <table className="w-full text-sm">
                <thead><tr className="text-dim text-2xs">
                  <th className="text-left px-3.5 py-1 font-normal">Rating</th><th className="text-right font-normal">Scored</th>
                  <th className="text-right font-normal">Right</th><th className="text-right font-normal">Wrong</th>
                  <th className="text-right font-normal">Hit rate</th><th className="text-right font-normal">Avg 1h</th>
                  <th className="text-right font-normal">Avg {sc.judge_h ?? 12}h</th>
                  <th className="text-right px-3.5 font-normal">Rugged</th>
                </tr></thead>
                <tbody>
                  {Object.entries(sc.by_rating).map(([r, s]: [string, any]) => s.calls > 0 && (
                    <ScoreRow key={r} name={<RatingBadge rating={r} />} s={s} />
                  ))}
                  <tr><td colSpan={8} className="px-3.5 pt-3 pb-1 text-2xs text-dim">By source</td></tr>
                  {Object.entries(sc.by_source).map(([k, s]: [string, any]) => s.calls > 0 && (
                    <ScoreRow key={k} name={SOURCE[k] || k} s={s} />
                  ))}
                </tbody>
              </table>
              <div className="text-2xs text-dim px-3.5 py-2 border-t border-line/60 whitespace-pre-line">{String(sc.rules || "").replace(/^\(judged at \d+h\)\s*/, "")}</div>
            </div>
          )}
        </Panel>

        <Panel title="Worst misses">
          {!sc?.worst_misses?.length && <Empty text="No wrong calls in this period." />}
          {sc?.worst_misses?.map((m: any) => (
            <Link key={m.verdict_id} href={`/token/${m.address}`} className="flex items-center gap-2 px-3.5 py-2 border-t border-line/40 hover:bg-panel2 text-sm">
              <span className="font-semibold">${m.symbol}</span><RatingBadge rating={m.rating} />
              <span className="flex-1" />
              {m.rugged ? <span className="badge badge-red !text-2xs">rugged</span>
                : <span className={`num ${(m.ret_judged ?? 0) >= 0 ? "text-greenx" : "text-redx"}`}>{fmtPct(m.ret_judged)} <span className="text-dim text-2xs">at {m.judge_h}h</span></span>}
            </Link>
          ))}
        </Panel>
      </div>

      <Panel title={<span>Latest call per coin <span className="panel-sub">last {winLabel}</span></span>} right={
        <select className="btn !py-0.5 bg-panel !text-xs" aria-label="Rating filter" value={rating} onChange={(e) => setRating(e.target.value)}>
          {FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>}>
        {!list && <Empty text="Loading…" />}
        {list && !list.items?.length && <Empty text={rating ? "No coin has this rating in this window." : "No verdicts in this window yet."} />}
        {list?.items?.map((v: any) => {
          const t = utcSeconds(v.created_at);
          const provisional = /Provisional:/.test(v.reason || "");
          return (
            <Link key={v.address} href={`/token/${v.address}`}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3.5 py-2 border-t border-line/40 hover:bg-panel2">
              <TokenAvatar url={v.image_url} symbol={v.symbol} size={22} />
              <span className="font-semibold w-24 truncate">${v.symbol}</span>
              <span className="w-32"><RatingBadge rating={v.rating} /></span>
              <span className="text-xs text-dim w-28">{SOURCE[v.source] || v.source}{t ? ` · ${timeAgo(t)}` : ""}
                {provisional && <span className="badge badge-gray !text-2xs ml-1" title="Some on-chain checks are still pending; capped at Watch">provisional</span>}</span>
              <span className="text-xs flex-1 min-w-[200px] truncate text-dim">{v.reason}</span>
              {v.rating !== "REVIEW" && <OutcomeChips o={v.outcome} />}
            </Link>
          );
        })}
      </Panel>
      <div className="text-2xs text-dim">Ratings are research labels from real on-chain and market evidence, not buy or sell signals.
        A provisional call is made while some on-chain checks are still running; it can&apos;t go above Watch until they finish.</div>
    </div>
  );
}

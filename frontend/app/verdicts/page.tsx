"use client";
import Link from "next/link";
import React, { useState } from "react";
import { useApi } from "@/lib/api";
import { Empty, Panel, TokenAvatar } from "@/components/ui";
import { OutcomeChips, RatingBadge, utcSeconds } from "@/components/verdict";
import { fmtPct, timeAgo } from "@/lib/format";

const FILTERS: [string, string][] = [["", "All ratings"], ["STRONG_WATCH", "🟢 Strong watch"], ["WATCH", "🟡 Watch"],
  ["NEUTRAL", "⚪ Neutral"], ["AVOID", "🟠 Avoid"], ["RUG_RISK", "🔴 Rug risk"], ["REVIEW", "⏳ Review"]];
const SOURCE: Record<string, string> = { llm: "AI debate", rules: "Rules", gate: "Safety gate" };
const hit = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function ScoreRow({ name, s }: { name: React.ReactNode; s: any }) {
  return (
    <tr className="border-t border-line/40">
      <td className="px-3.5 py-1.5">{name}</td>
      <td className="text-right num">{s.scored}<span className="text-dim">/{s.calls}</span></td>
      <td className="text-right num text-greenx">{s.right}</td>
      <td className="text-right num text-redx">{s.wrong}</td>
      <td className="text-right num font-semibold">{hit(s.hit_rate)}</td>
      <td className={`text-right num ${(s.avg_ret_judged ?? 0) >= 0 ? "text-greenx" : "text-redx"}`}>{s.avg_ret_judged == null ? "—" : fmtPct(s.avg_ret_judged)}</td>
      <td className="text-right px-3.5 num">{hit(s.rug_rate)}</td>
    </tr>
  );
}

export default function VerdictsPage() {
  const [days, setDays] = useState(7);
  const [rating, setRating] = useState("");
  const { data: sc } = useApi(`/verdicts/scorecard?days=${days}`, 120000);
  const { data: list } = useApi(`/verdicts?limit=100${rating ? `&rating=${rating}` : ""}`, 60000);
  const o = sc?.overall;
  return (
    <div className="space-y-4">
      <div className="panel p-4 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-extrabold">🧠 FOMO verdicts</h1>
        <span className="text-sm text-dim">Every call is tracked with real prices at 1h, 6h, 12h and 24h and judged right or wrong at {sc?.judge_h ?? 12}h.</span>
        <div className="flex-1" />
        <select className="btn !py-1 bg-panel" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[1, 3, 7, 14, 30].map((d) => <option key={d} value={d}>Last {d} day{d > 1 ? "s" : ""}</option>)}
        </select>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start">
        <Panel className="xl:col-span-2" title={<span>Scorecard <span className="panel-sub">judged at {sc?.judge_h ?? 12}h</span></span>}>
          {!sc && <Empty text="Loading…" />}
          {sc && !o?.calls && <Empty text="No verdicts in this period yet." />}
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
              <table className="w-full text-sm">
                <thead><tr className="text-dim text-2xs">
                  <th className="text-left px-3.5 py-1 font-normal">Rating</th><th className="text-right font-normal">Scored</th>
                  <th className="text-right font-normal">Right</th><th className="text-right font-normal">Wrong</th>
                  <th className="text-right font-normal">Hit rate</th><th className="text-right font-normal">Avg {sc.judge_h ?? 12}h</th>
                  <th className="text-right px-3.5 font-normal">Rugged</th>
                </tr></thead>
                <tbody>
                  {Object.entries(sc.by_rating).map(([r, s]: [string, any]) => s.calls > 0 && (
                    <ScoreRow key={r} name={<RatingBadge rating={r} />} s={s} />
                  ))}
                  <tr><td colSpan={7} className="px-3.5 pt-3 pb-1 text-2xs text-dim">By source</td></tr>
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

      <Panel title="Latest call per coin" right={
        <select className="btn !py-0.5 bg-panel !text-xs" value={rating} onChange={(e) => setRating(e.target.value)}>
          {FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>}>
        {!list && <Empty text="Loading…" />}
        {list && !list.items?.length && <Empty text="No verdicts yet." />}
        {list?.items?.map((v: any) => {
          const t = utcSeconds(v.created_at);
          return (
            <Link key={v.address} href={`/token/${v.address}`}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3.5 py-2 border-t border-line/40 hover:bg-panel2">
              <TokenAvatar url={v.image_url} symbol={v.symbol} size={22} />
              <span className="font-semibold w-24 truncate">${v.symbol}</span>
              <span className="w-32"><RatingBadge rating={v.rating} /></span>
              <span className="text-xs text-dim w-24">{SOURCE[v.source] || v.source}{t ? ` · ${timeAgo(t)}` : ""}</span>
              <span className="text-xs flex-1 min-w-[200px] truncate text-dim">{v.reason}</span>
              {v.rating !== "REVIEW" && <OutcomeChips o={v.outcome} />}
            </Link>
          );
        })}
      </Panel>
      <div className="text-2xs text-dim">Ratings are research labels from real on-chain and market evidence, not buy or sell signals.</div>
    </div>
  );
}

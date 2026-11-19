"use client";
import { NewsRow } from "@/components/news";
import Link from "next/link";
import { use } from "react";
import { useApi } from "@/lib/api";
import { Badge, Empty, FomoPill, Panel, RiskPill, TrendPill } from "@/components/ui";
import { fmtPct, pctColor, timeAgo } from "@/lib/format";

export default function NarrativeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, loading } = useApi(`/narratives/${id}`, 30000);
  if (loading && !data) return <Empty text="Loading narrative…" />;
  if (!data?.narrative) return <Empty text="Narrative not found." />;
  const n = data.narrative;
  return (
    <div className="space-y-4">
      <div className="panel p-5">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-2xl font-extrabold">🧠 {n.name}</h1>
          {n.kind === "auto" && <Badge kind="violet">auto-discovered</Badge>}
          <Badge kind={n.cross_platform === "HIGH" ? "green" : n.cross_platform === "MEDIUM" ? "amber" : "gray"}>
            {n.cross_platform} cross-platform
          </Badge>
          <span className="ml-auto num text-3xl font-extrabold text-violetx">{Math.round(n.score)}<span className="text-sm text-dim">/100</span></span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-4">
          {[
            ["Mentions growth", <span key="1" className={pctColor(n.mentions_growth)}>{fmtPct(n.mentions_growth)}</span>],
            ["Associated tokens", n.token_count],
            ["Strong candidates", n.strong_candidates],
            ["Abnormal volume", n.abnormal_volume_tokens],
            ["First seen", n.first_seen_at ? new Date(n.first_seen_at).toLocaleDateString() : "—"],
          ].map(([l, v], i) => (
            <div key={i} className="bg-panel2 border border-line rounded-lg p-2.5">
              <div className="text-2xs font-bold tracking-widest text-dim uppercase">{l}</div>
              <div className="num text-lg font-bold mt-0.5">{v as any}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start">
        <Panel title={`Tokens in this narrative (${data.tokens.length})`} className="xl:col-span-2">
          <div className="overflow-x-auto">
            <table className="tbl">
              <thead><tr><th>TOKEN</th><th>RELEVANCE</th><th>TREND</th><th>FOMO</th><th>RISK</th></tr></thead>
              <tbody>
                {data.tokens.map((t: any) => (
                  <tr key={t.address}>
                    <td className="!text-left">
                      <Link href={`/token/${t.address}`} className="hover:text-cyanx">
                        <span className="font-bold">${t.symbol}</span> <span className="text-dim text-sm">{t.name}</span>
                      </Link>
                    </td>
                    <td>
                      <div className="scorebar w-16 ml-auto"><div style={{ width: `${t.relevance * 100}%`, background: "#A78BFA" }} /></div>
                    </td>
                    <td><TrendPill value={t.trend} /></td>
                    <td><FomoPill value={t.fomo} /></td>
                    <td><RiskPill value={t.risk} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title={<span>News coverage {data.news_source === "rss"
          ? <span className="badge badge-green !text-2xs ml-1">real · publisher feeds</span>
          : <span className="badge badge-amber !text-2xs ml-1">simulated</span>}</span>}>
          {data.news.length === 0 && <Empty text="No matching headlines in the last 7 days." />}
          {data.news.map((a: any, i: number) => <NewsRow key={i} a={a} />)}
        </Panel>
      </div>
    </div>
  );
}

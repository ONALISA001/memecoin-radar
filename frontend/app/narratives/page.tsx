"use client";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { Badge, Empty, Panel } from "@/components/ui";
import { fmtPct, pctColor } from "@/lib/format";

export default function NarrativesPage() {
  const { data, loading } = useApi("/narratives/trending", 30000);
  const items = data?.items || [];
  return (
    <div className="space-y-4">
      <Panel title={<span>Emerging narratives <span className="panel-sub">(seeded taxonomy + auto-discovered clusters)</span></span>}>
        {loading && !items.length ? <Empty text="Loading…" /> : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-0 divide-x divide-y divide-line/60">
            {items.map((n: any) => (
              <Link key={n.id} href={`/narratives/${n.id}`} className="p-4 hover:bg-panel2 transition-colors">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-base">{n.name}</span>
                  <span className="num text-lg font-bold text-violetx">{Math.round(n.score)}<span className="text-xs text-dim">/100</span></span>
                </div>
                <div className="scorebar mt-2"><div style={{ width: `${Math.min(100, n.score)}%`, background: "#A78BFA" }} /></div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-3 text-sm">
                  <div className="flex justify-between"><span className="text-dim">mentions</span><span className={pctColor(n.mentions_growth)}>{fmtPct(n.mentions_growth)}</span></div>
                  <div className="flex justify-between"><span className="text-dim">tokens</span><span className="num">{n.token_count}</span></div>
                  <div className="flex justify-between"><span className="text-dim">strong</span><span className="num text-cyanx">{n.strong_candidates}</span></div>
                  <div className="flex justify-between"><span className="text-dim">abn. volume</span><span className="num text-amberx">{n.abnormal_volume_tokens}</span></div>
                </div>
                <div className="flex items-center gap-1.5 mt-3 flex-wrap">
                  <Badge kind={n.cross_platform === "HIGH" ? "green" : n.cross_platform === "MEDIUM" ? "amber" : "gray"}>
                    {n.cross_platform} x-platform
                  </Badge>
                  {n.kind === "auto" && <Badge kind="violet">auto</Badge>}
                  {(n.top_tokens || []).slice(0, 4).map((t: any) => (
                    <span key={t.address} className="text-xs text-dim bg-panel2 border border-line rounded px-1 py-0.5">
                      ${t.symbol} <span className="text-cyanx num">{Math.round(t.trend)}</span>
                    </span>
                  ))}
                </div>
              </Link>
            ))}
          </div>
        )}
      </Panel>
      <p className="text-xs text-dim px-1">
        Narrative Score = mention growth (30) + member tokens (20) + member volume growth (20) +
        strong candidates (15) + news activity (10) + cross-platform confirmation (5). Auto-discovered
        narratives come from keyword clustering over uncovered tokens (Phase 4: full NLP clustering).
      </p>
    </div>
  );
}

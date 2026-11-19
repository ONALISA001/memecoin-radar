"use client";
import Link from "next/link";
import { useState } from "react";
import { apiDelete, useApi } from "@/lib/api";
import { WatchlistIntel } from "@/components/intelfeed";
import { Badge, ConfChip, Empty, FomoPill, Panel, RiskPill, Sparkline, TokenAvatar, TrendPill } from "@/components/ui";
import { fmtPct, fmtUsd, pctColor, trendColor } from "@/lib/format";

function Trajectory({ history }: { history: any[] }) {
  const pts = (history || []).map((h) => h.trend);
  if (pts.length < 2) return <span className="text-xs text-dim">collecting history…</span>;
  return (
    <div className="flex items-center gap-2">
      <Sparkline points={pts} w={90} h={26} color={trendColor(pts[pts.length - 1])} />
      <span className="num text-xs text-dim whitespace-nowrap">
        {pts.slice(-5).map((v) => Math.round(v)).join(" → ")}
      </span>
      {pts[pts.length - 1] > pts[0] ? <Badge kind="green">strengthening</Badge> : pts[pts.length - 1] < pts[0] ? <Badge kind="red">weakening</Badge> : null}
    </div>
  );
}

export default function WatchlistPage() {
  const { data, loading } = useApi("/watchlist", 20000);
  const [busy, setBusy] = useState<string | null>(null);
  const items = data?.items || [];
  async function remove(address: string) {
    setBusy(address);
    try { await apiDelete(`/watchlist/${address}`); location.reload(); } finally { setBusy(null); }
  }
  return (
    <div className="space-y-4">
    <WatchlistIntel hours={24} />
    <Panel title={<span>Watchlist <span className="panel-sub">(score trajectories update every ingestion tick)</span></span>}>
      {loading && items.length === 0 ? <Empty text="Loading…" /> :
       items.length === 0 ? (
        <div className="p-8 text-center text-dim text-sm">
          Nothing watched yet. Open any token and hit <span className="text-cyanx">☆ Watch</span>.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-0 divide-x divide-y divide-line/60">
          {items.map((t: any) => (
            <div key={t.address} className="p-4 hover:bg-panel2/60">
              <div className="flex items-center gap-3">
                <TokenAvatar url={t.image_url} symbol={t.symbol} size={34} />
                <div className="flex-1 min-w-0">
                  <Link href={`/token/${t.address}`} className="font-bold text-lg hover:text-cyanx">${t.symbol}</Link>
                  <div className="text-sm text-dim truncate">{t.name} · {t.chain}</div>
                </div>
                <ConfChip level={(t.summary || {}).confidence} />
                <button className="btn btn-danger !py-1 !px-2 !text-xs" disabled={busy === t.address}
                        onClick={() => remove(t.address)}>remove</button>
              </div>
              <div className="grid grid-cols-3 gap-2 mt-3">
                <div><div className="text-2xs text-dim font-bold tracking-widest">TREND</div><TrendPill value={t.trend} /></div>
                <div><div className="text-2xs text-dim font-bold tracking-widest">FOMO</div><FomoPill value={t.fomo} cls={t.fomo_class} /></div>
                <div><div className="text-2xs text-dim font-bold tracking-widest">RISK</div><RiskPill value={t.risk} cls={t.risk_class} /></div>
              </div>
              <div className="mt-3 flex items-center justify-between gap-3">
                <Trajectory history={t.score_history} />
                <div className="text-right text-sm num">
                  <div className={pctColor(t.summary?.social_growth)}>{fmtPct(t.summary?.social_growth)} soc</div>
                  <div className="text-dim">{fmtUsd(t.summary?.volume_1h)} vol 1h</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
    </div>
  );
}

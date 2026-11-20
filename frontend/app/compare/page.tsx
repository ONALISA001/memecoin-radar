"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useApi } from "@/lib/api";
import { Badge, Empty, Panel, TokenAvatar } from "@/components/ui";
import { bandLabel, BAND_COLORS } from "@/components/marketintel";
import { RatingBadge } from "@/components/verdict";
import { fmtNum, fmtPct } from "@/lib/format";

type Row = { label: string; key: string; fmt?: (v: any) => string; good?: 1 | -1 | 0; band?: string };

const ROWS: Row[] = [
  { label: "Momentum", key: "momentum", good: 1, band: "momentum_band" },
  { label: "Sell pressure", key: "sellp", good: -1, band: "sellp_band" },
  { label: "Risk score", key: "risk", good: -1 },
  { label: "Trend score", key: "trend", good: 1 },
  { label: "FOMO score", key: "fomo", good: -1 },
  { label: "Liquidity health", key: "liq_health", good: 1, band: "liq_band" },
  { label: "Liquidity $", key: "liquidity", fmt: (v) => "$" + fmtNum(v), good: 1 },
  { label: "Market cap", key: "mcap", fmt: (v) => "$" + fmtNum(v) },
  { label: "Volume 24h", key: "volume_24h", fmt: (v) => "$" + fmtNum(v), good: 1 },
  { label: "Volume acceleration 1h", key: "vol_accel_h1", fmt: (v) => fmtPct(v), good: 1 },
  { label: "Holder growth 1h", key: "holder_growth", fmt: (v) => fmtPct(v), good: 1 },
  { label: "Buy share 1h", key: "buy_share", fmt: (v) => Math.round((v || 0) * 100) + "%", good: 1 },
  { label: "Price 24h", key: "price_change_h24", fmt: (v) => fmtPct(v), good: 1 },
  { label: "Price 1h", key: "price_change_h1", fmt: (v) => fmtPct(v), good: 1 },
  { label: "Organic score", key: "organic", good: 1 },
  { label: "Token age", key: "age_hours", fmt: (v) => v >= 900 ? "unknown" : v < 48 ? Math.round(v) + "h" : Math.round(v / 24) + "d" },
];

const MAX = 5;

/** Click-to-add picker over every tracked coin (no address pasting). */
function CoinPicker({ selected, onAdd, onSet }: { selected: string[]; onAdd: (a: string) => void; onSet: (a: string[]) => void }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("volume");
  const { data, loading } = useApi(`/tokens/trending?sort=${sort}&limit=100`, 60000);
  const { data: prio } = useApi("/signals/priority?window=4h&limit=5", 60000);
  const { data: wl } = useApi("/watchlist", 60000);
  const all: any[] = data?.items || [];
  const term = q.trim().toLowerCase();
  const list = all.filter((t) => !term || (t.symbol || "").toLowerCase().includes(term) || (t.name || "").toLowerCase().includes(term));
  const full = selected.length >= MAX;
  const quick: [string, string[]][] = [
    ["Top 3 by volume", [...all].sort((a, b) => (b.volume_1h || 0) - (a.volume_1h || 0)).slice(0, 3).map((t) => t.address)],
    ["Priority coins", (prio?.items || []).slice(0, MAX).map((t: any) => t.address)],
    ["My watchlist", (wl?.items || []).slice(0, MAX).map((t: any) => t.address)],
  ];
  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap gap-2 items-center">
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter coins"
               placeholder="Filter by symbol or name…" className="inp !w-56" />
        <select className="sel !w-auto" aria-label="Sort coins" value={sort} onChange={(e) => setSort(e.target.value)}>
          {[["volume", "Volume 1h"], ["trend", "Trend"], ["liquidity", "Liquidity"], ["price_change", "Price 1h"], ["age", "Newest"]]
            .map(([v, l]) => <option key={v} value={v}>Sort: {l}</option>)}
        </select>
        <span className="text-2xs text-dim">Quick pick:</span>
        {quick.map(([label, addrs]) => (
          <button key={label} className="pill-mini text-dim border-line hover:text-ink disabled:opacity-40"
                  disabled={addrs.length < 2} onClick={() => onSet(addrs)}
                  title={addrs.length < 2 ? "Needs at least 2 coins" : `Compare ${addrs.length} coins`}>{label}</button>
        ))}
      </div>
      <div className="max-h-[260px] overflow-y-auto rounded-lg border border-line/60 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
        {loading && !data && <div className="p-3 text-xs text-dim">Loading coins…</div>}
        {data && list.length === 0 && <div className="p-3 text-xs text-dim">No tracked coin matches “{q}”.</div>}
        {list.map((t) => {
          const on = selected.includes(t.address);
          return (
            <button key={t.address} disabled={on || full} onClick={() => onAdd(t.address)}
                    className={`flex items-center gap-2 px-3 py-2 text-left border-b border-line/40 hover:bg-panel2 disabled:cursor-default ${on ? "bg-cyanx/10" : full ? "opacity-50" : ""}`}>
              <TokenAvatar url={t.image_url} symbol={t.symbol} size={22} />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold truncate">${t.symbol}</span>
                <span className="block text-2xs text-dim num truncate">liq ${fmtNum(t.liquidity)} · vol ${fmtNum(t.volume_1h)}</span>
              </span>
              <span className={`num text-xs ${(t.price_change_h1 || 0) >= 0 ? "text-greenx" : "text-redx"}`}>{fmtPct(t.price_change_h1)}</span>
              <span className="text-xs w-4 text-right">{on ? "✓" : "+"}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function CompareInner() {
  const sp = useSearchParams();
  const [addrs, setAddrs] = useState<string[]>([]);
  useEffect(() => {
    const t = sp.get("tokens");
    if (t && addrs.length === 0) {
      setAddrs(t.split(",").map((x) => x.trim()).filter(Boolean).slice(0, MAX));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const qs = addrs.join(",");
  const { data, loading } = useApi(qs ? `/tokens/compare?addresses=${qs}` : null, 30000);
  const items: any[] = data?.items || [];
  const missing: string[] = data?.missing || [];
  const byAddr: Record<string, any> = Object.fromEntries(items.map((t) => [t.address, t]));
  const add = (a: string) => { if (!addrs.includes(a) && addrs.length < MAX) setAddrs([...addrs, a]); };

  const best = (key: string, good?: 1 | -1 | 0) => {
    if (!good || !items.length) return -1;
    let bi = 0;
    items.forEach((it, i) => {
      const a = (it[key] ?? (good > 0 ? -1e18 : 1e18)), b = (items[bi][key] ?? (good > 0 ? -1e18 : 1e18));
      if (good > 0 ? a > b : a < b) bi = i;
    });
    return bi;
  };

  return (
    <div className="space-y-4">
      <div className="panel p-4">
        <h1 className="text-lg font-extrabold">⚖️ Compare Memecoins</h1>
        <p className="text-sm text-dim mt-1">Side-by-side intelligence: momentum vs distribution vs structure. Pick up to {MAX} tracked coins.</p>
        <div className="flex gap-1.5 flex-wrap items-center mt-3 min-h-[26px]">
          <span className="text-2xs text-dim">Comparing ({addrs.length}/{MAX}):</span>
          {addrs.length === 0 && <span className="text-xs text-dim">nothing yet — pick coins below</span>}
          {addrs.map((a) => (
            <span key={a} className="badge badge-gray text-xs inline-flex items-center gap-1">
              {byAddr[a] ? <><TokenAvatar url={byAddr[a].image_url} symbol={byAddr[a].symbol} size={14} /> ${byAddr[a].symbol}</> : <span className="num">{a.slice(0, 4)}…{a.slice(-4)}</span>}
              <button className="ml-1 text-redx" aria-label="Remove" onClick={() => setAddrs(addrs.filter((x) => x !== a))}>✕</button>
            </span>
          ))}
          {addrs.length > 0 && <button className="text-xs text-dim hover:text-ink hover:underline" onClick={() => setAddrs([])}>clear</button>}
        </div>
        <CoinPicker selected={addrs} onAdd={add} onSet={(a) => setAddrs(a.slice(0, MAX))} />
      </div>

      {addrs.length < 2 && <Empty text="Pick at least two coins to compare." />}
      {loading && qs && <Empty text="Comparing…" />}
      {missing.length > 0 && <div className="text-xs text-amberx">⚠ no longer tracked: {missing.map((m) => m.slice(0, 8) + "…").join(", ")}</div>}

      {items.length > 0 && (
        <Panel title="Head-to-head" className="overflow-x-auto">
          <table className="tbl min-w-[640px]">
            <thead>
              <tr>
                <th className="!text-left">METRIC</th>
                {items.map((t) => (
                  <th key={t.address} className="!text-left">
                    <a href={`/token/${t.address}`} className="flex items-center gap-2 hover:text-cyanx">
                      <TokenAvatar url={t.image_url} symbol={t.symbol} size={22} />
                      <span>${t.symbol}</span>
                    </a>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => {
                const bs = best(row.key, row.good);
                return (
                  <tr key={row.key}>
                    <td className="text-dim text-xs uppercase tracking-wider">{row.label}</td>
                    {items.map((t, i) => {
                      const v = t[row.key];
                      const bandName = row.band ? t[row.band] : undefined;
                      const color = bandName ? (BAND_COLORS[bandName] || "#E5EAF2") : (i === bs ? "#34D399" : "#E5EAF2");
                      return (
                        <td key={t.address} className="!text-left">
                          <span className="num text-sm font-bold" style={{ color }}>
                            {v === null || v === undefined ? "—" : row.fmt ? row.fmt(v) : Math.round(v)}
                          </span>
                          {i === bs && !bandName && <span className="text-2xs text-greenx ml-1">▲ best</span>}
                          {bandName && <div className="text-2xs" style={{ color }}>{bandLabel(bandName)}</div>}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
              <tr>
                <td className="text-dim text-xs uppercase tracking-wider">FOMO verdict</td>
                {items.map((t) => (
                  <td key={t.address} className="!text-left text-xs">
                    {t.verdict ? <RatingBadge rating={t.verdict} /> : <span className="text-dim">not judged yet</span>}
                  </td>
                ))}
              </tr>
              <tr>
                <td className="text-dim text-xs uppercase tracking-wider">Whale flow</td>
                {items.map((t) => (
                  <td key={t.address} className="!text-left text-xs" style={{ color: BAND_COLORS[t.whale_class] || "#E5EAF2" }}>
                    {t.whale_class === "accumulating" ? "🟢 accumulating" : t.whale_class === "neutral" || !t.whale_class ? "🟡 neutral" : "🔴 " + (t.whale_class || "").replace("_", " ")}
                    <div className="text-2xs text-dim">(inferred)</div>
                  </td>
                ))}
              </tr>
              <tr>
                <td className="text-dim text-xs uppercase tracking-wider">Confluence</td>
                {items.map((t) => (
                  <td key={t.address} className="!text-left text-xs">
                    {t.confluence === "emerging_momentum" ? <Badge kind="green">🟢 emerging momentum</Badge>
                     : t.confluence === "potential_distribution" ? <Badge kind="red">🔴 potential distribution</Badge>
                     : <span className="text-dim">—</span>}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </Panel>
      )}
      <div className="text-2xs text-dim leading-relaxed">
        ▲ "best" marks the most favorable observed value per row (structure/flow), not a recommendation.
        Whale flow is inferred from aggregate large-wallet behavior. Analytics only — not financial advice.
      </div>
    </div>
  );
}

export default function ComparePage() {
  return <Suspense fallback={null}><CompareInner /></Suspense>;
}

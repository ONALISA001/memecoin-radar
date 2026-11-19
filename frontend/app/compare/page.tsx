"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useApi, api } from "@/lib/api";
import { Badge, Empty, Panel, TokenAvatar } from "@/components/ui";
import { bandLabel, BAND_COLORS } from "@/components/marketintel";
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

function CompareInner() {
  const sp = useSearchParams();
  const [addrs, setAddrs] = useState<string[]>([]);
  useEffect(() => {
    const t = sp.get("tokens");
    if (t && addrs.length === 0) {
      setAddrs(t.split(",").map((x) => x.trim()).filter(Boolean).slice(0, 5));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const qs = addrs.join(",");
  const { data, loading } = useApi(qs ? `/tokens/compare?addresses=${qs}` : null, 30000);
  const items: any[] = data?.items || [];
  const missing: string[] = data?.missing || [];

  async function addAddress() {
    const v = input.trim();
    if (!v) return;
    if (addrs.includes(v)) { setError("already added"); return; }
    if (addrs.length >= 5) { setError("max 5 tokens"); return; }
    try {
      await api(`/tokens/${v}`);
      setAddrs([...addrs, v]); setInput(""); setError("");
    } catch { setError("token not tracked — copy the address from a token page"); }
  }

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
        <p className="text-sm text-dim mt-1">Side-by-side intelligence: momentum vs distribution vs structure. Paste contract addresses (up to 5) — use a token page's address or the watchlist.</p>
        <div className="flex gap-2 mt-3">
          <input value={input} onChange={(e) => setInput(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && addAddress()}
                 placeholder="paste Solana/BSC address…"
                 className="flex-1 bg-bg/60 border border-line rounded-lg px-3 py-2 text-sm num outline-none focus:border-cyanx" />
          <button className="btn btn-primary" onClick={addAddress}>+ Add</button>
        </div>
        {error && <div className="text-xs text-redx mt-1.5">⚠ {error}</div>}
        {addrs.length > 0 && (
          <div className="flex gap-1.5 flex-wrap mt-2">
            {addrs.map((a) => (
              <span key={a} className="badge badge-gray num text-2xs">
                {a.slice(0, 5)}…{a.slice(-4)}
                <button className="ml-1.5 text-redx" onClick={() => setAddrs(addrs.filter((x) => x !== a))}>✕</button>
              </span>
            ))}
          </div>
        )}
      </div>

      {!qs && <Empty text="Add at least two token addresses to compare." />}
      {loading && qs && <Empty text="Comparing…" />}
      {missing.length > 0 && <div className="text-xs text-amberx">⚠ not tracked: {missing.map((m) => m.slice(0, 8) + "…").join(", ")}</div>}

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

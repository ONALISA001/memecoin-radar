"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import { Empty, Panel, SkeletonRows, TokenAvatar } from "@/components/ui";
import { fmtPct, fmtPrice, fmtUsd, pctColor } from "@/lib/format";

const TABS: [string, string, string][] = [
  ["new", "⚡ New", "Every coin the moment it is created, newest first."],
  ["bonding", "📈 Bonding", "Still on the pump.fun curve with trading; closest to graduating first."],
  ["graduated", "🎓 Graduated", "Just moved from the curve to a DEX (PumpSwap, Raydium)."],
  ["hot", "🔥 Hot", "Under 6h old, busy, mostly buyers, $8K+ market cap and no severe launch flags. These also go to full scoring."],
];
const MCAPS: [string, string][] = [["0", "Any mcap"], ["5000", "≥ $5K"], ["20000", "≥ $20K"], ["50000", "≥ $50K"], ["100000", "≥ $100K"]];
const TFS = ["1m", "5m", "15m", "1h"];

function ago(secs: number): string {
  if (secs < 60) return `${Math.max(0, secs)}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
  return `${Math.floor(secs / 86400)}d`;
}

function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

function Chg({ v, label }: { v: number | null | undefined; label: string }) {
  return (
    <div className="text-right">
      <div className="text-2xs text-dim">{label}</div>
      <div className={`num text-xs ${pctColor(v)}`}>{v == null ? "—" : fmtPct(v)}</div>
    </div>
  );
}

function BuySell({ b, s }: { b: number; s: number }) {
  const tot = b + s;
  const share = tot ? (b / tot) * 100 : 50;
  return (
    <div className="w-24">
      <div className="flex justify-between text-2xs num"><span className="text-greenx">{b}</span><span className="text-redx">{s}</span></div>
      <div className="h-1.5 rounded-full bg-redx/40 overflow-hidden" title={`${b} buys / ${s} sells in the last hour`}>
        <div className="h-full bg-greenx" style={{ width: `${share}%` }} />
      </div>
    </div>
  );
}

function Curve({ p, graduated }: { p: number | null; graduated: boolean }) {
  if (graduated) return <span className="badge badge-green !text-2xs">🎓 graduated</span>;
  if (p == null) return <span className="text-2xs text-dim">curve —</span>;
  return (
    <div className="w-20" title={`${p}% of the bonding curve sold; at 100% it graduates to a DEX`}>
      <div className="text-2xs text-dim num">curve {Math.round(p)}%</div>
      <div className="h-1.5 rounded-full bg-line overflow-hidden"><div className="h-full bg-cyanx" style={{ width: `${p}%` }} /></div>
    </div>
  );
}

function Candles({ mint }: { mint: string }) {
  const [tf, setTf] = useState("1m");
  const { data, loading } = useApi(`/launches/${mint}/candles?tf=${tf}`, 30000);
  const cs: any[] = data?.candles || [];
  const W = 640, H = 220, VOL_H = 40, PAD_R = 62;
  const view = useMemo(() => {
    if (!cs.length) return null;
    const hi = Math.max(...cs.map((c) => c.h)), lo = Math.min(...cs.map((c) => c.l));
    const vmax = Math.max(...cs.map((c) => c.v), 1);
    const span = hi - lo || hi * 0.01 || 1;
    const plotW = W - PAD_R, plotH = H - VOL_H - 8;
    const step = plotW / cs.length;
    const y = (v: number) => 4 + (1 - (v - lo) / span) * plotH;
    return { hi, lo, vmax, step, y, plotW, plotH };
  }, [cs]);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1">
        {TFS.map((t) => (
          <button key={t} onClick={() => setTf(t)}
                  className={`pill-mini ${tf === t ? "!text-cyanx !border-cyanx/60" : "text-dim border-line"}`}>{t}</button>
        ))}
        {data?.candles?.length > 0 && (() => {
          const first = cs[0].o, last = cs[cs.length - 1].c;
          const ch = first ? ((last - first) / first) * 100 : null;
          return <span className={`ml-2 text-xs num ${pctColor(ch)}`}>{fmtPct(ch)} over this chart</span>;
        })()}
      </div>
      {loading && !data ? <div className="h-[220px] rounded bg-line/20 animate-pulse" /> : !view ? (
        <div className="h-24 flex items-center justify-center text-sm text-dim">{data?.note || "No candles yet."}</div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={`${tf} price candles`}>
          {[0, 0.5, 1].map((f) => {
            const v = view.lo + (view.hi - view.lo) * (1 - f);
            const yy = view.y(v);
            return (
              <g key={f}>
                <line x1={0} x2={view.plotW} y1={yy} y2={yy} stroke="currentColor" className="text-line" strokeDasharray="3 4" />
                <text x={view.plotW + 4} y={yy + 3} fontSize="10" className="fill-dim">${fmtPrice(v)}</text>
              </g>
            );
          })}
          {cs.map((c, i) => {
            const up = c.c >= c.o;
            const x = i * view.step + view.step / 2;
            const bw = Math.max(1, view.step * 0.65);
            const top = view.y(Math.max(c.o, c.c)), bot = view.y(Math.min(c.o, c.c));
            const color = up ? "#34D399" : "#F87171";
            const vh = (c.v / view.vmax) * (VOL_H - 4);
            return (
              <g key={c.t}>
                <line x1={x} x2={x} y1={view.y(c.h)} y2={view.y(c.l)} stroke={color} strokeWidth={1} />
                <rect x={x - bw / 2} y={top} width={bw} height={Math.max(1, bot - top)} fill={color} />
                <rect x={x - bw / 2} y={H - vh} width={bw} height={vh} fill={color} opacity={0.35} />
                <title>{new Date(c.t * 1000).toLocaleTimeString()} · O {fmtPrice(c.o)} H {fmtPrice(c.h)} L {fmtPrice(c.l)} C {fmtPrice(c.c)} · vol {fmtUsd(c.v, 0)}</title>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

function LaunchRow({ r, now, open, onToggle }: { r: any; now: number; open: boolean; onToggle: () => void }) {
  const age = Math.floor(now - r.created_ts);
  const severe = (r.flags || []).some((f: any) => f.severe);
  return (
    <div className={`border-b border-line/50 last:border-0 ${open ? "bg-line/10" : ""}`}>
      <button onClick={onToggle} aria-expanded={open}
              className="w-full text-left flex items-center gap-3 px-4 py-2.5 hover:bg-line/10">
        <TokenAvatar url={r.image_url} symbol={r.symbol} size={30} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="font-semibold text-sm truncate">${r.symbol || "?"}</span>
            <span className={`text-2xs num ${age < 600 ? "text-greenx" : "text-dim"}`}>{ago(age)}</span>
            {r.hot_score != null && <span className="badge badge-amber !text-2xs">🔥 {Math.round(r.hot_score)}</span>}
            {severe && <span className="badge badge-red !text-2xs">⚠ risky</span>}
          </div>
          <div className="text-2xs text-dim truncate">{r.name}</div>
        </div>
        <div className="text-right w-20">
          <div className="text-2xs text-dim">mcap</div>
          <div className="num text-sm">{r.mcap_usd ? fmtUsd(r.mcap_usd, 0) : "—"}</div>
        </div>
        <div className="text-right w-16 hidden sm:block">
          <div className="text-2xs text-dim">vol 1h</div>
          <div className="num text-xs">{r.vol_h1 ? fmtUsd(r.vol_h1, 0) : "—"}</div>
        </div>
        <div className="hidden md:flex gap-3">
          <Chg v={r.chg_m5} label="5m" /><Chg v={r.chg_h1} label="1h" /><Chg v={r.chg_h6} label="6h" /><Chg v={r.chg_h24} label="24h" />
        </div>
        <div className="hidden lg:block"><BuySell b={r.buys_h1 || 0} s={r.sells_h1 || 0} /></div>
        <div className="hidden sm:block"><Curve p={r.progress} graduated={r.graduated} /></div>
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3">
          <div className="flex md:hidden gap-4">
            <Chg v={r.chg_m5} label="5m" /><Chg v={r.chg_h1} label="1h" /><Chg v={r.chg_h6} label="6h" /><Chg v={r.chg_h24} label="24h" />
            <BuySell b={r.buys_h1 || 0} s={r.sells_h1 || 0} />
          </div>
          {r.has_market_data ? <Candles mint={r.mint} /> : (
            <div className="text-sm text-dim">Market data and candles appear a minute or two after the first trades.</div>
          )}
          <div className="flex flex-wrap gap-1.5 text-2xs">
            <span className="badge !text-2xs">creator bought {r.dev_buy_pct}% at launch</span>
            {r.liq_usd ? <span className="badge !text-2xs">liquidity {fmtUsd(r.liq_usd, 0)}</span> : null}
            {r.price_usd ? <span className="badge !text-2xs">price ${fmtPrice(r.price_usd)}</span> : null}
            <span className="badge !text-2xs">{r.dex}</span>
            {(r.flags || []).map((f: any) => (
              <span key={f.id} className={`badge ${f.severe ? "badge-red" : "badge-amber"} !text-2xs`}>⚠ {f.label}</span>
            ))}
            {!r.flags?.length && <span className="badge badge-green !text-2xs">✓ no launch flags</span>}
          </div>
          <div className="flex flex-wrap gap-3 text-xs">
            <Link href={`/token/${r.mint}`} className="text-cyanx hover:underline">Full analysis →</Link>
            <a href={`https://dexscreener.com/solana/${r.mint}`} target="_blank" rel="noopener noreferrer" className="text-dim hover:text-cyanx">DexScreener ↗</a>
            <a href={`https://solscan.io/token/${r.mint}`} target="_blank" rel="noopener noreferrer" className="text-dim hover:text-cyanx">Solscan ↗</a>
            <span className="text-dim num select-all truncate max-w-full">{r.mint}</span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function LaunchRadarPage() {
  const [tab, setTab] = useState("new");
  const [mcap, setMcap] = useState("0");
  const [paused, setPaused] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const now = useNow();
  const { data, loading } = useApi(paused ? null : `/launches?tab=${tab}&limit=80&min_mcap=${mcap}`, 5000);
  const [frozen, setFrozen] = useState<any>(null);
  useEffect(() => { if (data && !paused) setFrozen(data); }, [data, paused]);
  const shown = frozen || data;
  const items: any[] = shown?.items || [];
  const st = shown?.status;
  const c = shown?.counts;
  const live = st?.connected && (st?.seconds_since_event ?? 999) < 120;
  return (
    <div className="space-y-4">
      <div className="panel p-4 space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-extrabold">⚡ Launch radar</h1>
          <span className={`inline-flex items-center gap-1.5 text-xs ${live ? "text-greenx" : "text-amberx"}`}>
            <span className={`h-2 w-2 rounded-full ${live ? "bg-greenx animate-pulse" : "bg-amberx"}`} />
            {!st ? "connecting…" : !st.enabled ? "off on this server" : live ? "live" : "reconnecting…"}
          </span>
          {c && <span className="text-xs text-dim num">{c.launched_1h} launched · {c.graduated_1h} graduated in the last hour</span>}
          {st?.sol_usd && <span className="text-xs text-dim num">SOL ${st.sol_usd}</span>}
          <div className="flex-1" />
          <select className="btn !py-1 bg-panel" value={mcap} onChange={(e) => setMcap(e.target.value)} aria-label="Minimum market cap">
            {MCAPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <button className="btn !py-1" onClick={() => setPaused(!paused)} aria-pressed={paused}>
            {paused ? "▶ Resume" : "⏸ Pause"}
          </button>
        </div>
        <p className="text-xs text-dim leading-relaxed">
          Coins appear here seconds after they are created on pump.fun, before they reach a DEX. Most new launches fail
          within hours: open a coin to see its candles and launch checks, and use Full analysis for the risk score
          and verdict. Research only, not a buy signal.
        </p>
      </div>
      <Panel
        title={<div className="flex gap-1 flex-wrap" role="tablist">
          {TABS.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => { setTab(k); setOpen(null); setFrozen(null); }}
                    className={`pill-mini ${tab === k ? "!text-cyanx !border-cyanx/60" : "text-dim border-line"}`}>{l}</button>
          ))}
        </div>}
        right={<span className="text-2xs text-dim num">{shown ? `${shown.total} coins` : ""}{paused ? " · paused" : ""}</span>}>
        <div className="px-4 py-1.5 text-2xs text-dim border-b border-line/50">{TABS.find((t) => t[0] === tab)?.[2]}</div>
        {loading && !shown ? <SkeletonRows rows={6} /> : items.length === 0 ? (
          <Empty text={st && !st.enabled ? "The launch radar is turned off on this server (LAUNCH_STREAM_ENABLED=0)."
            : tab === "new" ? "Waiting for the first launches… new coins usually arrive every few seconds."
            : "Nothing here yet. This fills in as launches trade; try the New tab."} />
        ) : (
          <div>
            {items.map((r) => (
              <LaunchRow key={r.mint} r={r} now={now} open={open === r.mint}
                         onToggle={() => setOpen(open === r.mint ? null : r.mint)} />
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

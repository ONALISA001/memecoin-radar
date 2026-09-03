"use client";
import React from "react";
import { fomoBandClass, riskTextClass, trendColor } from "@/lib/format";

export function Badge({ kind, children }: { kind: string; children: React.ReactNode }) {
  return <span className={`badge badge-${kind}`}>{children}</span>;
}

export function ScorePill({ value, max = 100, color }: { value: number | null; max?: number; color: string }) {
  const v = value ?? 0;
  return (
    <div className="flex items-center gap-1.5 justify-end">
      <span className="num font-semibold text-[13px]" style={{ color }}>{value === null ? "—" : Math.round(v)}</span>
      <div className="scorebar w-9"><div style={{ width: `${Math.min(100, (v / max) * 100)}%`, background: color }} /></div>
    </div>
  );
}

export function TrendPill({ value }: { value: number | null }) {
  return <ScorePill value={value} color={trendColor(value ?? 0)} />;
}

export function FomoPill({ value, cls }: { value: number | null; cls?: string }) {
  const v = value ?? 0;
  const color = v >= 81 ? "#F87171" : v >= 61 ? "#FB923C" : v >= 41 ? "#FBBF24" : "#64748B";
  return (
    <div className="flex flex-col items-end gap-0.5">
      <ScorePill value={value} color={color} />
      {cls && v >= 41 && <span className={`badge ${fomoBandClass(cls)}`} style={{ fontSize: 8 }}>{cls}</span>}
    </div>
  );
}

export function RiskPill({ value, cls }: { value: number | null; cls?: string }) {
  return (
    <div className="flex flex-col items-end gap-0.5">
      <span className={`num font-semibold text-[13px] ${riskTextClass(value ?? 0)}`}>
        {value === null ? "—" : Math.round(value)}
      </span>
      {cls && <span className="text-[9px] uppercase tracking-wider text-dim">{cls}</span>}
    </div>
  );
}

/** SVG ring gauge for the token page. */
export function Gauge({ value, label, color, sub }: { value: number; label: string; color: string; sub?: React.ReactNode }) {
  const r = 34, c = 2 * Math.PI * r;
  const pct = Math.min(100, Math.max(0, value)) / 100;
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative w-[92px] h-[92px]">
        <svg viewBox="0 0 84 84" className="w-full h-full -rotate-90">
          <circle cx="42" cy="42" r={r} fill="none" stroke="#182130" strokeWidth="7" />
          <circle cx="42" cy="42" r={r} fill="none" stroke={color} strokeWidth="7"
                  strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - pct)} />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="num text-2xl font-bold" style={{ color }}>{Math.round(value)}</span>
        </div>
      </div>
      <span className="text-[11px] font-bold tracking-wider text-ink uppercase">{label}</span>
      {sub}
    </div>
  );
}

export function Sparkline({ points, w = 76, h = 22, color = "#22D3EE", fill = true }: {
  points: number[]; w?: number; h?: number; color?: string; fill?: boolean;
}) {
  if (!points || points.length < 2) return <div style={{ width: w, height: h }} />;
  const min = Math.min(...points), max = Math.max(...points);
  const span = max - min || 1;
  const step = w / (points.length - 1);
  const path = points.map((p, i) =>
    `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - 2 - ((p - min) / span) * (h - 4)).toFixed(1)}`).join(" ");
  const area = `${path} L${w},${h} L0,${h} Z`;
  return (
    <svg width={w} height={h} className="shrink-0">
      {fill && <path d={area} fill={color} opacity="0.12" />}
      <path d={path} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

export function ConfChip({ level }: { level?: string }) {
  if (!level) return null;
  const kind = level === "HIGH" ? "green" : level === "MEDIUM" ? "amber" : "gray";
  return <Badge kind={kind}>conf: {level}</Badge>;
}

export function TokenAvatar({ url, symbol, size = 24 }: { url?: string | null; symbol?: string; size?: number }) {
  const [err, setErr] = React.useState(!url);
  if (!err && url) {
    return <img src={url} alt={symbol || ""} width={size} height={size}
                className="rounded-full shrink-0 object-cover" style={{ width: size, height: size }}
                onError={() => setErr(true)} />;
  }
  const hue = ((symbol || "?").charCodeAt(0) * 47) % 360;
  return (
    <div className="rounded-full shrink-0 flex items-center justify-center font-bold text-[10px]"
         style={{ width: size, height: size, background: `hsl(${hue} 40% 16%)`, color: `hsl(${hue} 90% 65%)` }}>
      {(symbol || "??").slice(0, 2).toUpperCase()}
    </div>
  );
}

export function RowBadges({ item }: { item: any }) {
  return (
    <div className="flex gap-1 flex-wrap">
      {item.emerging && <Badge kind="violet">early</Badge>}
      {item.trend >= 65 && <Badge kind="cyan">trending</Badge>}
      {item.fomo >= 81 && <Badge kind="red">extreme fomo</Badge>}
      {(item.anomalies || []).length > 0 && <Badge kind="amber">anomaly</Badge>}
      {item.risk >= 70 && <Badge kind="red">high risk</Badge>}
      {item.conflicts > 0 && <Badge kind="amber">conflict</Badge>}
      {item.regime === "distribution" && <Badge kind="red">distribution</Badge>}
      {typeof item.organic === "number" && item.organic < 40 && <Badge kind="amber">manufactured?</Badge>}
    </div>
  );
}

export function LivePulse() {
  return (
    <span className="flex items-center gap-1.5 text-[10px] font-bold tracking-wider text-greenx">
      <span className="live-dot" /> LIVE
    </span>
  );
}

export function Empty({ text }: { text: string }) {
  return <div className="py-10 text-center text-dim text-xs">{text}</div>;
}

export function Panel({ title, right, children, className = "" }: {
  title?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <div className={`panel ${className}`}>
      {title && (
        <div className="panel-hd">
          <span className="panel-title">{title}</span>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function fmtUsd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}K`;
  return `${sign}$${a.toFixed(digits)}`;
}

export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v <= 0) return "—";
  if (v >= 1) return `$${v.toFixed(4)}`;
  if (v >= 0.0001) return `$${v.toFixed(7)}`;
  return `$${v.toExponential(2)}`;
}

export function fmtPct(v: number | null | undefined, signed = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = signed && v > 0 ? "+" : "";
  return `${s}${v.toFixed(v >= 100 || v <= -100 ? 0 : 1)}%`;
}

export function pctColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "text-dim";
  return v > 0 ? "text-greenx" : v < 0 ? "text-redx" : "text-dim";
}

export function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return `${Math.round(v)}`;
}

export function timeAgo(ts: number): string {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return `${Math.max(1, Math.floor(d))}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

export function riskColor(risk: number): string {
  if (risk >= 70) return "#F87171";
  if (risk >= 50) return "#FB923C";
  if (risk >= 30) return "#FBBF24";
  return "#34D399";
}

export function riskTextClass(risk: number): string {
  if (risk >= 70) return "text-redx";
  if (risk >= 50) return "text-orange-400";
  if (risk >= 30) return "text-amberx";
  return "text-greenx";
}

export function fomoBandClass(cls: string): string {
  switch (cls) {
    case "EXTREME FOMO": return "badge-red";
    case "STRONG FOMO": return "badge-amber";
    case "MOMENTUM": return "badge-cyan";
    case "EMERGING": return "badge-violet";
    default: return "badge-gray";
  }
}

export function trendColor(t: number): string {
  if (t >= 70) return "#22D3EE";
  if (t >= 50) return "#60A5FA";
  if (t >= 35) return "#A78BFA";
  return "#64748B";
}

export function ageLabel(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || hours > 9000) return "—";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`;
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

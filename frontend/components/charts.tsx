"use client";
import React, { useState } from "react";
import { Area, AreaChart, Bar, CartesianGrid, Cell, ComposedChart, Line,
         ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "@/lib/api";
import { fmtNum, fmtUsd } from "@/lib/format";

const TFS = ["15m", "1h", "4h", "24h", "7d"] as const;

const tooltipStyle = {
  background: "#0C1017", border: "1px solid #1B2331", borderRadius: 8,
  fontSize: 11, color: "#DCE4EE",
};

function fmtT(t: number, long: boolean) {
  const d = new Date(t * 1000);
  return long
    ? d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function TfBar({ tf, setTf }: { tf: string; setTf: (v: string) => void }) {
  return (
    <div className="flex gap-0.5">
      {TFS.map((v) => (
        <button key={v} onClick={() => setTf(v)}
                className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${tf === v ? "bg-cyanx/15 text-cyanx" : "text-dim hover:text-ink"}`}>
          {v}
        </button>
      ))}
    </div>
  );
}

function ChartShell({ title, addr, metric, sub, children }: {
  title: string; addr: string; metric: string; sub?: React.ReactNode;
  children: (points: any[], tf: string) => React.ReactNode;
}) {
  const [tf, setTf] = useState<string>("24h");
  const { data, loading } = useApi(`/tokens/${addr}/history?metric=${metric}&tf=${tf}`, 30000);
  const pts: any[] = data?.points || [];
  return (
    <div className="panel">
      <div className="panel-hd">
        <span className="panel-title flex items-center gap-2">{title}{sub}</span>
        <TfBar tf={tf} setTf={setTf} />
      </div>
      <div className="h-[190px] px-2 py-2">
        {loading && pts.length === 0 ? (
          <div className="h-full flex items-center justify-center text-dim text-xs">Loading…</div>
        ) : pts.length < 2 ? (
          <div className="h-full flex items-center justify-center text-dim text-xs">Collecting data…</div>
        ) : children(pts, tf)}
      </div>
    </div>
  );
}

const fmtUsdTick = (v: number) => fmtUsd(v, 0);

export function PriceChart({ addr }: { addr: string }) {
  return (
    <ChartShell title="Price" addr={addr} metric="price">
      {(pts, tf) => {
        const chg = pts.length > 1 ? ((pts[pts.length - 1].price / (pts[0].price || 1e-12) - 1) * 100) : 0;
        const color = chg >= 0 ? "#34D399" : "#F87171";
        return (
          <ResponsiveContainer>
            <AreaChart data={pts}>
              <defs>
                <linearGradient id="pg" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.3} />
                  <stop offset="100%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#141B27" vertical={false} />
              <XAxis dataKey="t" tickFormatter={(t) => fmtT(t, tf === "7d")} stroke="#3B4A61" tickLine={false} axisLine={false} minTickGap={48} />
              <YAxis dataKey="price" stroke="#3B4A61" tickLine={false} axisLine={false} width={72}
                     tickFormatter={(v) => `$${v.toExponential(1)}`} domain={["dataMin", "dataMax"]} />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={(t: any) => fmtT(t as number, true)}
                       formatter={(v: any) => [`$${Number(v).toExponential(3)}`, "price"]} />
              <Area type="monotone" dataKey="price" stroke={color} strokeWidth={1.8} fill="url(#pg)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        );
      }}
    </ChartShell>
  );
}

export function VolumeChart({ addr }: { addr: string }) {
  return (
    <ChartShell title="Volume per bucket" addr={addr} metric="volume">
      {(pts, tf) => (
        <ResponsiveContainer>
          <ComposedChart data={pts}>
            <CartesianGrid stroke="#141B27" vertical={false} />
            <XAxis dataKey="t" tickFormatter={(t) => fmtT(t, tf === "7d")} stroke="#3B4A61" tickLine={false} axisLine={false} minTickGap={48} />
            <YAxis yAxisId="v" stroke="#3B4A61" tickLine={false} axisLine={false} width={56} tickFormatter={fmtUsdTick} />
            <YAxis yAxisId="n" orientation="right" stroke="#3B4A61" tickLine={false} axisLine={false} width={44} tickFormatter={fmtNum} />
            <Tooltip contentStyle={tooltipStyle} labelFormatter={(t: any) => fmtT(t as number, true)}
                     formatter={(v: any, name: any) => [name === "volume" ? fmtUsd(Number(v)) : v, name]} />
            <Bar yAxisId="v" dataKey="volume" fill="#2DD4BF" opacity={0.75} radius={[2, 2, 0, 0]} />
            <Line yAxisId="n" type="monotone" dataKey="txns" stroke="#A78BFA" strokeWidth={1.3} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </ChartShell>
  );
}

export function SocialChart({ addr }: { addr: string }) {
  return (
    <ChartShell title="Social activity" addr={addr} metric="social"
                sub={<span className="badge badge-amber !text-[8px] ml-2">simulated</span>}>
      {(pts, tf) => (
        <ResponsiveContainer>
          <ComposedChart data={pts}>
            <CartesianGrid stroke="#141B27" vertical={false} />
            <XAxis dataKey="t" tickFormatter={(t) => fmtT(t, tf === "7d")} stroke="#3B4A61" tickLine={false} axisLine={false} minTickGap={48} />
            <YAxis stroke="#3B4A61" tickLine={false} axisLine={false} width={44} tickFormatter={fmtNum} />
            <Tooltip contentStyle={tooltipStyle} labelFormatter={(t: any) => fmtT(t as number, true)} />
            <Area type="monotone" dataKey="mentions" stroke="#22D3EE" fill="#22D3EE22" strokeWidth={1.6} dot={false} />
            <Line type="monotone" dataKey="engagement" stroke="#FBBF24" strokeWidth={1.1} dot={false} />
            <Line type="monotone" dataKey="uniques" stroke="#34D399" strokeWidth={1.1} dot={false} strokeDasharray="3 3" />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </ChartShell>
  );
}

export function HoldersChart({ addr }: { addr: string }) {
  return (
    <ChartShell title="Holders" addr={addr} metric="holders"
                sub={<span className="badge badge-green !text-[8px] ml-2">real · Helius · checked ~every 6h</span>}>
      {(pts, tf) => (
        <ResponsiveContainer>
          <ComposedChart data={pts}>
            <CartesianGrid stroke="#141B27" vertical={false} />
            <XAxis dataKey="t" tickFormatter={(t) => fmtT(t, tf === "7d")} stroke="#3B4A61" tickLine={false} axisLine={false} minTickGap={48} />
            <YAxis yAxisId="h" stroke="#3B4A61" tickLine={false} axisLine={false} width={44} tickFormatter={fmtNum} domain={["dataMin", "dataMax"]} />
            <Tooltip contentStyle={tooltipStyle} labelFormatter={(t: any) => fmtT(t as number, true)} />
            <Area yAxisId="h" type="monotone" dataKey="holders" stroke="#34D399" fill="#34D39922" strokeWidth={1.6} dot />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </ChartShell>
  );
}

export function LiquidityChart({ addr }: { addr: string }) {
  return (
    <ChartShell title="Liquidity" addr={addr} metric="liquidity">
      {(pts, tf) => (
        <ResponsiveContainer>
          <AreaChart data={pts}>
            <CartesianGrid stroke="#141B27" vertical={false} />
            <XAxis dataKey="t" tickFormatter={(t) => fmtT(t, tf === "7d")} stroke="#3B4A61" tickLine={false} axisLine={false} minTickGap={48} />
            <YAxis stroke="#3B4A61" tickLine={false} axisLine={false} width={64} tickFormatter={fmtUsdTick} domain={["dataMin", "dataMax"]} />
            <Tooltip contentStyle={tooltipStyle} labelFormatter={(t: any) => fmtT(t as number, true)}
                     formatter={(v: any) => [fmtUsd(Number(v)), "liquidity"]} />
            <Area type="monotone" dataKey="liquidity" stroke="#38BDF8" fill="#38BDF822" strokeWidth={1.6} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </ChartShell>
  );
}

export function ScoreChart({ addr }: { addr: string }) {
  return (
    <ChartShell title="Score history (live)" addr={addr} metric="score">
      {(pts, tf) => (
        <ResponsiveContainer>
          <ComposedChart data={pts}>
            <CartesianGrid stroke="#141B27" vertical={false} />
            <XAxis dataKey="t" tickFormatter={(t) => fmtT(t, tf === "7d")} stroke="#3B4A61" tickLine={false} axisLine={false} minTickGap={48} />
            <YAxis stroke="#3B4A61" tickLine={false} axisLine={false} width={32} domain={[0, 100]} />
            <Tooltip contentStyle={tooltipStyle} labelFormatter={(t: any) => fmtT(t as number, true)} />
            <ReferenceLine y={75} stroke="#22D3EE55" strokeDasharray="4 4" />
            <ReferenceLine y={80} stroke="#F8717155" strokeDasharray="4 4" />
            <Line type="monotone" dataKey="trend" stroke="#22D3EE" strokeWidth={1.8} dot={false} />
            <Line type="monotone" dataKey="fomo" stroke="#FBBF24" strokeWidth={1.3} dot={false} />
            <Line type="monotone" dataKey="risk" stroke="#F87171" strokeWidth={1.3} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </ChartShell>
  );
}

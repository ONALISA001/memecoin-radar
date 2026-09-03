"use client";
import { useState } from "react";
import { apiDelete, apiPatch, apiPost, useApi } from "@/lib/api";
import { Badge, Empty, Panel } from "@/components/ui";
import { timeAgo } from "@/lib/format";

const METRICS: [string, string][] = [
  ["trend_score", "Trend Score"], ["fomo_score", "FOMO Score"], ["risk_score", "Risk Score"],
  ["mentions_growth_pct", "Social mentions growth %"], ["volume_growth_pct", "Volume growth %"],
  ["holder_growth_pct", "Holder growth %"], ["liquidity_drop_pct", "Liquidity drop %"],
  ["new_narrative", "New narrative emerges"],
];
const OPS: [string, string][] = [["gt", ">"], ["gte", "≥"], ["lt", "<"], ["lte", "≤"]];

function CreateAlert({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("trend_score");
  const [op, setOp] = useState("gt");
  const [value, setValue] = useState("75");
  const [scope, setScope] = useState("all");
  const [address, setAddress] = useState("");
  const [channel, setChannel] = useState("browser");
  const [webhook, setWebhook] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit() {
    setBusy(true); setErr("");
    try {
      await apiPost("/alerts", {
        name: name || `${metric} ${op} ${value}`, metric, op, value: parseFloat(value),
        scope, address: scope === "address" ? address : undefined,
        channel_type: channel, webhook_url: channel === "webhook" ? webhook : undefined,
      });
      setName(""); onCreated();
      location.reload();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <Panel title="Create alert">
      <div className="p-4 space-y-3">
        <input className="inp w-full" placeholder='Name, e.g. "Strong trend detected"' value={name}
               onChange={(e) => setName(e.target.value)} />
        <div className="grid grid-cols-2 gap-2">
          <div className="col-span-2">
            <label className="text-[10px] text-dim font-bold tracking-wider">CONDITION</label>
            <div className="flex gap-2 mt-1">
              <select className="sel flex-1" value={metric} onChange={(e) => setMetric(e.target.value)}>
                {METRICS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <select className="sel w-16" value={op} onChange={(e) => setOp(e.target.value)}>
                {OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              {metric !== "new_narrative" && (
                <input className="inp w-20" value={value} onChange={(e) => setValue(e.target.value)} />
              )}
            </div>
          </div>
          <div>
            <label className="text-[10px] text-dim font-bold tracking-wider">SCOPE</label>
            <select className="sel w-full mt-1" value={scope} onChange={(e) => setScope(e.target.value)}
                    disabled={metric === "new_narrative"}>
              <option value="all">all tokens</option>
              <option value="watchlist">watchlist only</option>
              <option value="address">specific token</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] text-dim font-bold tracking-wider">CHANNEL</label>
            <select className="sel w-full mt-1" value={channel} onChange={(e) => setChannel(e.target.value)}>
              <option value="browser">browser (bell)</option>
              <option value="webhook">webhook (Discord/Slack)</option>
            </select>
          </div>
          {scope === "address" && (
            <input className="inp col-span-2" placeholder="token contract address"
                   value={address} onChange={(e) => setAddress(e.target.value)} />
          )}
          {channel === "webhook" && (
            <input className="inp col-span-2" placeholder="https://discord.com/api/webhooks/…"
                   value={webhook} onChange={(e) => setWebhook(e.target.value)} />
          )}
        </div>
        {err && <div className="text-[11px] text-redx">{err}</div>}
        <button className="btn btn-primary w-full justify-center" onClick={submit} disabled={busy}>
          {busy ? "Creating…" : "Create alert"}
        </button>
        <p className="text-[10px] text-dim leading-relaxed">
          Evaluated every ingestion tick (60s) with a 30-minute per-token cooldown.
          Telegram &amp; email channels are Phase-5 adapters on the same interface.
        </p>
      </div>
    </Panel>
  );
}

export default function AlertsPage() {
  const { data, loading } = useApi("/alerts", 30000);
  const items = data?.items || [];
  async function toggle(a: any) { await apiPatch(`/alerts/${a.id}`, { enabled: !a.enabled }); location.reload(); }
  async function remove(a: any) { await apiDelete(`/alerts/${a.id}`); location.reload(); }
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
      <Panel title={`Configured alerts (${items.length})`} className="lg:col-span-2">
        {loading && items.length === 0 ? <Empty text="Loading…" /> :
         items.length === 0 ? <Empty text="No alerts configured." /> : (
          <div className="divide-y divide-line/60">
            {items.map((a: any) => (
              <div key={a.id} className="px-4 py-3 flex items-center gap-3 hover:bg-panel2/60">
                <button onClick={() => toggle(a)}
                        className={`w-9 h-5 rounded-full relative transition-colors shrink-0 ${a.enabled ? "bg-cyanx/70" : "bg-line"}`}>
                  <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${a.enabled ? "left-[18px]" : "left-0.5"}`} />
                </button>
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-[13px]">{a.name}</div>
                  <div className="text-[11px] text-dim font-mono">
                    when {a.condition?.metric} {a.condition?.op} {a.condition?.value}
                    {" · "}{a.condition?.scope}{a.condition?.address ? ` (${a.condition.address.slice(0, 8)}…)` : ""}
                    {" · "}{a.channel?.type}{a.has_webhook ? " 🔗" : ""}
                  </div>
                </div>
                <Badge kind={a.enabled ? "green" : "gray"}>{a.enabled ? "live" : "paused"}</Badge>
                <button className="btn btn-danger !py-1 !px-2 !text-[10px]" onClick={() => remove(a)}>delete</button>
              </div>
            ))}
          </div>
        )}
        <div className="px-4 py-3 border-t border-line/60">
          <AlertFeed />
        </div>
      </Panel>
      <CreateAlert onCreated={() => {}} />
    </div>
  );
}

function AlertFeed() {
  const { data } = useApi("/alerts/events", 20000);
  const items = (data?.items || []).slice(0, 12);
  return (
    <div>
      <div className="text-[10px] font-bold tracking-widest text-dim uppercase mb-2">Recent events</div>
      {items.length === 0 && <div className="text-xs text-dim py-2">None yet.</div>}
      <div className="space-y-1.5">
        {items.map((ev: any) => {
          const p = ev.payload || {};
          return (
            <div key={ev.id} className="text-[11px] flex items-center gap-2 flex-wrap">
              <span className="text-dim shrink-0 w-16">{timeAgo(ev.ts)}</span>
              <span className="font-semibold text-ink/85">{p.alert}</span>
              {p.token && <span className="text-cyanx">${p.token}</span>}
              <span className="text-dim">{p.trigger}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

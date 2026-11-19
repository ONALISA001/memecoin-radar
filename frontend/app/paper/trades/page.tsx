"use client";
import { Suspense, useState } from "react";
import { useApi } from "@/lib/api";
import { Panel } from "@/components/ui";
import { SimStrip, TradesTable } from "@/components/paper";

function TradesInner() {
  const [status, setStatus] = useState("all");
  const { data } = useApi(`/paper/trades?status=${status}&limit=300`, 20000);
  return (
    <div className="space-y-3">
      <SimStrip compact />
      <Panel title={<span>Trade Ledger <span className="panel-sub">— permanent virtual record (§9)</span></span>}
             right={
               <div className="flex gap-1 items-center">
                 {["all", "open", "closed"].map((v) => (
                   <button key={v} onClick={() => setStatus(v)}
                           className={`pill-mini ${status === v ? "!text-cyanx !border-cyanx/60" : ""}`}>{v}</button>
                 ))}
                 <a href="/api/paper/export?what=trades&fmt=csv" className="pill-mini !text-greenx !border-greenx/50 ml-1"
                    title="Download trade history as CSV (§24)">⬇ CSV</a>
                 <a href="/api/paper/export?what=trades&fmt=json" className="pill-mini">⬇ JSON</a>
               </div>
             }>
        <div className="px-3 py-1.5 text-2xs text-dim border-b border-line/40">
          {data?.total ?? 0} trades · {data?.note}
        </div>
        <TradesTable items={data?.items || []} />
      </Panel>
    </div>
  );
}

export default function TradesPage() {
  return <Suspense fallback={null}><TradesInner /></Suspense>;
}

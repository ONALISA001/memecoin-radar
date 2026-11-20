import { Suspense } from "react";
import { IntelFeed } from "@/components/intelfeed";

export const metadata = { title: "Intelligence — Memecoin Radar" };

export default function FeedPage() {
  return (
    <div className="max-w-7xl mx-auto px-3 py-4">
      <div className="mb-3">
        <h1 className="text-lg font-black tracking-tight">Real-Time Market Intelligence</h1>
        <p className="text-sm text-dim mt-0.5">
          Every event is detected from observed metrics — with importance, confidence, supporting checks and
          the contradiction to watch. Nothing here is a buy/sell recommendation.
        </p>
      </div>
      <Suspense fallback={<div className="text-dim text-xs p-6">loading intelligence…</div>}>
        <IntelFeed />
      </Suspense>
    </div>
  );
}

"use client";
import { timeAgo } from "@/lib/format";

/** One headline: publisher, age, sentiment tag, title linking out to the article. */
export function NewsRow({ a }: { a: { source: string; ts: number; title: string; url: string; sentiment?: number; symbols?: string[] } }) {
  const s = a.sentiment ?? 0;
  const tag = s > 0 ? ["bullish", "text-greenx"] : s < 0 ? ["bearish", "text-redx"] : ["neutral", "text-dim"];
  const real = a.url && !a.url.startsWith("https://mocknews.example/");
  return (
    <div className="px-4 py-3 border-b border-line/50">
      <div className="text-xs text-dim flex gap-2">
        <span className="text-cyanx">{a.source}</span><span>{timeAgo(a.ts)}</span>
        <span className={tag[1]}>{tag[0]}</span>
        {a.symbols?.map((x) => <span key={x} className="text-amberx">${x}</span>)}
      </div>
      {real
        ? <a href={a.url} target="_blank" rel="noopener noreferrer nofollow" className="text-sm text-ink/85 mt-0.5 block hover:underline">{a.title}</a>
        : <div className="text-sm text-ink/85 mt-0.5">{a.title}</div>}
    </div>
  );
}

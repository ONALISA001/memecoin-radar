import type { Metadata } from "next";
import "./globals.css";
import Shell from "@/components/shell";

export const metadata: Metadata = {
  title: "MemeCoin Radar — Memecoin Momentum, Sell-Pressure & On-Chain Intelligence",
  description: "Memecoin market-intelligence terminal: new token discovery, multi-factor momentum and sell-pressure scores, whale-flow signals, liquidity health, ATH position, volume acceleration and measured signal calibration. Analytics, not financial advice.",
  keywords: ["memecoin tracker", "new memecoin tracker", "memecoin screener", "memecoin momentum tracker", "memecoin analytics", "memecoin whale tracker", "memecoin volume tracker", "memecoin on-chain analytics", "memecoin liquidity tracker"],
  openGraph: {
    title: "See Memecoin Momentum Before the Crowd — MemeCoin Radar",
    description: "New launches, real-time momentum, whale-flow, liquidity health and distribution signals in one intelligence dashboard.",
    type: "website",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}

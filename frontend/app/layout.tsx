import type { Metadata } from "next";
import "./globals.css";
import Shell from "@/components/shell";

export const metadata: Metadata = {
  title: "MemeCoin Radar — AI Trend Intelligence & Risk Analysis",
  description: "Multi-source meme-coin trend intelligence: trend, FOMO and risk scores with explainable analytics. Not financial advice.",
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

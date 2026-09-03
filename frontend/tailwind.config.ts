import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#06080C",
        panel: "#0C1017",
        panel2: "#10151E",
        line: "#1B2331",
        ink: "#DCE4EE",
        dim: "#6B7A90",
        cyanx: "#22D3EE",
        greenx: "#34D399",
        amberx: "#FBBF24",
        redx: "#F87171",
        violetx: "#A78BFA",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
        sans: ["-apple-system", "Segoe UI", "Inter", "Roboto", "sans-serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;

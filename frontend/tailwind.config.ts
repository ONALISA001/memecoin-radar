import type { Config } from "tailwindcss";

/**
 * Design tokens — change colors and sizes HERE, not inline in components.
 * Type scale is intentionally small: 2xs (meta), xs (labels), sm (body/table),
 * base (emphasis), lg (section titles), xl/2xl (key numbers, page titles).
 */
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0B0E14",
        panel: "#121722",
        panel2: "#171D2A",
        line: "#252D3D",
        ink: "#E6ECF3",
        dim: "#8593A8",
        cyanx: "#22D3EE",
        greenx: "#34D399",
        amberx: "#FBBF24",
        redx: "#F87171",
        violetx: "#A78BFA",
      },
      fontSize: {
        "2xs": ["11px", "16px"],
        xs: ["12px", "16px"],
        sm: ["13px", "20px"],
        base: ["14px", "20px"],
        lg: ["16px", "24px"],
        xl: ["20px", "28px"],
        "2xl": ["24px", "32px"],
      },
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
        sans: ["Inter", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;

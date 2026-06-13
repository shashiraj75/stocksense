import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef2ff",
          500: "#6366f1",
          600: "#4f46e5",
          900: "#1e1b4b",
        },
        bull: "#22c55e",
        bear: "#ef4444",
        neutral: "#f59e0b",
        dark: {
          bg: "#0f1117",
          card: "#1a1d2e",
          border: "#2a2d3e",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;

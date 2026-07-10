import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  // Dummy values only — createBrowserClient() throws at import time without
  // them, and utils/api.ts imports the supabase client at module scope.
  // Tests never make a real Supabase call, so no real project credentials
  // are needed here.
  define: {
    "process.env.NEXT_PUBLIC_SUPABASE_URL": JSON.stringify("https://test.supabase.co"),
    "process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY": JSON.stringify("test-anon-key"),
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    exclude: ["node_modules/**", ".next/**"],
  },
});

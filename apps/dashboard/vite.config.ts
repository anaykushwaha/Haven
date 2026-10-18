import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The dashboard polls Haven's read-only JSON at /api/dashboard. In dev, Vite
// proxies /api to the MCP server so the browser sees one origin and Haven
// never needs CORS headers. Start the server with the API switched on:
//
//   python scripts/run_server.py --dashboard-api
//
// Override the target with VITE_HAVEN_PROXY_TARGET (for example when the
// server runs in Docker on another host).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const target = env.VITE_HAVEN_PROXY_TARGET || "http://127.0.0.1:8787";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": { target, changeOrigin: true },
      },
    },
  };
});

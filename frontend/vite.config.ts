import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// BACKEND_PORT / FRONTEND_PORT live in the project root .env (shared with the
// Python backend via config/settings.py), not frontend/.env.
export default defineConfig(({ mode }) => {
  const rootEnv = loadEnv(mode, path.resolve(__dirname, ".."), "");
  const backendPort = rootEnv.BACKEND_PORT || "8000";
  const frontendPort = Number(rootEnv.FRONTEND_PORT) || 5173;

  return {
    plugins: [react()],
    server: {
      port: frontendPort,
      // Required when the Vite dev server is reached through an ngrok hostname.
      allowedHosts: true,
      proxy: {
        "/api": {
          target: `http://127.0.0.1:${backendPort}`,
          changeOrigin: true,
          // Public ngrok traffic is same-origin from the browser's perspective.
          // Remove the forwarded Origin header before it reaches FastAPI so its
          // local-development CORS allowlist is not asked to trust arbitrary
          // temporary ngrok hostnames. Local direct API calls still use CORS.
          configure: (proxy) => {
            proxy.on("proxyReq", (proxyReq) => {
              proxyReq.removeHeader("origin");
            });
          },
        },
      },
    },
  };
});

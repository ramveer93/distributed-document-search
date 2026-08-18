import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Same-origin by design: the dev server proxies the gateway's routes, and in
// docker nginx does the same. No CORS anywhere, which is also how this would
// actually be deployed.
const gateway = process.env.GATEWAY_URL ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // mirrors nginx in docker: /api/* -> gateway, prefix stripped
      "/api": {
        target: gateway,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});

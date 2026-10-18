import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API (FastAPI) runs on :8000. In development Vite proxies /api to it; in production
// the API serves the built files from apps/web/dist.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});

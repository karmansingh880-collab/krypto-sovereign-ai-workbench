import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = "http://127.0.0.1:8010";

// The backend serves the built app from ../frontend at /ui, so the app lives under that base path.
export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  build: {
    outDir: "../frontend",
    emptyOutDir: false, // ../frontend/legacy (the previous UI) must stay
    sourcemap: false,
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    // `npm run dev`: same-origin API calls, so the login cookie works without CORS.
    proxy: {
      "/auth": backend,
      "/tasks": backend,
      "/system": backend,
      "/health": backend,
      "/ws": { target: backend, ws: true },
    },
  },
});

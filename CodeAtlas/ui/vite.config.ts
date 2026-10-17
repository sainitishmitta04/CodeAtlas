/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The browser only ever talks to /api on the Vite origin; Vite forwards it to FastAPI.
// CODEATLAS_API_URL is the same variable the Streamlit UI used (default: local uvicorn).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.CODEATLAS_API_URL || process.env.CODEATLAS_API_URL || "http://127.0.0.1:8000";
  const proxy = {
    "/api": {
      target,
      changeOrigin: true,
      rewrite: (path: string) => path.replace(/^\/api/, ""),
    },
  };
  return {
    plugins: [react()],
    server: { port: 5173, strictPort: false, proxy },
    preview: { port: 4173, proxy },
    build: { target: "es2022", sourcemap: false, chunkSizeWarningLimit: 900 },
    test: { environment: "node", include: ["src/**/*.test.ts"] },
  };
});

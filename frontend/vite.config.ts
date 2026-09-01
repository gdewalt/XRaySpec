import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: send /api and health checks to the FastAPI backend on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/live": "http://localhost:8000",
      "/ready": "http://localhost:8000",
    },
  },
});

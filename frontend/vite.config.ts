import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds static assets that FastAPI serves at /dashboard/ (see app/main.py).
// No Node server in production - this is a build step only.
export default defineConfig({
  plugins: [react()],
  base: "/dashboard/",
  build: {
    outDir: "../app/static/dashboard",
    emptyOutDir: true,
  },
});

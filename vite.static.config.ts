import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const skillDoctorApiUrl = process.env.NEXT_PUBLIC_SKILL_DOCTOR_API_URL?.trim();

export default defineConfig({
  base: "/skill-doctor/",
  define: {
    "process.env.NEXT_PUBLIC_SKILL_DOCTOR_API_URL": skillDoctorApiUrl
      ? JSON.stringify(skillDoctorApiUrl)
      : "undefined",
  },
  plugins: [react()],
  build: {
    outDir: "dist-pages",
    emptyOutDir: true,
  },
});

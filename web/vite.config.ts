import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 注意：web/index.html 是静态 UI 原型，必须保留；
// 新控制台使用独立入口 console.html，避免覆盖原型。
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: { input: "console.html" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});

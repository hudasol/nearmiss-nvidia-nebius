import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

// End-to-end tests drive the production web build served by the real API, with a fresh
// SQLite database. Run `npm run build` first; `npm run e2e` does both.
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const python = process.platform === "win32"
  ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
  : path.join(repoRoot, ".venv", "bin", "python");
const port = 8765;

export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, viewport: { width: 1440, height: 900 } },
  webServer: {
    command: `"${process.env.NEARMISS_PYTHON ?? python}" -m nearmiss.api.app`,
    url: `http://127.0.0.1:${port}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      NEARMISS_PORT: String(port),
      NEARMISS_DB_PATH: path.join(repoRoot, "var", `e2e-${Date.now()}.sqlite3`),
      NEARMISS_WORKERS: "0",
      NEBIUS_API_KEY: "",
    },
  },
});

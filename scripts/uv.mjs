#!/usr/bin/env node
// npm → uv bridge, so the app can be used like a Node project:
//   npm install   → installs uv if missing, then `uv sync` (Python + dependencies)
//   npm run dev   → `uv run vitalmart ui --open` (http://127.0.0.1:8501)
// Usage: node scripts/uv.mjs setup | run <uv run args…>
// No data leaves the machine: the only downloads are uv itself and the Python packages.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir, platform } from "node:os";
import { join } from "node:path";

const isWin = platform() === "win32";
const exe = isWin ? "uv.exe" : "uv";

function onPath(cmd) {
  const r = spawnSync(cmd, ["--version"], { stdio: "ignore", shell: false });
  return r.status === 0;
}

function findUv() {
  if (onPath("uv")) return "uv";
  const candidates = [
    process.env.UV_INSTALL_DIR && join(process.env.UV_INSTALL_DIR, exe),
    join(homedir(), ".local", "bin", exe),
    join(homedir(), ".cargo", "bin", exe),
    isWin && process.env.LOCALAPPDATA && join(process.env.LOCALAPPDATA, "uv", exe),
  ].filter(Boolean);
  return candidates.find((p) => existsSync(p)) ?? null;
}

function installUv() {
  console.log("▶ Az uv (Python-csomagkezelő) nincs telepítve – telepítem…");
  const r = isWin
    ? spawnSync("powershell", ["-NoProfile", "-ExecutionPolicy", "ByPass", "-Command",
        "irm https://astral.sh/uv/install.ps1 | iex"], { stdio: "inherit" })
    : spawnSync("sh", ["-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
        { stdio: "inherit" });
  if (r.status !== 0) {
    console.error("✖ Az uv telepítése nem sikerült. Telepítsd kézzel: https://docs.astral.sh/uv/");
    process.exit(1);
  }
}

function uv(args) {
  let bin = findUv();
  if (!bin) {
    installUv();
    bin = findUv();
    if (!bin) {
      console.error("✖ Az uv települt, de nem található. Nyiss új terminált, és futtasd újra.");
      process.exit(1);
    }
  }
  const r = spawnSync(bin, args, { stdio: "inherit" });
  if (r.error) {
    console.error(`✖ ${r.error.message}`);
    process.exit(1);
  }
  process.exit(r.status ?? 1);
}

const [mode, ...rest] = process.argv.slice(2);
if (mode === "setup") {
  // Skip when npm installs this package as someone else's dependency (never the case here).
  if (process.env.VITALMART_SKIP_SETUP) process.exit(0);
  console.log("▶ Python-környezet és függőségek telepítése (uv sync)…");
  uv(["sync"]);
} else if (mode === "run") {
  uv(["run", ...rest]);
} else {
  console.error("Használat: node scripts/uv.mjs setup | run <parancs…>");
  process.exit(2);
}

import { spawnSync } from "node:child_process";

// Playwright currently bundles Node 24.21.0; route its driver through the patched host runtime.
const version = process.versions.node.split(".").map(Number);
if (version[0] !== 24 || version[1] < 22 || (version[1] === 22 && version[2] < 1)) {
  throw new Error("Playwright requires Node.js 24.22.1 or newer in the 24.x line");
}
const result = spawnSync(process.argv[2], process.argv.slice(3), {
  stdio: "inherit",
  env: { ...process.env, PLAYWRIGHT_NODEJS_PATH: process.execPath },
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);

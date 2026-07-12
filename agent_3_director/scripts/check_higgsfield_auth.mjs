#!/usr/bin/env node
/**
 * Preflight: verify Higgsfield CLI session before Seedance render.
 * Usage: node scripts/check_higgsfield_auth.mjs
 */
import { spawnSync } from "child_process";
import { readFileSync } from "fs";
import { resolve } from "path";
import { loadEnv, ROOT } from "../env.mjs";

loadEnv();

const cfg = JSON.parse(readFileSync(resolve(ROOT, "config/seedance.json"), "utf8"));
const bin = process.env.HIGGSFIELD_CLI_BIN || cfg.cli_bin || "higgsfield";

const res = spawnSync(bin, ["account", "status", "--json", "--no-color"], {
  encoding: "utf8",
});

const out = `${res.stdout || ""}${res.stderr || ""}`;
if (res.status !== 0 || /session expired|not authenticated/i.test(out)) {
  console.error("Higgsfield CLI: сессия истекла или не авторизован.");
  console.error("Выполните в терминале: higgsfield auth login");
  console.error("(откроется браузер, подтвердите вход за ~30 сек)");
  process.exit(1);
}

console.log("Higgsfield CLI: OK");
if (res.stdout.trim()) console.log(res.stdout.trim());

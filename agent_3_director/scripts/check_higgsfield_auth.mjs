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
  console.error("Уведомление уйдёт в @Error_real_estate_bot (chain watcher, tag=higgsfield_auth).");
  console.error("Починить: higgsfield auth login → scp credentials на VPS (HOME=/opt/openhome).");
  process.exit(1);
}

console.log("Higgsfield CLI: OK");
if (res.stdout.trim()) console.log(res.stdout.trim());

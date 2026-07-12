#!/usr/bin/env node
import { readFileSync, existsSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

function loadEnv() {
  for (const name of [".env.real-estate", ".env"]) {
    const p = resolve(ROOT, name);
    if (!existsSync(p)) continue;
    for (const line of readFileSync(p, "utf8").split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("#") || !t.includes("=")) continue;
      const i = t.indexOf("=");
      const k = t.slice(0, i).trim();
      const v = t.slice(i + 1).trim();
      if (!process.env[k]) process.env[k] = v;
    }
  }
  if (!process.env.HF_CREDENTIALS && process.env.HIGGSFIELD_API_KEY && process.env.HIGGSFIELD_API_SECRET) {
    process.env.HF_CREDENTIALS = `${process.env.HIGGSFIELD_API_KEY}:${process.env.HIGGSFIELD_API_SECRET}`;
  }
}

loadEnv();

const cfg = JSON.parse(readFileSync(resolve(ROOT, "config/pipeline.json"), "utf8")).seedance;
const { isCliAuthenticated } = await import("../higgsfieldCli.mjs");
const { isMcpConfigured } = await import("../higgsfieldMcp.mjs");
const { isSeedanceConfigured } = await import("../renderSeedance.mjs");

console.log("Seedance providers:");
console.log("  CLI auth:", isCliAuthenticated(cfg) ? "yes" : "no (run: higgsfield auth login)");
console.log("  MCP token:", isMcpConfigured(cfg) ? "yes" : "no (OAuth via https://mcp.higgsfield.ai/mcp)");
console.log("  API key:", process.env.HF_CREDENTIALS ? "yes (DoP only, not seedance_2_0)" : "no");
console.log("  Agent3 ready:", isSeedanceConfigured(cfg) ? "yes" : "no");

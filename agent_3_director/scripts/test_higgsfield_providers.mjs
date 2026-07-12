#!/usr/bin/env node
import { readFileSync } from "fs";
import { resolve } from "path";
import { loadEnv, ROOT } from "../env.mjs";

loadEnv();

const cfg = JSON.parse(readFileSync(resolve(ROOT, "config/seedance.json"), "utf8"));
const { isCliAuthenticated } = await import("../higgsfieldCli.mjs");
const { isMcpConfigured } = await import("../higgsfieldMcp.mjs");
const { isPlatformConfigured } = await import("../higgsfieldPlatform.mjs");
const { isSeedanceConfigured } = await import("../renderSeedance.mjs");

console.log("Seedance providers:");
console.log("  Platform API:", isPlatformConfigured() ? "yes (DoP, no OAuth)" : "no (cloud.higgsfield.ai/api-keys)");
console.log("  CLI auth:", isCliAuthenticated(cfg) ? "yes" : "no (run: higgsfield auth login)");
console.log("  MCP token:", isMcpConfigured(cfg) ? "yes" : "no");
console.log("  Ready:", isSeedanceConfigured(cfg) ? "yes" : "no");

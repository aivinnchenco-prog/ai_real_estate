#!/usr/bin/env node
/** Smoke test: Higgsfield credentials (no secrets in output). */
import { readFileSync, existsSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { createRequire } from "module";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const require = createRequire(import.meta.url);

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

const keyId = process.env.HIGGSFIELD_API_KEY || process.env.HF_API_KEY;
const secret = process.env.HIGGSFIELD_API_SECRET || process.env.HF_API_SECRET;

if (!keyId || !secret) {
  console.error("FAIL: missing Higgsfield credentials in env");
  process.exit(1);
}

console.log(`Key ID prefix: ${keyId.slice(0, 8)}…`);

try {
  const { HiggsfieldClient } = require("@higgsfield/client");
  const client = new HiggsfieldClient({
    apiKey: keyId,
    apiSecret: secret,
  });
  const motions = await client.getMotions();
  console.log(`OK: auth valid, motions=${motions.length}`);
} catch (e) {
  console.error("FAIL:", e.message?.slice(0, 200) || e);
  process.exit(1);
}

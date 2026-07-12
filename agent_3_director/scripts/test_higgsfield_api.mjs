#!/usr/bin/env node
/** Smoke test: Platform API key (DoP). No secrets in output. */
import { readFileSync } from "fs";
import { resolve } from "path";
import { ROOT, loadEnv } from "../env.mjs";
import { isPlatformConfigured, generateClipViaPlatform } from "../higgsfieldPlatform.mjs";

loadEnv();

const keyId = process.env.HIGGSFIELD_API_KEY;
if (!isPlatformConfigured()) {
  console.error("FAIL: set HIGGSFIELD_API_KEY + HIGGSFIELD_API_SECRET in .env");
  process.exit(1);
}

console.log(`Key ID prefix: ${keyId.slice(0, 8)}…`);
const cfg = JSON.parse(readFileSync(resolve(ROOT, "config/seedance.json"), "utf8"));
console.log(`Model: ${cfg.api_model || "dop-turbo"}`);

const dryRun = process.argv.includes("--submit");
if (!dryRun) {
  console.log("OK: API credentials present");
  console.log("Run with --submit to test one clip (uses credits)");
  process.exit(0);
}

const img =
  process.env.TEST_IMAGE_URL ||
  `${process.env.CLOUDFLARE_PUBLIC_BASE_URL}/20260701_001/photos/photo_001.png`;

try {
  const url = await generateClipViaPlatform({
    imageUrl: img,
    prompt: "slow cinematic push in luxury apartment interior, vertical 9:16",
    cfg,
  });
  console.log("OK: video URL", url);
} catch (e) {
  console.error("FAIL:", e.message?.slice(0, 300) || e);
  process.exit(1);
}

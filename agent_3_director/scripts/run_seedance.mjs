#!/usr/bin/env node
/**
 * Standalone Seedance 2.0 montage via Higgsfield.
 *
 * Usage:
 *   node scripts/run_seedance.mjs --object-id demo_001 --images https://a.jpg,https://b.jpg
 *   node scripts/run_seedance.mjs --object-id demo_001 --r2-keys obj/photos/01.jpg,obj/photos/02.jpg
 *   node scripts/run_seedance.mjs --object-id demo_001 --curator http://127.0.0.1:8077/select-diverse --image-list images.json
 *
 * image-list JSON: [{ "key": "photo_01.jpg", "url": "https://..." }, ...]
 */
import { readFileSync, existsSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { renderSeedance } from "../renderSeedance.mjs";
import { publicUrlForKey } from "../r2util.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

function loadEnv() {
  for (const name of [".env", ".env.local"]) {
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
  process.env.R2_ACCOUNT_ID = process.env.R2_ACCOUNT_ID || process.env.CLOUDFLARE_ACCOUNT_ID;
  process.env.R2_ACCESS_KEY_ID = process.env.R2_ACCESS_KEY_ID || process.env.CLOUDFLARE_ACCESS_KEY_ID;
  process.env.R2_SECRET_ACCESS_KEY =
    process.env.R2_SECRET_ACCESS_KEY || process.env.CLOUDFLARE_SECRET_ACCESS_KEY;
  process.env.R2_BUCKET = process.env.R2_BUCKET || process.env.CLOUDFLARE_BUCKET;
  process.env.R2_PUBLIC_BASE = process.env.R2_PUBLIC_BASE || process.env.CLOUDFLARE_PUBLIC_BASE_URL;
  if (!process.env.HF_CREDENTIALS && process.env.HIGGSFIELD_API_KEY && process.env.HIGGSFIELD_API_SECRET) {
    process.env.HF_CREDENTIALS = `${process.env.HIGGSFIELD_API_KEY}:${process.env.HIGGSFIELD_API_SECRET}`;
  }
}

function loadConfig() {
  return JSON.parse(readFileSync(resolve(ROOT, "config/seedance.json"), "utf8"));
}

function parseArgs() {
  const args = process.argv.slice(2);
  const out = { objectId: null, images: [], r2Keys: [], curatorUrl: null, imageList: null };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--object-id") out.objectId = args[++i];
    else if (a === "--images") out.images = args[++i].split(",").map((s) => s.trim()).filter(Boolean);
    else if (a === "--r2-keys") out.r2Keys = args[++i].split(",").map((s) => s.trim()).filter(Boolean);
    else if (a === "--curator") out.curatorUrl = args[++i];
    else if (a === "--image-list") out.imageList = args[++i];
  }
  if (!out.objectId) {
    console.error(`Usage: node scripts/run_seedance.mjs --object-id ID [--images url1,url2 | --r2-keys k1,k2 | --image-list file.json]`);
    process.exit(1);
  }
  return out;
}

async function curatorSelectDiverse(imageItems, cfg, curatorUrl) {
  const body = {
    images: imageItems,
    top_k: cfg.image_count || 13,
    max_per_category: cfg.max_per_category ?? 2,
    max_similarity: cfg.max_similarity ?? 0.88,
    mmr_lambda: cfg.mmr_lambda ?? 0.6,
  };
  const res = await fetch(curatorUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Curator HTTP ${res.status}: ${await res.text()}`);
  const data = await res.json();
  return data.selected.map((s) => s.key);
}

async function resolveImageKeys(opts, cfg) {
  if (opts.r2Keys.length) return opts.r2Keys;

  let items = [];
  if (opts.imageList) {
    const raw = JSON.parse(readFileSync(resolve(ROOT, opts.imageList), "utf8"));
    items = raw.map((x) => (typeof x === "string" ? { key: x, url: x } : x));
  } else if (opts.images.length) {
    items = opts.images.map((url, i) => ({ key: `img_${i}.jpg`, url }));
  }

  if (!items.length) {
    throw new Error("Provide --images, --r2-keys, or --image-list");
  }

  const curatorUrl = opts.curatorUrl || cfg.curator_diverse_url;
  if (curatorUrl && items.length > (cfg.image_count || 13)) {
    const names = await curatorSelectDiverse(items, cfg, curatorUrl);
    const prefix = opts.objectId.includes("/") ? "" : `${opts.objectId}/photos/`;
    return names.map((n) => `${prefix}${n}`);
  }

  if (opts.images.length) {
    throw new Error("Direct --images URLs: pass as R2 public URLs in --image-list JSON for renderSeedance");
  }
  return items.map((x) => x.key);
}

async function main() {
  loadEnv();
  const cfg = loadConfig();
  const opts = parseArgs();

  const image_keys = await resolveImageKeys(opts, cfg);
  console.log(`Seedance: ${opts.objectId}, ${image_keys.length} images`);

  const result = await renderSeedance({
    object_id: opts.objectId,
    image_keys,
    cfg,
  });

  console.log(JSON.stringify(result, null, 2));
}

main().catch((e) => {
  console.error("FAILED:", e.message);
  process.exit(1);
});

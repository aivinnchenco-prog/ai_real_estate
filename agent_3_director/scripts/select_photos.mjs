#!/usr/bin/env node
/**
 * Проверка отбора фото БЕЗ Notion и БЕЗ рендера — только R2 + селектор.
 *
 * Usage:
 *   node scripts/select_photos.mjs --object-id F_20260719_008
 *   node scripts/select_photos.mjs --object-id F_20260719_008 --engine seedance
 *   node scripts/select_photos.mjs --object-id F_20260719_008 --no-curator   # форсировать Gemini
 */
import { readFileSync } from "fs";
import { resolve } from "path";
import { loadEnv, ROOT } from "../env.mjs";
import { listKeys } from "../r2list.mjs";
import { selectPhotosSeedance } from "../selectPhotosSeedance.mjs";

function parseArgs() {
  const args = process.argv.slice(2);
  const out = { objectId: null, engine: "wan", noCurator: false };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--object-id") out.objectId = args[++i];
    else if (args[i] === "--engine") out.engine = args[++i];
    else if (args[i] === "--no-curator") out.noCurator = true;
  }
  if (!out.objectId) {
    console.error("Usage: node scripts/select_photos.mjs --object-id <id> [--engine wan|seedance] [--no-curator]");
    process.exit(1);
  }
  return out;
}

async function main() {
  loadEnv();
  const { objectId, engine, noCurator } = parseArgs();
  const cfg = JSON.parse(readFileSync(resolve(ROOT, `config/${engine}.json`), "utf8"));
  if (noCurator) {
    delete cfg.curator_diverse_url;
    delete process.env.CURATOR_BASE_URL;
  }

  const prefix = `${objectId}/photos/`;
  const allKeys = await listKeys(prefix);
  const photoKeys = allKeys.filter((k) => /\.(jpe?g|png|webp)$/i.test(k) && !k.includes("hook_cover"));
  if (!photoKeys.length) throw new Error(`No photos in R2 at ${prefix}`);

  const publicBase = process.env.R2_PUBLIC_BASE;
  if (!publicBase) throw new Error("R2_PUBLIC_BASE not set");

  const imageItems = photoKeys.map((key) => ({
    key: key.split("/").pop(),
    url: `${publicBase}/${key}`,
  }));

  console.log(`Object: ${objectId}, photos in R2: ${imageItems.length}, engine config: ${engine}`);
  const t0 = Date.now();
  const selected = await selectPhotosSeedance(imageItems, cfg);
  console.log(`\nSelected ${selected.length} in ${((Date.now() - t0) / 1000).toFixed(1)}s:`);
  for (const k of selected) console.log(`  - ${k}`);
}

main().catch((err) => {
  console.error("FAILED:", err.message);
  process.exit(1);
});

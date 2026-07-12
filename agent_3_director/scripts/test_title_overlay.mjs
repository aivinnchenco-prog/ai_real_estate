#!/usr/bin/env node
/**
 * Test title overlay on an existing Seedance video (no Higgsfield re-render).
 *
 * Usage:
 *   node scripts/test_title_overlay.mjs --object-id 20260702_001
 *   node scripts/test_title_overlay.mjs --object-id 20260702_001 --publish
 */
import { readFileSync, mkdirSync, createWriteStream } from "fs";
import { resolve } from "path";
import { pipeline } from "stream/promises";
import { loadEnv, ROOT } from "../env.mjs";
import {
  loadNotionConfig,
  queryByObjectId,
  pageVideoOverlayMeta,
  pageSeedanceUrl,
  setSeedanceUrl,
} from "../notionCrm.mjs";
import { uploadVideoToR2 } from "../r2util.mjs";
import {
  loadTitleOverlayConfig,
  applyTitleOverlay,
} from "../applyTitleOverlay.mjs";

function loadSeedanceConfig() {
  return JSON.parse(readFileSync(resolve(ROOT, "config/seedance.json"), "utf8"));
}

function parseArgs() {
  const args = process.argv.slice(2);
  const out = { objectId: null, video: null, publish: false };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--object-id") out.objectId = args[++i];
    else if (args[i] === "--video") out.video = args[++i];
    else if (args[i] === "--publish") out.publish = true;
  }
  if (!out.objectId) throw new Error("--object-id is required");
  return out;
}

async function download(url, dest) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed: ${res.status} ${url}`);
  await pipeline(res.body, createWriteStream(dest));
}

async function main() {
  loadEnv();
  const { objectId, video, publish } = parseArgs();
  const notionCfg = loadNotionConfig();
  const seedanceCfg = loadSeedanceConfig();
  const overlayCfg = loadTitleOverlayConfig(seedanceCfg);
  const fields = notionCfg.fields;

  const page = await queryByObjectId(objectId, fields);
  if (!page) throw new Error(`Object not found: ${objectId}`);

  const meta = pageVideoOverlayMeta(page, fields, notionCfg.overlay_fields);
  console.log("Overlay meta:", meta);

  const tmpDir = `/tmp/title-overlay-${objectId}-${Date.now()}`;
  mkdirSync(tmpDir, { recursive: true });

  const inputPath = video || resolve(tmpDir, "input.mp4");
  if (!video) {
    const url = pageSeedanceUrl(page, fields);
    if (!url) throw new Error("No video_url_Seedance in Notion — pass --video");
    console.log(`Downloading: ${url}`);
    await download(url, inputPath);
  }

  const outputPath = resolve(tmpDir, "video_with_title.mp4");
  const result = await applyTitleOverlay({
    inputPath,
    outputPath,
    meta,
    cfg: overlayCfg,
  });

  let publishedUrl = null;
  if (publish) {
    const r2Key = `${objectId}/video_seedance_9x16.mp4`;
    publishedUrl = await uploadVideoToR2(outputPath, r2Key);
    await setSeedanceUrl(page, fields, publishedUrl);
    console.log(`\nPublished to R2: ${publishedUrl}`);
  }

  console.log("\nTITLE_OVERLAY_DONE");
  console.log(JSON.stringify({ ...result, output: outputPath, published_url: publishedUrl }, null, 2));
}

main().catch((err) => {
  console.error("FAILED:", err.message);
  process.exit(1);
});

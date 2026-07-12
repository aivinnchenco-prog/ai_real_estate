#!/usr/bin/env node
/**
 * Agent 3 — два параллельных видео-трека:
 *   A) FFmpeg reel (6 кадров, 9:16) — обязательный → ready_to_post сразу после reel
 *   B) Seedance/Higgsfield (13 кадров, 9:16) — стартует параллельно с A, URL пишется отдельно
 */
import { readFileSync, existsSync, appendFileSync, mkdirSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { createHash } from "crypto";
import { renderReel } from "../renderReel.mjs";
import { renderSeedance } from "../renderSeedance.mjs";
import { listKeys } from "../r2list.mjs";
import { applyEnvOverrides } from "../pipelineEnv.mjs";

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
  process.env.R2_ACCOUNT_ID = process.env.R2_ACCOUNT_ID || process.env.CLOUDFLARE_ACCOUNT_ID;
  process.env.R2_ACCESS_KEY_ID = process.env.R2_ACCESS_KEY_ID || process.env.CLOUDFLARE_ACCESS_KEY_ID;
  process.env.R2_SECRET_ACCESS_KEY = process.env.R2_SECRET_ACCESS_KEY || process.env.CLOUDFLARE_SECRET_ACCESS_KEY;
  process.env.R2_BUCKET = process.env.R2_BUCKET || process.env.CLOUDFLARE_BUCKET;
  process.env.R2_PUBLIC_BASE = process.env.R2_PUBLIC_BASE || process.env.CLOUDFLARE_PUBLIC_BASE_URL;
  if (!process.env.HF_CREDENTIALS && process.env.HIGGSFIELD_API_KEY && process.env.HIGGSFIELD_API_SECRET) {
    process.env.HF_CREDENTIALS = `${process.env.HIGGSFIELD_API_KEY}:${process.env.HIGGSFIELD_API_SECRET}`;
  }
}

function loadConfig() {
  return applyEnvOverrides(JSON.parse(readFileSync(resolve(ROOT, "config/pipeline.json"), "utf8")));
}

function logEvent(objectId, event, payload) {
  try {
    const dir = resolve(ROOT, "data/events");
    mkdirSync(dir, { recursive: true });
    appendFileSync(
      resolve(dir, `${objectId}.jsonl`),
      `${JSON.stringify({ ts: new Date().toISOString(), event, ...payload })}\n`
    );
  } catch {
    /* non-fatal */
  }
}

const NOTION_KEY = () => process.env.NOTION_API_KEY;
const DB_ID = () => {
  const raw = process.env.NOTION_DB_ID || process.env.NOTION_DATABASE_ID;
  if (raw.includes("-")) return raw;
  return `${raw.slice(0, 8)}-${raw.slice(8, 12)}-${raw.slice(12, 16)}-${raw.slice(16, 20)}-${raw.slice(20)}`;
};

async function notionQueryByObjectId(objectId) {
  const res = await fetch(`https://api.notion.com/v1/databases/${DB_ID()}/query`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${NOTION_KEY()}`,
      "Notion-Version": "2022-06-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      filter: { property: "Объект ID", rich_text: { equals: objectId } },
    }),
  });
  if (!res.ok) throw new Error(`Notion query: ${await res.text()}`);
  const data = await res.json();
  return data.results[0] || null;
}

async function notionUpdatePage(pageId, properties) {
  const res = await fetch(`https://api.notion.com/v1/pages/${pageId}`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${NOTION_KEY()}`,
      "Notion-Version": "2022-06-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ properties }),
  });
  if (!res.ok) throw new Error(`Notion update: ${await res.text()}`);
}

function readErrorCount(page) {
  const n = page?.properties?.error_count?.number;
  return typeof n === "number" ? n : 0;
}

async function notionSetFailed(page, fields, statuses, message) {
  await notionUpdatePage(page.id, {
    [fields.status]: { status: { name: statuses.video_failed } },
    [fields.last_error]: { rich_text: [{ text: { content: String(message).slice(0, 2000) } }] },
    error_count: { number: readErrorCount(page) + 1 },
  });
}

async function curatorHealthy(healthUrl) {
  try {
    const res = await fetch(healthUrl, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}

async function curatorSelect(imageItems, topK, curatorUrl) {
  const res = await fetch(curatorUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ images: imageItems, top_k: topK }),
  });
  if (!res.ok) throw new Error(`Curator HTTP ${res.status}`);
  const data = await res.json();
  return data.selected.map((s) => s.key);
}

async function curatorSelectDiverse(imageItems, cfg) {
  const seedance = cfg.seedance || {};
  const topK = seedance.image_count || 13;
  const diverseUrl =
    seedance.curator_diverse_url ||
    (cfg.curator_url || "http://127.0.0.1:8077/select").replace(/\/select$/, "/select-diverse");

  const body = {
    images: imageItems,
    top_k: topK,
    max_per_category: seedance.max_per_category ?? 2,
    max_similarity: seedance.max_similarity ?? 0.88,
    mmr_lambda: seedance.mmr_lambda ?? 0.6,
  };

  const res = await fetch(diverseUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Curator diverse HTTP ${res.status}`);
  const data = await res.json();
  if (data.warning) console.warn(`Curator diverse: ${data.warning}`);
  return data.selected.map((s) => s.key);
}

function fallbackSelect(imageItems, topK) {
  const junk = /kettle|dryer|cutting|board|meeting|playroom|pinball|arcade|foosball/i;
  const preferred = imageItems.filter((i) => !junk.test(i.key));
  const pool = preferred.length >= topK ? preferred : imageItems;
  if (pool.length <= topK) return pool.map((i) => i.key);
  const step = pool.length / topK;
  return Array.from({ length: topK }, (_, i) => pool[Math.floor(i * step)].key);
}

async function selectPhotosReel(imageItems, cfg) {
  const topK = cfg.curator_top_k;
  const useFallback = cfg.curator_fallback !== false;
  const healthUrl = cfg.curator_health_url || cfg.curator_url.replace(/\/select$/, "/health");
  if (await curatorHealthy(healthUrl)) {
    try {
      return await curatorSelect(imageItems, topK, cfg.curator_url);
    } catch (err) {
      if (!useFallback) throw err;
      console.warn("Curator select failed, using fallback:", err.message);
    }
  } else if (!useFallback) {
    throw new Error(`Curator unavailable at ${healthUrl}`);
  } else {
    console.warn("Curator offline — reel photo fallback");
  }
  return fallbackSelect(imageItems, topK);
}

async function selectPhotosSeedance(imageItems, cfg) {
  const seedance = cfg.seedance || {};
  const topK = seedance.image_count || 13;
  const useFallback = cfg.curator_fallback !== false;
  const healthUrl = cfg.curator_health_url || cfg.curator_url.replace(/\/select$/, "/health");

  if (await curatorHealthy(healthUrl)) {
    try {
      return await curatorSelectDiverse(imageItems, cfg);
    } catch (err) {
      if (!useFallback) throw err;
      console.warn("Curator diverse failed, using fallback:", err.message);
    }
  } else if (!useFallback) {
    throw new Error(`Curator unavailable at ${healthUrl}`);
  } else {
    console.warn("Curator offline — seedance photo fallback");
  }
  return fallbackSelect(imageItems, topK);
}

function pickMusicTrack(tracks, objectId) {
  const hash = createHash("sha256").update(objectId).digest();
  return tracks[hash[0] % tracks.length];
}

function parseArgs() {
  const args = process.argv.slice(2);
  let objectId = null;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--object-id") objectId = args[++i];
  }
  if (!objectId) {
    console.error("Usage: node scripts/agent3_video.mjs --object-id 20260701_001");
    process.exit(1);
  }
  return { objectId };
}

async function main() {
  loadEnv();
  const cfg = loadConfig();
  const { objectId } = parseArgs();
  const statuses = cfg.notion.statuses;
  const fields = cfg.notion.fields;
  const seedanceCfg = cfg.seedance || {};

  console.log(`\n=== Agent 3: ${objectId} ===\n`);

  const page = await notionQueryByObjectId(objectId);
  if (!page) throw new Error(`Object not found in Notion: ${objectId}`);

  const status = page.properties?.["Статус"]?.status?.name;
  const galleryUrl = page.properties?.[fields.photo]?.url;
  if (!galleryUrl) {
    throw new Error("Notion CRM: missing gallery URL in photo field — run Agent 2 first");
  }
  console.log(`Gallery (Notion): ${galleryUrl}`);

  if (status === "ready_to_post") {
    console.log("Already ready_to_post — skip");
    process.exit(0);
  }
  if (status !== "ready_for_video" && status !== "video_failed") {
    throw new Error(`Wrong status '${status}' — expected ready_for_video. Run Agent 2 first.`);
  }

  await notionUpdatePage(page.id, {
    [fields.status]: { status: { name: statuses.video_start } },
    [fields.last_error]: { rich_text: [] },
  });

  try {
    const prefix = `${objectId}/photos/`;
    const allKeys = await listKeys(prefix);
    const photoKeys = allKeys.filter((k) => /\.(jpe?g|png|webp)$/i.test(k));
    if (!photoKeys.length) {
      throw new Error(`No photos in R2 at ${prefix} — run Agent 2 first`);
    }

    const publicBase = process.env.R2_PUBLIC_BASE;
    const imageItems = photoKeys.map((key) => ({
      key: key.split("/").pop(),
      url: `${publicBase}/${key}`,
    }));

    const seedanceEnabled = seedanceCfg.enabled !== false;

    const [reelNames, seedanceNames] = await Promise.all([
      selectPhotosReel(imageItems, cfg),
      seedanceEnabled ? selectPhotosSeedance(imageItems, cfg) : Promise.resolve([]),
    ]);

    const reelKeys = reelNames.map((name) => `${objectId}/photos/${name}`);
    const seedanceKeys = seedanceNames.map((name) => `${objectId}/photos/${name}`);

    const musicKey = pickMusicTrack(cfg.music_tracks, objectId);
    console.log(`Music: ${musicKey}`);
    console.log(`Reel photos: ${reelKeys.length}, Seedance photos: ${seedanceKeys.length}`);

    let seedanceError = null;

    const seedancePromise =
      seedanceEnabled && seedanceKeys.length
        ? renderSeedance({
            object_id: objectId,
            image_keys: seedanceKeys,
            cfg: seedanceCfg,
          }).catch((err) => {
            seedanceError = err.message;
            console.warn(`Seedance failed (non-blocking): ${seedanceError}`);
            logEvent(objectId, "agent3_seedance_failed", { error: seedanceError });
            return null;
          })
        : Promise.resolve(null);

    if (seedanceEnabled && seedanceKeys.length) {
      console.log("Seedance generation started in parallel with FFmpeg reel");
      logEvent(objectId, "agent3_seedance_started", { image_count: seedanceKeys.length });
    }

    const videoUrls = await renderReel({
      object_id: objectId,
      image_keys: reelKeys,
      music_key: musicKey,
      frame_duration: cfg.frame_duration_seconds,
      min_frames: cfg.min_frames_for_video,
    });

    const reelProperties = {
      [fields.status]: { status: { name: statuses.video_done } },
    };
    if (videoUrls["9x16"]) reelProperties[fields.video_vertical] = { url: videoUrls["9x16"] };
    await notionUpdatePage(page.id, reelProperties);
    logEvent(objectId, "agent3_reel_done", { reel: videoUrls });

    const seedanceResult = await seedancePromise;
    if (seedanceResult?.["9x16"]) {
      await notionUpdatePage(page.id, {
        [fields.video_seedance]: { url: seedanceResult["9x16"] },
      });
      logEvent(objectId, "agent3_seedance_done", { seedance: seedanceResult });
    }

    const summary = {
      object_id: objectId,
      page_id: page.id,
      reel: videoUrls,
      seedance: seedanceResult,
      seedance_error: seedanceError,
    };
    logEvent(objectId, "agent3_done", summary);

    console.log("\nREADY_TO_POST");
    console.log(JSON.stringify(summary, null, 2));
  } catch (err) {
    await notionSetFailed(page, fields, statuses, err.message);
    throw err;
  }
}

main().catch((err) => {
  console.error("FAILED:", err.message);
  process.exit(1);
});

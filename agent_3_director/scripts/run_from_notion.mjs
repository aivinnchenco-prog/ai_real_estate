#!/usr/bin/env node
/**
 * Agent 5 — Seedance video from Notion CRM + R2 photos.
 *
 * Usage:
 *   node scripts/run_from_notion.mjs --latest
 *   node scripts/run_from_notion.mjs --object-id 20260701_001
 *   node scripts/run_from_notion.mjs --latest --dry-run
 *   node scripts/run_from_notion.mjs --object-id 20260701_001 --force
 */
import { existsSync, readFileSync } from "fs";
import { dirname, join, resolve } from "path";
import { spawnSync } from "child_process";
import { loadEnv, ROOT } from "../env.mjs";
import {
  loadNotionConfig,
  queryByObjectId,
  queryLatestPages,
  pageStatus,
  pageSeedanceUrl,
  pageTitle,
  pageObjectId,
  pageMontageEnabled,
  pageVideoOverlayMeta,
  setSeedanceUrl,
  setError,
} from "../notionCrm.mjs";
import { listKeys } from "../r2list.mjs";
import { selectPhotosSeedance } from "../selectPhotosSeedance.mjs";
import { renderSeedance, isSeedanceConfigured } from "../renderSeedance.mjs";
import { renderHookCover, isTitleOverlayEnabled, loadTitleOverlayConfig } from "../applyTitleOverlay.mjs";
import { downloadFromR2, uploadFileToR2 } from "../r2util.mjs";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";

function loadSeedanceConfig() {
  return JSON.parse(readFileSync(resolve(ROOT, "config/seedance.json"), "utf8"));
}

/**
 * Хук-обложка карусели: первое фото объекта + карточка-хук →
 * R2 {id}/hook_cover.jpg — НАМЕРЕННО вне папки photos/: галерею photos/
 * Агент 6 отправляет клиенту, а на хуке напечатана цена, которая меняется
 * по сезону. Публикатор берёт обложку отдельно первым слайдом карусели.
 * Ошибка не блокирует видео.
 */
const HOOK_COVER_NAME = "hook_cover.jpg";

async function makeHookCover({ objectId, firstPhotoKey, overlayMeta, seedanceCfg }) {
  const overlayCfg = loadTitleOverlayConfig(seedanceCfg);
  const tmpDir = mkdtempSync(join(tmpdir(), "hook-cover-"));
  try {
    const photoLocal = join(tmpDir, "photo.jpg");
    await downloadFromR2(firstPhotoKey, photoLocal);
    const coverLocal = join(tmpDir, HOOK_COVER_NAME);
    await renderHookCover({
      photoPath: photoLocal,
      outputPath: coverLocal,
      meta: overlayMeta,
      cfg: overlayCfg,
    });
    return await uploadFileToR2(
      coverLocal, `${objectId}/${HOOK_COVER_NAME}`, "image/jpeg"
    );
  } finally {
    rmSync(tmpDir, { recursive: true, force: true });
  }
}

function runSchemaCheck(skip) {
  // Валидация живой схемы Notion против schema/notion_schema.json (общий контракт репо).
  if (skip || process.env.SKIP_SCHEMA_CHECK === "1") {
    console.log("[schema] проверка схемы пропущена (--skip-schema-check)");
    return;
  }
  let dir = resolve(ROOT);
  while (true) {
    const validator = join(dir, "schema", "validate_schema.py");
    if (existsSync(validator)) {
      const proc = spawnSync("python3", [validator], { stdio: "inherit" });
      if (proc.error) {
        console.warn(`[schema] не удалось запустить python3 — проверка схемы пропущена: ${proc.error.message}`);
        return;
      }
      if (proc.status !== 0) {
        console.error(
          "[schema] Схема Notion не совпадает с контрактом. " +
            "Исправь таблицу/контракт или запусти с --skip-schema-check."
        );
        process.exit(2);
      }
      return;
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  console.warn("[schema] validate_schema.py не найден — проверка схемы пропущена");
}

function parseArgs() {
  const args = process.argv.slice(2);
  const out = { objectId: null, latest: false, dryRun: false, force: false, skipOverlay: false, skipSchemaCheck: false };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--object-id") out.objectId = args[++i];
    else if (a === "--latest") out.latest = true;
    else if (a === "--dry-run") out.dryRun = true;
    else if (a === "--force") out.force = true;
    else if (a === "--skip-overlay") out.skipOverlay = true;
    else if (a === "--skip-schema-check") out.skipSchemaCheck = true;
  }
  if (!out.objectId && !out.latest) out.latest = true;
  if (out.objectId && out.latest) {
    console.error("Use either --latest or --object-id, not both");
    process.exit(1);
  }
  return out;
}

async function resolvePage(fields, { objectId, latest }) {
  if (objectId) {
    const page = await queryByObjectId(objectId, fields);
    if (!page) throw new Error(`Object not found in Notion: ${objectId}`);
    return { page, objectId };
  }

  const pages = await queryLatestPages(fields);
  for (const page of pages) {
    const id = pageObjectId(page, fields);
    if (!id) continue;
    if (!pageMontageEnabled(page, fields)) {
      console.warn(`Skip ${id}: «Монтаж» = НЕТ (монтаж выключен)`);
      continue;
    }
    const prefix = `${id}/photos/`;
    const keys = await listKeys(prefix);
    const photoKeys = keys.filter((k) => /\.(jpe?g|png|webp)$/i.test(k) && !k.includes("hook_cover"));
    if (photoKeys.length) {
      return { page, objectId: id };
    }
    console.warn(`Skip ${id}: no photos in R2 at ${prefix}`);
  }

  throw new Error("No CRM object with photos in R2 found (sorted by latest added)");
}

async function main() {
  loadEnv();
  const notionCfg = loadNotionConfig();
  const seedanceCfg = loadSeedanceConfig();
  const { dryRun, force, skipOverlay, skipSchemaCheck, ...lookup } = parseArgs();
  runSchemaCheck(skipSchemaCheck);
  const fields = notionCfg.fields;
  const allowedStatuses = notionCfg.statuses?.allowed || ["ready_for_video", "ready_to_post", "video_failed"];

  const { page, objectId } = await resolvePage(fields, lookup);

  if (!pageMontageEnabled(page, fields)) {
    console.log(`Skip ${objectId}: «Монтаж» = НЕТ — объект только для базы, монтаж и Seedance не нужны.`);
    process.exit(0);
  }

  console.log(`\n=== Seedance Agent: ${objectId} ===\n`);

  const title = pageTitle(page, fields);
  const status = pageStatus(page, fields);
  const existingSeedance = pageSeedanceUrl(page, fields);

  const overlayMeta = pageVideoOverlayMeta(page, fields, notionCfg.overlay_fields);

  console.log(`Notion: ${title || objectId}`);
  console.log(`Status: ${status}`);
  console.log(`Overlay: ${JSON.stringify(overlayMeta)}`);
  if (lookup.latest) console.log("Source: latest CRM row with R2 photos");
  if (existingSeedance) console.log(`Existing seedance: ${existingSeedance}`);

  if (existingSeedance && !force) {
    console.log("Seedance URL already set — skip (use --force to regenerate)");
    process.exit(0);
  }

  if (status && !allowedStatuses.includes(status)) {
    throw new Error(`Wrong status '${status}' — expected one of: ${allowedStatuses.join(", ")}`);
  }

  const prefix = `${objectId}/photos/`;
  const allKeys = await listKeys(prefix);
  // hook_cover исключаем: он не должен попадать в референсы Seedance
  // и не должен становиться базой для самого себя при --force
  const photoKeys = allKeys.filter(
    (k) => /\.(jpe?g|png|webp)$/i.test(k) && !k.includes("hook_cover")
  );
  if (!photoKeys.length) {
    throw new Error(`No photos in R2 at ${prefix}`);
  }

  const publicBase = process.env.R2_PUBLIC_BASE;
  if (!publicBase) throw new Error("R2_PUBLIC_BASE / CLOUDFLARE_PUBLIC_BASE_URL not set");

  const imageItems = photoKeys.map((key) => ({
    key: key.split("/").pop(),
    url: `${publicBase}/${key}`,
  }));

  const selectedNames = await selectPhotosSeedance(imageItems, seedanceCfg);
  const image_keys = selectedNames.map((name) => `${objectId}/photos/${name}`);

  console.log(`Duration: ${seedanceCfg.duration_seconds || 14}s, resolution: ${seedanceCfg.resolution}`);
  console.log(`R2 photos: ${photoKeys.length}, selected for Seedance: ${image_keys.length}`);
  for (const k of image_keys) console.log(`  - ${k}`);

  if (dryRun) {
    console.log("\nDRY RUN — no Higgsfield render");
    console.log(
      JSON.stringify(
        {
          object_id: objectId,
          status,
          duration_seconds: seedanceCfg.duration_seconds,
          image_count: image_keys.length,
          image_keys,
          overlay_meta: overlayMeta,
          higgsfield_ready: isSeedanceConfigured(seedanceCfg),
        },
        null,
        2
      )
    );
    return;
  }

  // Хук-обложка карусели — до видео, чтобы она была даже при падении Seedance
  if (!skipOverlay && isTitleOverlayEnabled(seedanceCfg)) {
    try {
      const coverUrl = await makeHookCover({
        objectId,
        firstPhotoKey: photoKeys.sort()[0],
        overlayMeta,
        seedanceCfg,
      });
      console.log(`✓ hook cover (карусель): ${coverUrl}`);
    } catch (err) {
      console.warn(`Hook cover failed (non-blocking): ${err.message}`);
    }
  }

  if (!isSeedanceConfigured(seedanceCfg)) {
    throw new Error("Higgsfield not configured — run: higgsfield auth login  OR set HIGGSFIELD_MCP_ACCESS_TOKEN");
  }

  const authCheck = spawnSync("node", [resolve(ROOT, "scripts/check_higgsfield_auth.mjs")], {
    encoding: "utf8",
    cwd: ROOT,
  });
  if (authCheck.status !== 0) {
    throw new Error(authCheck.stderr?.trim() || "Higgsfield auth check failed");
  }

  try {
    const result = await renderSeedance({
      object_id: objectId,
      image_keys,
      cfg: seedanceCfg,
      overlay_meta: overlayMeta,
      skip_overlay: skipOverlay,
    });

    if (!result?.["9x16"]) {
      throw new Error("Seedance returned no video URL");
    }

    await setSeedanceUrl(page, fields, result["9x16"], notionCfg.statuses?.video_done || "ready_to_post");

    const summary = {
      object_id: objectId,
      page_id: page.id,
      seedance: result,
    };
    console.log("\nSEEDANCE_DONE");
    console.log(JSON.stringify(summary, null, 2));
  } catch (err) {
    await setError(page, fields, err.message, notionCfg.statuses?.video_failed || "video_failed");
    throw err;
  }
}

main().catch((err) => {
  console.error("FAILED:", err.message);
  process.exit(1);
});

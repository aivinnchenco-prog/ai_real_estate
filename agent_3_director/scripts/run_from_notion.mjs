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
  pageCarouselMeta,
  pageVideoEngineId,
  setSeedanceUrl,
  setCarouselUrl,
  setError,
  setMontageStart,
} from "../notionCrm.mjs";
import { listKeys } from "../r2list.mjs";
import { selectPhotosSeedance } from "../selectPhotosSeedance.mjs";
import { buildCarousel, buildBrandFolder } from "../carouselSlides.mjs";
import { renderSeedance, isSeedanceConfigured } from "../renderSeedance.mjs";
import { renderWan, isWanConfigured } from "../renderWan.mjs";
import { renderHookCover, isTitleOverlayEnabled, loadTitleOverlayConfig } from "../applyTitleOverlay.mjs";
import { downloadFromR2, uploadFileToR2 } from "../r2util.mjs";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";

function loadSeedanceConfig() {
  return JSON.parse(readFileSync(resolve(ROOT, "config/seedance.json"), "utf8"));
}

function loadWanConfig() {
  return JSON.parse(readFileSync(resolve(ROOT, "config/wan.json"), "utf8"));
}

function loadVideoEngine(page, fields) {
  const fromNotion = pageVideoEngineId(page, fields);
  if (fromNotion === "seedance") {
    return { engine: "seedance", cfg: loadSeedanceConfig() };
  }
  if (fromNotion === "wan") {
    return { engine: "wan", cfg: loadWanConfig() };
  }
  const engine = (process.env.VIDEO_ENGINE || "wan").toLowerCase();
  if (engine === "seedance") {
    return { engine, cfg: loadSeedanceConfig() };
  }
  return { engine: "wan", cfg: loadWanConfig() };
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
  const fields = notionCfg.fields;
  const { dryRun, force, skipOverlay, skipSchemaCheck, ...lookup } = parseArgs();
  runSchemaCheck(skipSchemaCheck);
  const allowedStatuses = notionCfg.statuses?.allowed || [
    "ready_for_video",
    "video_in_progress",
    "ready_to_post",
    "video_failed",
  ];
  const videoStart = notionCfg.statuses?.video_start || "video_in_progress";

  const { page, objectId } = await resolvePage(fields, lookup);

  if (!pageMontageEnabled(page, fields)) {
    console.log(`Skip ${objectId}: «Монтаж» = НЕТ — объект только для базы, монтаж и Seedance не нужны.`);
    process.exit(0);
  }

  const { engine, cfg: videoCfg } = loadVideoEngine(page, fields);
  const engineFromNotion = pageVideoEngineId(page, fields);
  if (!engineFromNotion && !process.env.VIDEO_ENGINE) {
    throw new Error(
      "Видео-движок не выбран в Notion (Seedance 2.0 / Wan 2.7). " +
        "Выберите в TG-боте или задайте VIDEO_ENGINE в .env для ручного запуска."
    );
  }

  console.log(`\n=== Video Agent (${engine}): ${objectId} ===\n`);

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
    const doneStatus = notionCfg.statuses?.video_done || "ready_to_post";
    if (status !== doneStatus) {
      await setSeedanceUrl(page, fields, existingSeedance, doneStatus);
      console.log(`Status → ${doneStatus} (video already in Notion)`);
    }
    console.log("Seedance URL already set — skip (use --force to regenerate)");
    process.exit(0);
  }

  if (status && !allowedStatuses.includes(status)) {
    throw new Error(`Wrong status '${status}' — expected one of: ${allowedStatuses.join(", ")}`);
  }

  if (status !== videoStart) {
    await setMontageStart(page, fields, videoStart);
    console.log(`Status → ${videoStart}`);
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

  const selectedNames = await selectPhotosSeedance(imageItems, videoCfg);
  const image_keys = selectedNames.map((name) => `${objectId}/photos/${name}`);

  const durationLabel =
    engine === "wan"
      ? `${videoCfg.duration_seconds || 2}s × ${videoCfg.segment_seconds || 1.6}s segment`
      : `${videoCfg.duration_seconds || 14}s`;
  console.log(`Engine: ${engine}, duration: ${durationLabel}, resolution: ${videoCfg.resolution}`);
  console.log(`R2 photos: ${photoKeys.length}, selected: ${image_keys.length}`);
  for (const k of image_keys) console.log(`  - ${k}`);

  if (dryRun) {
    console.log("\nDRY RUN — no video render");
    console.log(
      JSON.stringify(
        {
          object_id: objectId,
          status,
          engine,
          duration_seconds: videoCfg.duration_seconds,
          resolution: videoCfg.resolution,
          image_count: image_keys.length,
          image_keys,
          overlay_meta: overlayMeta,
          ready: engine === "wan" ? isWanConfigured(videoCfg) : isSeedanceConfigured(videoCfg),
        },
        null,
        2
      )
    );
    return;
  }

  // Хук-обложка карусели — до видео, чтобы она была даже при падении рендера
  if (!skipOverlay && isTitleOverlayEnabled(videoCfg)) {
    try {
      const coverUrl = await makeHookCover({
        objectId,
        firstPhotoKey: photoKeys.sort()[0],
        overlayMeta,
        seedanceCfg: videoCfg,
      });
      console.log(`✓ hook cover (карусель): ${coverUrl}`);
    } catch (err) {
      console.warn(`Hook cover failed (non-blocking): ${err.message}`);
    }
  }

  // Дизайн-карусель для соц.сетей: отобранные фото → слайды (хук с ценой
  // только у FB-объектов, бейджи только на инфо-слайде) → R2 {id}/carousel/
  // + carousel_url в Notion.
  // Ошибка не блокирует видео (карусель можно доделать build_carousel.mjs).
  if (!skipOverlay) {
    try {
      const carousel = await buildCarousel({
        objectId,
        photoKeys: image_keys,
        overlayMeta,
        carouselMeta: pageCarouselMeta(page, fields),
        videoCfg,
      });
      await setCarouselUrl(page, fields, carousel.url);
      console.log(`✓ дизайн-карусель: ${carousel.slides} слайдов → ${carousel.url}`);
    } catch (err) {
      console.warn(`Carousel failed (non-blocking): ${err.message}`);
    }

    // «brand open home»: ВСЕ фото объекта с бренд-шаблоном
    // (лого + контакты, без хука и бейджей) → R2 {id}/brand_open_home/.
    try {
      const brand = await buildBrandFolder({ objectId, photoKeys });
      console.log(`✓ brand open home: ${brand.count} фото → ${brand.url}`);
    } catch (err) {
      console.warn(`Brand folder failed (non-blocking): ${err.message}`);
    }
  }

  try {
    // Конфиг/авторизация — внутри try: при ошибке setError снимет
    // video_in_progress, иначе объект зависает и блокирует очередь.
    if (engine === "wan") {
      if (!isWanConfigured(videoCfg)) {
        throw new Error("Wan not configured — set FAL_KEY in agent_3_director/.env");
      }
    } else if (!isSeedanceConfigured(videoCfg)) {
      throw new Error("Higgsfield not configured — run: higgsfield auth login  OR set HIGGSFIELD_MCP_ACCESS_TOKEN");
    }

    if (engine === "seedance") {
      const authCheck = spawnSync("node", [resolve(ROOT, "scripts/check_higgsfield_auth.mjs")], {
        encoding: "utf8",
        cwd: ROOT,
      });
      if (authCheck.status !== 0) {
        throw new Error(authCheck.stderr?.trim() || "Higgsfield auth check failed");
      }
    }

    const result =
      engine === "wan"
        ? await renderWan({
            object_id: objectId,
            image_keys,
            cfg: videoCfg,
            overlay_meta: overlayMeta,
            skip_overlay: skipOverlay,
          })
        : await renderSeedance({
            object_id: objectId,
            image_keys,
            cfg: videoCfg,
            overlay_meta: overlayMeta,
            skip_overlay: skipOverlay,
          });

    if (!result?.["9x16"]) {
      throw new Error(`${engine} returned no video URL`);
    }

    await setSeedanceUrl(page, fields, result["9x16"], notionCfg.statuses?.video_done || "ready_to_post");

    const summary = {
      object_id: objectId,
      page_id: page.id,
      engine,
      video: result,
    };
    console.log("\nVIDEO_DONE");
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

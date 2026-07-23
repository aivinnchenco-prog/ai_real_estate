#!/usr/bin/env node
/**
 * Дизайн-карусель объекта: отбор лучших фото → слайды 1080×1350 →
 * R2 {id}/carousel/ → ссылка на галерею в Notion (carousel_url).
 * Хук с ценой на слайде 1 — только для FB-объектов (F_*).
 *
 * Плюс «brand open home»: ВСЕ фото объекта дублируются с бренд-шаблоном
 * (лого + контакты, без хука и бейджей) в R2 {id}/brand_open_home/.
 *
 * Запускается:
 *   - из run_from_notion.mjs (вместе с монтажом);
 *   - из chain_runner.py для объектов с «Монтаж = НЕТ, Публикация = ДА»;
 *   - вручную: node scripts/build_carousel.mjs --object-id A_20260722_001
 *
 * Флаги:
 *   --force    перегенерировать, даже если carousel_url уже заполнен
 *   --dry-run  сохранить слайды в data/carousel_preview/{id}/ без R2 и Notion
 */
import { readFileSync } from "fs";
import { resolve } from "path";
import { loadEnv, ROOT } from "../env.mjs";
import {
  loadNotionConfig,
  queryByObjectId,
  pageVideoOverlayMeta,
  pageCarouselMeta,
  setCarouselUrl,
  setBrandOpenHomeUrl,
} from "../notionCrm.mjs";
import { listKeys } from "../r2list.mjs";
import { selectCarouselPhotos } from "../selectPhotosSeedance.mjs";
import { buildCarousel, buildBrandFolder } from "../carouselSlides.mjs";

function parseArgs() {
  const args = process.argv.slice(2);
  const out = { objectId: null, force: false, dryRun: false };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--object-id") out.objectId = args[++i];
    else if (a === "--force") out.force = true;
    else if (a === "--dry-run") out.dryRun = true;
  }
  if (!out.objectId) {
    console.error("Usage: node scripts/build_carousel.mjs --object-id <ID> [--force] [--dry-run]");
    process.exit(1);
  }
  return out;
}

function loadSelectConfig() {
  // Отбор карусели: 9 лучших, 30% экстерьера / 70% интерьера.
  try {
    return JSON.parse(readFileSync(resolve(ROOT, "config/seedance.json"), "utf8"));
  } catch {
    return { image_count: 9, max_per_category: 1 };
  }
}

async function main() {
  loadEnv();
  const { objectId, force, dryRun } = parseArgs();
  const notionCfg = loadNotionConfig();
  const fields = notionCfg.fields;

  const page = await queryByObjectId(objectId, fields);
  if (!page) throw new Error(`Object not found in Notion: ${objectId}`);

  const carouselField = fields.carousel_url || "carousel_url";
  const existing = page.properties?.[carouselField]?.url || null;
  if (existing && !force && !dryRun) {
    console.log(`Carousel already built: ${existing} (use --force to rebuild)`);
    return;
  }

  const prefix = `${objectId}/photos/`;
  const allKeys = await listKeys(prefix);
  const photoKeys = allKeys.filter(
    (k) => /\.(jpe?g|png|webp)$/i.test(k) && !k.includes("hook_cover")
  );
  if (!photoKeys.length) throw new Error(`No photos in R2 at ${prefix}`);

  const publicBase = process.env.R2_PUBLIC_BASE;
  if (!publicBase) throw new Error("R2_PUBLIC_BASE / CLOUDFLARE_PUBLIC_BASE_URL not set");

  const selectCfg = loadSelectConfig();
  const imageItems = photoKeys.map((key) => ({
    key: key.split("/").pop(),
    url: `${publicBase}/${key}`,
  }));
  const selectedNames = await selectCarouselPhotos(imageItems, selectCfg, {
    exteriorRatio: selectCfg.carousel_exterior_ratio ?? 0.3,
  });
  const selectedKeys = selectedNames.map((name) => `${objectId}/photos/${name}`);

  const overlayMeta = pageVideoOverlayMeta(page, fields, notionCfg.overlay_fields);
  const carouselMeta = pageCarouselMeta(page, fields);
  console.log(`Carousel meta: ${JSON.stringify(carouselMeta)}`);

  const { url, slides } = await buildCarousel({
    objectId,
    photoKeys: selectedKeys,
    overlayMeta,
    carouselMeta,
    videoCfg: selectCfg,
    outDir: dryRun ? resolve(ROOT, "data/carousel_preview", objectId) : null,
  });

  if (!dryRun) {
    await setCarouselUrl(page, fields, url);
    console.log(`✓ carousel_url → Notion: ${url}`);
  }
  console.log(`CAROUSEL_DONE ${objectId}: ${slides} слайдов → ${url}`);

  // «brand open home»: ВСЕ фото объекта с бренд-шаблоном (без хука и бейджей).
  // Ошибка не блокирует результат карусели.
  try {
    const brand = await buildBrandFolder({
      objectId,
      photoKeys,
      outDir: dryRun ? resolve(ROOT, "data/brand_preview", objectId) : null,
    });
    if (!dryRun) {
      await setBrandOpenHomeUrl(page, fields, brand.url);
      console.log(`✓ brand_open_home_url → Notion: ${brand.url}`);
    }
    console.log(`BRAND_DONE ${objectId}: ${brand.count} фото → ${brand.url}`);
  } catch (err) {
    console.warn(`Brand folder failed (non-blocking): ${err.message}`);
  }
}

main().catch((err) => {
  console.error("CAROUSEL FAILED:", err.message);
  process.exit(1);
});

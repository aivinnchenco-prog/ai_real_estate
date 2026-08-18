/**
 * Дизайн-карусель для соц.сетей: из отобранных фото объекта собираем
 * до 9 слайдов 1080×1350 (4:5).
 *
 * Объекты из FB (ID F_*) — годовой контракт: хук «в месяц · годовой контракт».
 * Объекты из Airbnb (ID A_*) — сезонная цена: хук «в месяц · {месяц}».
 *
 * Карусель (carousel/):
 *   слайд 1 — хук-обложка на лучшем «инстаграмном» фото;
 *   слайд 2 — фото + три бейджа;
 *   слайды 3+ — только логотип и контакты.
 *
 * Brand open home (brand_open_home/):
 *   ВСЕ фото с бренд-шаблоном (лого + контакты);
 *   на первом лучшем фото дополнительно хук с ценой.
 */
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "fs";
import { join, resolve } from "path";
import { tmpdir } from "os";
import { ROOT } from "./env.mjs";
import { downloadFromR2, uploadFileToR2 } from "./r2util.mjs";
import { renderHookCover, loadTitleOverlayConfig } from "./applyTitleOverlay.mjs";
import { isFbObject } from "./notionCrm.mjs";

export const CAROUSEL_PREFIX = "carousel";
export const BRAND_PREFIX = "brand_open_home";
const SLIDE_WIDTH = 1080;
const SLIDE_HEIGHT = 1350;
const MAX_SLIDES = 9;
const TG_LABEL = "TG @OpenHome_th";
const PHONE_LABEL = "+66 62 512 4001";

// «Приватные» типы жилья: своя вилла/дом → Private Pool / Private Parking.
// Кондо, апартаменты, квартиры → Shared (общие бассейн и парковка).
const PRIVATE_HOUSING_RE = /вилл|дом|таунхаус|villa|house|townhouse/i;

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const ICONS = {
  bed: `<svg viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.5">
    <path d="M3 18v-6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v6" stroke-linecap="round"/>
    <path d="M3 18h18" stroke-linecap="round"/>
    <path d="M5 10V7a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v3"/>
    <path d="M7 10v-1.5A1.5 1.5 0 0 1 8.5 7h2A1.5 1.5 0 0 1 12 8.5V10"/>
    <path d="M3 18v2M21 18v2" stroke-linecap="round"/>
  </svg>`,
  bath: `<svg viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.5">
    <path d="M4 12V5a2 2 0 0 1 2-2h1.5a2 2 0 0 1 2 2" stroke-linecap="round"/>
    <path d="M2 12h20v2a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5v-2z" stroke-linejoin="round"/>
    <path d="M6 19l-1 2M18 19l1 2" stroke-linecap="round"/>
  </svg>`,
  pool: `<svg viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.5">
    <circle cx="16.5" cy="5" r="1.8"/>
    <path d="M3 14c4-1 5 1.5 9-.5 2.5-1.3 4.5-3 9-1" stroke-linecap="round"/>
    <path d="M3 18.5c4-1 5 1.5 9-.5 2.5-1.3 4.5-3 9-1" stroke-linecap="round"/>
    <path d="M5 11l6-6 3 3.5" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M8 8l3.5 4" stroke-linecap="round"/>
  </svg>`,
  car: `<svg viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.5">
    <path d="M5 12l1.5-4.2A2 2 0 0 1 8.4 6.5h7.2a2 2 0 0 1 1.9 1.3L19 12" stroke-linejoin="round"/>
    <rect x="3.5" y="12" width="17" height="5.5" rx="1.6"/>
    <circle cx="7.5" cy="15" r="0.9" fill="#ffffff"/>
    <circle cx="16.5" cy="15" r="0.9" fill="#ffffff"/>
    <path d="M5.5 17.5v1.6M18.5 17.5v1.6" stroke-linecap="round"/>
  </svg>`,
};

function badgeHtml(icon, label) {
  return `<div class="badge">${ICONS[icon]}<div class="label">${escapeHtml(label)}</div></div>`;
}

export function isPrivateHousing(housingType) {
  return PRIVATE_HOUSING_RE.test(String(housingType || ""));
}

export function hasPool(meta) {
  const blob = [...(meta.amenities || []), meta.view || ""].join(" ").toLowerCase();
  return blob.includes("бассейн") || blob.includes("pool");
}

/** Объекты, спарсенные из Facebook (F_*): годовой контракт. */
export { isFbObject };

/**
 * Три бейджа инфо-слайда: спальни / бассейн (или санузлы) / парковка.
 * Private для виллы/дома, Shared для кондо/апартаментов.
 * Используются только на одном слайде карусели (первом после хука).
 */
export function slideBadges(meta) {
  const priv = isPrivateHousing(meta.housing_type);
  const badges = [];

  const bedrooms = String(meta.bedrooms || "").trim();
  if (bedrooms) {
    badges.push(badgeHtml("bed", `${bedrooms} ${priv ? "Private " : ""}Bedrooms`));
  }

  if (hasPool(meta)) {
    badges.push(badgeHtml("pool", priv ? "Private Pool" : "Shared Pool"));
  } else {
    const bathrooms = String(meta.bathrooms || "").trim();
    if (bathrooms) badges.push(badgeHtml("bath", `${bathrooms} Bathrooms`));
  }

  badges.push(badgeHtml("car", priv ? "Private Parking" : "Shared Parking"));
  return badges.join("\n");
}

function fillSlideTemplate(template, { photoPath, badges }) {
  const photoB64 = readFileSync(photoPath).toString("base64");
  const ext = /\.png$/i.test(photoPath) ? "png" : "jpeg";
  return template
    .replaceAll("{{PHOTO_SRC}}", `data:image/${ext};base64,${photoB64}`)
    .replaceAll("{{BADGES}}", badges)
    .replaceAll("{{TG}}", escapeHtml(TG_LABEL))
    .replaceAll("{{PHONE}}", escapeHtml(PHONE_LABEL));
}

/** Рендер набора слайдов одним браузером (Playwright). */
async function renderSlides(slides, tmpDir) {
  const templatePath = resolve(ROOT, "templates/carousel_slide.html");
  const template = readFileSync(templatePath, "utf8");

  const { chromium } = await import("playwright");
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: SLIDE_WIDTH, height: SLIDE_HEIGHT },
    });
    for (const slide of slides) {
      const html = fillSlideTemplate(template, slide);
      const htmlPath = join(tmpDir, `${slide.name}.html`);
      writeFileSync(htmlPath, html, "utf8");
      await page.goto(`file://${htmlPath}`);
      await page.waitForLoadState("load");
      await page.waitForTimeout(160);
      await page.screenshot({ path: slide.outPath, type: "jpeg", quality: 90 });
    }
  } finally {
    await browser.close();
  }
}

function slideName(n) {
  return `slide_${String(n).padStart(2, "0")}.jpg`;
}

function galleryIndexHtml(objectId, names, label = "дизайн-карусель") {
  const imgs = names
    .map((n) => `    <a href="${n}"><img src="${n}" alt="${escapeHtml(objectId)}"></a>`)
    .join("\n");
  return `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(objectId)} — ${escapeHtml(label)}</title>
<style>
  body { margin: 0; padding: 24px; background: #101010; font-family: sans-serif; }
  h1 { color: #eee; font-size: 18px; font-weight: 600; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
  img { width: 100%; border-radius: 10px; display: block; }
</style>
</head>
<body>
<h1>${escapeHtml(objectId)} — ${escapeHtml(label)} (${names.length} шт.)</h1>
<div class="grid">
${imgs}
</div>
</body>
</html>
`;
}

/**
 * Собрать карусель и залить в R2.
 *
 * @param objectId      ID объекта (папка в R2)
 * @param photoKeys     R2-ключи ОТОБРАННЫХ фото (лучшие «инстаграмные», по порядку)
 * @param overlayMeta   { title_phrase, bedrooms, price, period, district } — для хук-слайда
 * @param carouselMeta  { bedrooms, bathrooms, housing_type, amenities, view } — для бейджей
 * @param videoCfg      конфиг движка (для title-overlay конфига хука)
 * @param outDir        локальная папка вместо R2 (для --dry-run)
 * @returns { url, slides } — url галереи carousel/index.html
 */
export async function buildCarousel({
  objectId,
  photoKeys,
  overlayMeta,
  carouselMeta,
  videoCfg = {},
  outDir = null,
}) {
  if (!photoKeys?.length) throw new Error("Carousel: нет отобранных фото");

  // Хук с ценой — на лучшем фото, если в overlayMeta есть цена (FB и Airbnb).
  const withHook = Boolean(overlayMeta?.price && overlayMeta?.period);

  const tmpDir = mkdtempSync(join(tmpdir(), "carousel-"));
  try {
    const maxPhotos = withHook ? MAX_SLIDES - 1 : MAX_SLIDES;
    const photoLocals = [];
    for (let i = 0; i < photoKeys.length && photoLocals.length < maxPhotos; i++) {
      const local = join(tmpDir, `photo_${i}${photoKeys[i].slice(photoKeys[i].lastIndexOf("."))}`);
      try {
        await downloadFromR2(photoKeys[i], local);
        photoLocals.push(local);
      } catch (err) {
        console.warn(`Carousel: не скачалось ${photoKeys[i]}: ${err.message}`);
      }
    }
    if (!photoLocals.length) throw new Error("Carousel: не удалось скачать ни одного фото");

    const names = [];
    let firstPhotoSlideNo = 1;

    // --- слайд 1 (если есть цена): хук-обложка на лучшем фото ---
    // Не reuse {id}/hook_cover.jpg — там обложка для видео/Agent6; может быть без месяца.
    // Карусель для постинга всегда рендерит slide_01 из overlayMeta.
    if (withHook) {
      const hookLocal = join(tmpDir, slideName(1));
      const overlayCfg = loadTitleOverlayConfig(videoCfg);
      await renderHookCover({
        photoPath: photoLocals[0],
        outputPath: hookLocal,
        meta: overlayMeta,
        cfg: overlayCfg,
      });
      names.push(slideName(1));
      firstPhotoSlideNo = 2;
    }

    // --- фото-слайды: первый — с тремя бейджами, остальные — только лого + контакты ---
    const slides = [];
    photoLocals.forEach((photoPath, idx) => {
      const n = firstPhotoSlideNo + idx;
      if (n > MAX_SLIDES) return;
      slides.push({
        name: slideName(n).replace(/\.jpg$/, ""),
        photoPath,
        badges: idx === 0 ? slideBadges(carouselMeta) : "",
        outPath: join(tmpDir, slideName(n)),
      });
      names.push(slideName(n));
    });
    await renderSlides(slides, tmpDir);

    const indexHtml = galleryIndexHtml(objectId, names, "дизайн-карусель");
    const indexLocal = join(tmpDir, "index.html");
    writeFileSync(indexLocal, indexHtml, "utf8");

    // --- результат: локальная папка (dry-run) или R2 ---
    if (outDir) {
      const { mkdirSync, copyFileSync } = await import("fs");
      mkdirSync(outDir, { recursive: true });
      for (const n of names) copyFileSync(join(tmpDir, n), join(outDir, n));
      copyFileSync(indexLocal, join(outDir, "index.html"));
      return { url: join(outDir, "index.html"), slides: names.length };
    }

    for (const n of names) {
      const publicUrl = await uploadFileToR2(
        join(tmpDir, n), `${objectId}/${CAROUSEL_PREFIX}/${n}`, "image/jpeg"
      );
      console.log(`  ↑ ${publicUrl}`);
    }
    const url = await uploadFileToR2(
      indexLocal, `${objectId}/${CAROUSEL_PREFIX}/index.html`, "text/html; charset=utf-8"
    );
    return { url, slides: names.length };
  } finally {
    rmSync(tmpDir, { recursive: true, force: true });
  }
}

/**
 * «brand open home»: дублировать ВСЕ фото объекта с бренд-шаблоном
 * (градиент, лого OpenHome, колонтитул с контактами).
 * На heroPhotoKey (лучшее «инстаграмное» фото) дополнительно хук с ценой.
 *
 * @param objectId      ID объекта (папка в R2)
 * @param photoKeys     R2-ключи ВСЕХ фото объекта ({id}/photos/*)
 * @param heroPhotoKey  R2-ключ лучшего фото для хука (первое из отбора карусели)
 * @param overlayMeta   метаданные хука (цена, период, район…)
 * @param videoCfg      конфиг движка (для title-overlay конфига хука)
 * @param outDir        локальная папка вместо R2 (для --dry-run)
 * @returns { url, count } — url галереи brand_open_home/index.html
 */
export async function buildBrandFolder({
  objectId,
  photoKeys,
  heroPhotoKey = null,
  overlayMeta = null,
  videoCfg = {},
  outDir = null,
}) {
  if (!photoKeys?.length) throw new Error("Brand: нет фото объекта");

  const withHook = Boolean(
    heroPhotoKey && overlayMeta?.price && overlayMeta?.period
  );
  const overlayCfg = loadTitleOverlayConfig(videoCfg);

  const tmpDir = mkdtempSync(join(tmpdir(), "brand-"));
  try {
    const slides = [];
    const names = [];
    for (const key of photoKeys) {
      const base = key.split("/").pop();
      const srcLocal = join(tmpDir, `src_${base}`);
      try {
        await downloadFromR2(key, srcLocal);
      } catch (err) {
        console.warn(`Brand: не скачалось ${key}: ${err.message}`);
        continue;
      }

      let photoForSlide = srcLocal;
      if (withHook && key === heroPhotoKey) {
        const hookLocal = join(tmpDir, `hook_${base.replace(/\.[^.]+$/, "")}.jpg`);
        try {
          await renderHookCover({
            photoPath: srcLocal,
            outputPath: hookLocal,
            meta: overlayMeta,
            cfg: overlayCfg,
          });
          photoForSlide = hookLocal;
          console.log(`  brand hook cover: ${base}`);
        } catch (err) {
          console.warn(`Brand: hook cover failed for ${base}: ${err.message}`);
        }
      }

      const outName = base.replace(/\.(jpe?g|png|webp)$/i, "") + ".jpg";
      slides.push({
        name: `brand_${outName.replace(/\.jpg$/, "")}`,
        photoPath: photoForSlide,
        badges: "",
        outPath: join(tmpDir, outName),
      });
      names.push(outName);
    }
    if (!slides.length) throw new Error("Brand: не удалось скачать ни одного фото");

    await renderSlides(slides, tmpDir);

    const indexHtml = galleryIndexHtml(objectId, names, "brand open home");
    const indexLocal = join(tmpDir, "brand_index.html");
    writeFileSync(indexLocal, indexHtml, "utf8");

    if (outDir) {
      const { mkdirSync, copyFileSync } = await import("fs");
      mkdirSync(outDir, { recursive: true });
      for (const n of names) copyFileSync(join(tmpDir, n), join(outDir, n));
      copyFileSync(indexLocal, join(outDir, "index.html"));
      return { url: join(outDir, "index.html"), count: names.length };
    }

    for (const n of names) {
      const publicUrl = await uploadFileToR2(
        join(tmpDir, n), `${objectId}/${BRAND_PREFIX}/${n}`, "image/jpeg"
      );
      console.log(`  ↑ ${publicUrl}`);
    }
    const url = await uploadFileToR2(
      indexLocal, `${objectId}/${BRAND_PREFIX}/index.html`, "text/html; charset=utf-8"
    );
    return { url, count: names.length };
  } finally {
    rmSync(tmpDir, { recursive: true, force: true });
  }
}

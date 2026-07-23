/**
 * Дизайн-карусель для соц.сетей: из отобранных фото объекта собираем
 * до 9 слайдов 1080×1350 (4:5):
 *
 *   слайд 1 — хук-обложка (шаблон hook_card, как в видео);
 *   слайд 2 — фото + бейджи «спальни / санузлы»;
 *   слайды 3+ — фото + бейджи «спальни / бассейн / парковка»
 *              (Private для виллы/дома, Shared для кондо/апартаментов).
 *
 * На каждом слайде: затемняющий градиент снизу, прозрачный логотип OpenHome,
 * колонтитул: слева «TG @OpenHome_th», справа телефон.
 *
 * Готовые слайды льются в R2: {object_id}/carousel/slide_NN.jpg
 * + {object_id}/carousel/index.html (галерея — её URL пишется в Notion
 * в колонку carousel_url; публикатор Агента 4 берёт слайды из неё).
 */
import { readFileSync, writeFileSync, mkdtempSync, rmSync, existsSync } from "fs";
import { join, resolve } from "path";
import { tmpdir } from "os";
import { ROOT } from "./env.mjs";
import { downloadFromR2, uploadFileToR2 } from "./r2util.mjs";
import { renderHookCover, loadTitleOverlayConfig } from "./applyTitleOverlay.mjs";

export const CAROUSEL_PREFIX = "carousel";
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

/**
 * Набор бейджей для слайда.
 * slideNo — номер слайда в карусели (2 — первый после хука).
 */
export function slideBadges(meta, slideNo) {
  const priv = isPrivateHousing(meta.housing_type);
  const badges = [];

  const bedrooms = String(meta.bedrooms || "").trim();
  const bedroomsLabel = bedrooms
    ? `${bedrooms} ${priv ? "Private " : ""}Bedrooms`
    : null;

  const bathrooms = String(meta.bathrooms || "").trim();
  const bathroomsLabel = bathrooms ? `${bathrooms} Bathrooms` : null;

  if (slideNo === 2) {
    // Слайд 2 — комнаты и санузлы из таблицы
    if (bedroomsLabel) badges.push(badgeHtml("bed", bedroomsLabel));
    if (bathroomsLabel) badges.push(badgeHtml("bath", bathroomsLabel));
  } else {
    if (bedroomsLabel) badges.push(badgeHtml("bed", bedroomsLabel));
    if (hasPool(meta)) {
      badges.push(badgeHtml("pool", priv ? "Private Pool" : "Shared Pool"));
    } else if (bathroomsLabel) {
      badges.push(badgeHtml("bath", bathroomsLabel));
    }
    badges.push(badgeHtml("car", priv ? "Private Parking" : "Shared Parking"));
  }
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

function galleryIndexHtml(objectId, names) {
  const imgs = names
    .map((n) => `    <a href="${n}"><img src="${n}" alt="${escapeHtml(objectId)}"></a>`)
    .join("\n");
  return `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(objectId)} — карусель</title>
<style>
  body { margin: 0; padding: 24px; background: #101010; font-family: sans-serif; }
  h1 { color: #eee; font-size: 18px; font-weight: 600; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
  img { width: 100%; border-radius: 10px; display: block; }
</style>
</head>
<body>
<h1>${escapeHtml(objectId)} — дизайн-карусель (${names.length} слайдов)</h1>
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

  const tmpDir = mkdtempSync(join(tmpdir(), "carousel-"));
  try {
    // --- слайд 1: хук-обложка (готовая {id}/hook_cover.jpg или рендерим) ---
    const hookLocal = join(tmpDir, slideName(1));
    let hookReady = false;
    try {
      await downloadFromR2(`${objectId}/hook_cover.jpg`, hookLocal);
      hookReady = true;
    } catch {
      /* обложки ещё нет — рендерим ниже */
    }

    // Фото для слайдов скачиваем заранее (первое — база для хука, если нужен рендер)
    const photoLocals = [];
    for (let i = 0; i < photoKeys.length && photoLocals.length < MAX_SLIDES; i++) {
      const local = join(tmpDir, `photo_${i}${photoKeys[i].slice(photoKeys[i].lastIndexOf("."))}`);
      try {
        await downloadFromR2(photoKeys[i], local);
        photoLocals.push(local);
      } catch (err) {
        console.warn(`Carousel: не скачалось ${photoKeys[i]}: ${err.message}`);
      }
    }
    if (!photoLocals.length) throw new Error("Carousel: не удалось скачать ни одного фото");

    if (!hookReady) {
      const overlayCfg = loadTitleOverlayConfig(videoCfg);
      await renderHookCover({
        photoPath: photoLocals[0],
        outputPath: hookLocal,
        meta: overlayMeta,
        cfg: overlayCfg,
      });
    }

    // --- слайды 2..N: фото + бейджи + колонтитул ---
    const slides = [];
    const photoSlides = photoLocals.slice(0, MAX_SLIDES - 1);
    photoSlides.forEach((photoPath, idx) => {
      const n = idx + 2;
      slides.push({
        name: slideName(n).replace(/\.jpg$/, ""),
        photoPath,
        badges: slideBadges(carouselMeta, n),
        outPath: join(tmpDir, slideName(n)),
      });
    });
    await renderSlides(slides, tmpDir);

    const names = [slideName(1), ...photoSlides.map((_, idx) => slideName(idx + 2))];
    const indexHtml = galleryIndexHtml(objectId, names);
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

    let url = null;
    for (const n of names) {
      const publicUrl = await uploadFileToR2(
        join(tmpDir, n), `${objectId}/${CAROUSEL_PREFIX}/${n}`, "image/jpeg"
      );
      console.log(`  ↑ ${publicUrl}`);
    }
    url = await uploadFileToR2(
      indexLocal, `${objectId}/${CAROUSEL_PREFIX}/index.html`, "text/html; charset=utf-8"
    );
    return { url, slides: names.length };
  } finally {
    rmSync(tmpDir, { recursive: true, force: true });
  }
}

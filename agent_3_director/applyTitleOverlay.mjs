import { execSync, spawnSync } from "child_process";
import { createRequire } from "module";
import { readFileSync, existsSync, writeFileSync, mkdtempSync } from "fs";
import { resolve, join } from "path";
import { tmpdir } from "os";
import { ROOT } from "./env.mjs";

const require = createRequire(import.meta.url);

export function loadTitleOverlayConfig(seedanceCfg) {
  const rel = seedanceCfg?.title_overlay?.config || "config/title-overlay.json";
  const path = resolve(ROOT, rel);
  if (!existsSync(path)) return { enabled: false };
  const cfg = JSON.parse(readFileSync(path, "utf8"));
  if (seedanceCfg?.title_overlay?.enabled === false) return { ...cfg, enabled: false };
  return cfg;
}

export function isTitleOverlayEnabled(seedanceCfg) {
  const cfg = loadTitleOverlayConfig(seedanceCfg);
  return cfg.enabled !== false;
}

function resolveFfmpeg() {
  if (spawnSync("which", ["ffmpeg"]).status === 0) return "ffmpeg";
  try {
    return require("@ffmpeg-installer/ffmpeg").path;
  } catch {
    return null;
  }
}

function resolveFfprobe(ffmpegPath) {
  if (spawnSync("which", ["ffprobe"]).status !== 0) return null;
  return "ffprobe";
}

function getVideoSize(ffmpeg, videoPath) {
  const ffprobe = resolveFfprobe(ffmpeg);
  if (ffprobe) {
    try {
      const out = execSync(
        `"${ffprobe}" -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0:s=x "${videoPath}"`,
        { encoding: "utf8" }
      ).trim();
      const [width, height] = out.split("x").map(Number);
      if (width && height) return { width, height };
    } catch {
      // fall through
    }
  }

  try {
    execSync(`"${ffmpeg}" -i "${videoPath}"`, { encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] });
  } catch (err) {
    const probe = `${err.stdout || ""}${err.stderr || ""}`;
    const match = probe.match(/,\s*(\d{2,5})x(\d{2,5})/);
    if (!match) throw new Error(`Could not read video size: ${videoPath}`);
    return { width: Number(match[1]), height: Number(match[2]) };
  }
  throw new Error(`Could not read video size: ${videoPath}`);
}

function buildOverlayFilter(cfg, videoWidth, videoHeight) {
  const enable =
    cfg.duration_seconds == null ? "" : `:enable='lte(t,${cfg.duration_seconds})'`;
  return `[1]scale=${videoWidth}:${videoHeight}[ov];[0:v][ov]overlay=0:0:format=auto${enable}[v]`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fillHookTemplate(html, meta) {
  return html
    .replaceAll("{{TITLE_PHRASE}}", escapeHtml(meta.title_phrase))
    .replaceAll("{{BEDROOMS}}", escapeHtml(meta.bedrooms))
    .replaceAll("{{PRICE}}", escapeHtml(meta.price))
    .replaceAll("{{PERIOD}}", escapeHtml(meta.period))
    .replaceAll("{{DISTRICT}}", escapeHtml(meta.district));
}

async function renderHookCardPng({ templatePath, meta, outPath, width, height }) {
  const template = readFileSync(templatePath, "utf8");
  const html = fillHookTemplate(template, meta);
  const tmpDir = mkdtempSync(join(tmpdir(), "hook-card-"));
  const htmlPath = join(tmpDir, "card.html");
  writeFileSync(htmlPath, html, "utf8");

  const { chromium } = await import("playwright");
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width, height } });
    await page.goto(`file://${htmlPath}`);
    await page.waitForLoadState("load");
    await page.waitForTimeout(150);
    await page.screenshot({ path: outPath, omitBackground: true });
  } finally {
    await browser.close();
  }
}

/**
 * Хук-обложка карусели: первое фото объекта + карточка-хук поверх.
 * Формат 4:5 (1080×1350) — стандарт вертикального поста Instagram,
 * карточка рендерится сразу под этот размер (она прибита к низу-левому краю).
 */
export async function renderHookCover({ photoPath, outputPath, meta, cfg, width = 1080, height = 1350 }) {
  if (!meta?.title_phrase && !meta?.price && !meta?.district) {
    throw new Error("Hook cover: empty CRM metadata");
  }
  const ffmpeg = resolveFfmpeg();
  if (!ffmpeg) throw new Error("Hook cover: ffmpeg not found");

  const templatePath = resolve(ROOT, cfg.template || "templates/hook_card.html");
  if (!existsSync(templatePath)) {
    throw new Error(`Hook cover template not found: ${templatePath}`);
  }

  const overlayPng = outputPath.replace(/\.[a-z]+$/i, "_overlay.png");
  await renderHookCardPng({ templatePath, meta, outPath: overlayPng, width, height });

  const filter =
    `[0]scale=${width}:${height}:force_original_aspect_ratio=increase,` +
    `crop=${width}:${height}[bg];[1]scale=${width}:${height}[ov];` +
    `[bg][ov]overlay=0:0:format=auto[v]`;

  execSync(
    [
      `"${ffmpeg}"`,
      "-y",
      `-i "${photoPath}"`,
      `-i "${overlayPng}"`,
      `-filter_complex "${filter}"`,
      '-map "[v]"',
      "-frames:v 1",
      "-q:v 2",
      "-loglevel error",
      `"${outputPath}"`,
    ].join(" "),
    { stdio: "inherit" }
  );

  return { cover: outputPath, meta };
}

export async function applyTitleOverlay({ inputPath, outputPath, meta, cfg }) {
  if (!meta?.title_phrase && !meta?.price && !meta?.district) {
    throw new Error("Title overlay: empty CRM metadata");
  }

  const ffmpeg = resolveFfmpeg();
  if (!ffmpeg) throw new Error("Title overlay: ffmpeg not found (brew install ffmpeg)");

  const { width: videoWidth, height: videoHeight } = getVideoSize(ffmpeg, inputPath);

  const templatePath = resolve(ROOT, cfg.template || "templates/hook_card.html");
  if (!existsSync(templatePath)) {
    throw new Error(`Title overlay template not found: ${templatePath}`);
  }

  const renderWidth = cfg.width || 1080;
  const renderHeight = cfg.height || 1920;
  const overlayPng = outputPath.replace(/\.mp4$/, "_overlay.png");

  await renderHookCardPng({
    templatePath,
    meta,
    outPath: overlayPng,
    width: renderWidth,
    height: renderHeight,
  });

  console.log(`Overlay: scale ${renderWidth}x${renderHeight} PNG → ${videoWidth}x${videoHeight} video`);

  const filter = buildOverlayFilter(cfg, videoWidth, videoHeight);

  const cmd = [
    `"${ffmpeg}"`,
    "-y",
    `-i "${inputPath}"`,
    `-i "${overlayPng}"`,
    `-filter_complex "${filter}"`,
    '-map "[v]"',
    "-map 0:a?",
    "-c:v libx264",
    "-crf 20",
    "-preset fast",
    "-pix_fmt yuv420p",
    "-c:a copy",
    "-movflags +faststart",
    "-loglevel error",
    `"${outputPath}"`,
  ].join(" ");

  execSync(cmd, { stdio: "inherit" });

  return {
    overlay_applied: true,
    template: templatePath,
    meta,
  };
}

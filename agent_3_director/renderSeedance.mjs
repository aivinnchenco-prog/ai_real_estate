/**
 * Seedance 2.0 via Higgsfield — до 9 ref-фото → один 9:16 ролик (без склейки).
 * Providers (priority): CLI (OAuth) → MCP (OAuth Bearer) → legacy REST API.
 */
import { execSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import {
  buildPrompt,
  downloadToFile,
  generateSeedanceClip,
} from "./higgsfieldClient.mjs";
import { generateSeedanceViaCli, isCliAuthenticated } from "./higgsfieldCli.mjs";
import { generateSeedanceViaMcp, isMcpConfigured } from "./higgsfieldMcp.mjs";
import { generateSeedanceViaPlatform, isPlatformConfigured } from "./higgsfieldPlatform.mjs";
import { downloadFromR2, publicUrlForKey, uploadVideoToR2 } from "./r2util.mjs";
import { listMusicTracks, pickMusicTrackRoundRobin } from "./musicRotation.mjs";
import {
  applyTitleOverlay,
  isTitleOverlayEnabled,
  loadTitleOverlayConfig,
  resolveFfmpeg,
} from "./applyTitleOverlay.mjs";

function resolveProvider(cfg) {
  const explicit = process.env.SEEDANCE_PROVIDER || cfg.provider;
  if (explicit && explicit !== "auto") return explicit;

  if (isCliAuthenticated(cfg)) return "cli";
  if (isMcpConfigured(cfg)) return "mcp";
  if (isPlatformConfigured()) return "api";
  return "api";
}

async function generateClip(provider, imageUrls, prompt, cfg) {
  if (provider === "cli") {
    return generateSeedanceViaCli({ imageUrls, prompt, cfg });
  }
  if (provider === "mcp") {
    return generateSeedanceViaMcp({ imageUrls, prompt, cfg });
  }
  if (provider === "api" && isPlatformConfigured()) {
    return generateSeedanceViaPlatform({ imageUrls, prompt, cfg });
  }
  return generateSeedanceClip({ imageUrls, prompt, cfg });
}

/**
 * Мини-монтаж: наложение вирусной аудиодорожки из R2 (папка music/) на готовый
 * ролик — Metricool не даёт выбирать музыку при постинге, поэтому звук
 * вшиваем в файл. Треки по кругу; очередь в R2 music/_rotation.json (не в APP).
 */
export async function addMusicTrack({ objectId, videoPath, outPath, cfg, tmpDir }) {
  const music = cfg.music || {};
  if (music.enabled === false) return false;

  const prefix = music.r2_prefix || "music/";
  const tracks = await listMusicTracks(prefix, music.rotation_key);
  if (!tracks.length) {
    console.warn(`Music: нет треков в R2 ${prefix} — ролик остаётся без музыки`);
    return false;
  }

  const trackKey = await pickMusicTrackRoundRobin(objectId, tracks, music);
  const trackLocal = path.join(tmpDir, `music${path.extname(trackKey)}`);
  await downloadFromR2(trackKey, trackLocal);

  const ffmpeg = resolveFfmpeg();
  if (!ffmpeg) throw new Error("Music mix: ffmpeg not found");

  execSync(
    [
      `"${ffmpeg}"`,
      "-y",
      `-i "${videoPath}"`,
      `-i "${trackLocal}"`,
      "-map 0:v -map 1:a",
      "-c:v copy -c:a aac -b:a 192k",
      "-shortest",
      "-movflags +faststart",
      "-loglevel error",
      `"${outPath}"`,
    ].join(" "),
    { stdio: "inherit" }
  );
  console.log(`Step 2.5/3: музыка наложена — ${trackKey}`);
  return true;
}

export function isSeedanceConfigured(cfg) {
  if (cfg.enabled === false) return false;
  return (
    isCliAuthenticated(cfg) ||
    isMcpConfigured(cfg) ||
    isPlatformConfigured()
  );
}

export async function renderSeedance({ object_id, image_keys, cfg, overlay_meta, skip_overlay = false }) {
  if (!isSeedanceConfigured(cfg)) {
    console.warn("Seedance skipped: no Higgsfield CLI auth, MCP token, or API credentials");
    return null;
  }

  if (!image_keys?.length) {
    throw new Error("Seedance: no images selected");
  }

  const provider = resolveProvider(cfg);
  const maxImages = cfg.max_images_per_request || 9;
  if (image_keys.length > maxImages) {
    console.warn(`Seedance: using first ${maxImages} of ${image_keys.length} images`);
  }
  const imageUrls = image_keys.slice(0, maxImages).map(publicUrlForKey);

  console.log(`Seedance provider: ${provider}`);
  console.log(
    `Seedance: 1 clip, ${imageUrls.length} images, ${cfg.duration_seconds || 14}s`
  );

  const template =
    cfg.prompt_template ||
    "Vertical 9:16 real estate video from references {tags}: a montage of {count} separate static shots, one per reference, in order. Each shot starts as an exact frozen replica of its reference image, then a very slow smooth stabilized dolly-in straight forward only. Plain hard cuts between shots, no morphing, no invented transitions or objects, no people. Photorealistic.";

  const prompt = buildPrompt(template, imageUrls.length);
  const tmpDir = `/tmp/seedance-${object_id}-${Date.now()}`;
  fs.mkdirSync(tmpDir, { recursive: true });

  try {
    const remoteUrl = await generateClip(provider, imageUrls, prompt, cfg);

    const rawLocal = path.join(tmpDir, "video_seedance_raw.mp4");
    await downloadToFile(remoteUrl, rawLocal);
    console.log("Step 1/3: Higgsfield clip downloaded");

    const finalLocal = path.join(tmpDir, "video_seedance_9x16.mp4");
    let overlayResult = null;

    if (skip_overlay) {
      console.log("Step 2/3: overlay skipped (--skip-overlay)");
      fs.copyFileSync(rawLocal, finalLocal);
    } else if (isTitleOverlayEnabled(cfg)) {
      if (!overlay_meta) {
        throw new Error("Title overlay: CRM metadata required (pass overlay_meta from Notion)");
      }
      const overlayCfg = loadTitleOverlayConfig(cfg);
      console.log("Step 2/3: hook-card overlay", overlay_meta);
      await applyTitleOverlay({
        inputPath: rawLocal,
        outputPath: finalLocal,
        meta: overlay_meta,
        cfg: overlayCfg,
      });
      overlayResult = overlay_meta;
    } else {
      fs.copyFileSync(rawLocal, finalLocal);
    }

    // Мини-монтаж: вирусная аудиодорожка из R2 (некритично при ошибке)
    let uploadLocal = finalLocal;
    try {
      const withMusic = path.join(tmpDir, "video_seedance_music.mp4");
      if (await addMusicTrack({ objectId: object_id, videoPath: finalLocal, outPath: withMusic, cfg, tmpDir })) {
        uploadLocal = withMusic;
      }
    } catch (err) {
      console.warn(`Music mix failed (non-blocking): ${err.message}`);
    }

    console.log("Step 3/3: upload to R2");

    const r2Key = `${object_id}/video_seedance_9x16.mp4`;
    const url = await uploadVideoToR2(uploadLocal, r2Key);
    console.log(`✓ seedance 9x16: ${url}`);
    return { "9x16": url, image_count: imageUrls.length, provider, overlay: overlayResult };
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

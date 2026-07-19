/**
 * Wan 2.7 @ 720p via fal.ai — до 9 фото → клипы → склейка (wan_reel.py).
 */
import { spawnSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { ROOT } from "./env.mjs";
import { downloadFromR2, uploadVideoToR2 } from "./r2util.mjs";
import {
  applyTitleOverlay,
  isTitleOverlayEnabled,
  loadTitleOverlayConfig,
} from "./applyTitleOverlay.mjs";
import { addMusicTrack } from "./renderSeedance.mjs";

function resolveFalKey() {
  return process.env.FAL_KEY?.trim() || "";
}

function resolveWanScript(cfg) {
  const rel = cfg.wan_reel_script || "scripts/wan_reel.py";
  const script = path.resolve(ROOT, rel);
  if (!fs.existsSync(script)) {
    throw new Error(`wan_reel.py not found: ${script}`);
  }
  return script;
}

export function isWanConfigured(cfg) {
  if (cfg.enabled === false) return false;
  return !!resolveFalKey();
}

export async function renderWan({
  object_id,
  image_keys,
  cfg,
  overlay_meta,
  skip_overlay = false,
}) {
  const falKey = resolveFalKey();
  if (!falKey) {
    console.warn("Wan skipped: FAL_KEY not set");
    return null;
  }

  if (!image_keys?.length) {
    throw new Error("Wan: no images selected");
  }

  const maxImages = cfg.max_images_per_request || cfg.image_count || 9;
  const keys = image_keys.slice(0, maxImages);
  const resolution = cfg.resolution || "720p";
  const duration = cfg.duration_seconds || 2;
  const segment = cfg.segment_seconds || 1.6;

  console.log(`Wan 2.7: ${keys.length} photos × ${duration}s @ ${resolution}, segment ${segment}s`);

  const tmpDir = `/tmp/wan-${object_id}-${Date.now()}`;
  const photosDir = path.join(tmpDir, "photos");
  const outDir = path.join(tmpDir, "out");
  fs.mkdirSync(photosDir, { recursive: true });

  try {
    for (let i = 0; i < keys.length; i++) {
      const local = path.join(photosDir, `photo_${String(i + 1).padStart(3, "0")}.jpg`);
      await downloadFromR2(keys[i], local);
    }

    const script = resolveWanScript(cfg);
    const prompt = cfg.prompt || "";
    const args = [
      script,
      "--photos", photosDir,
      "--out", outDir,
      "--limit", String(keys.length),
      "--segment", String(segment),
      "--duration", String(duration),
      "--resolution", resolution,
    ];
    if (prompt) args.push("--prompt", prompt);

    const proc = spawnSync(process.env.WAN_PYTHON || "python3", args, {
        stdio: "inherit",
        cwd: path.dirname(script),
        env: { ...process.env, FAL_KEY: falKey },
      }
    );
    if (proc.status !== 0) {
      throw new Error(`wan_reel.py exited with code ${proc.status}`);
    }

    const rawLocal = path.join(outDir, "reel_wan.mp4");
    if (!fs.existsSync(rawLocal)) {
      throw new Error("Wan: reel_wan.mp4 not produced");
    }
    console.log("Step 1/3: Wan reel assembled");

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

    let uploadLocal = finalLocal;
    try {
      const withMusic = path.join(tmpDir, "video_wan_music.mp4");
      if (await addMusicTrack({ objectId: object_id, videoPath: finalLocal, outPath: withMusic, cfg, tmpDir })) {
        uploadLocal = withMusic;
      }
    } catch (err) {
      console.warn(`Music mix failed (non-blocking): ${err.message}`);
    }

    console.log("Step 3/3: upload to R2");
    const r2Key = `${object_id}/video_seedance_9x16.mp4`;
    const url = await uploadVideoToR2(uploadLocal, r2Key);
    console.log(`✓ wan 9x16: ${url}`);
    return {
      "9x16": url,
      image_count: keys.length,
      provider: "fal-wan-2.7",
      resolution,
      overlay: overlayResult,
    };
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

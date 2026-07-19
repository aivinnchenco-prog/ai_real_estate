/**
 * Seedance 2.0 via Higgsfield — 13 diverse photos → 9:16 video.
 * Providers (priority): CLI (OAuth) → MCP (OAuth Bearer) → legacy REST API.
 * Ошибка Seedance не блокирует Agent 3 (reel обязателен).
 */
import { execSync, spawnSync } from "child_process";
import { createRequire } from "module";
import * as fs from "fs";
import * as path from "path";
import {
  buildPrompt,
  downloadToFile,
  generateSeedanceClip,
} from "./higgsfieldClient.mjs";
import { generateSeedanceViaCli, isCliAuthenticated } from "./higgsfieldCli.mjs";
import { generateSeedanceViaMcp, isMcpConfigured } from "./higgsfieldMcp.mjs";
import { publicUrlForKey, uploadVideoToR2 } from "./r2util.mjs";

const require = createRequire(import.meta.url);

function resolveFfmpeg() {
  if (spawnSync("which", ["ffmpeg"]).status === 0) return "ffmpeg";
  try {
    return require("@ffmpeg-installer/ffmpeg").path;
  } catch {
    return null;
  }
}

function chunkImages(urls, maxPerBatch) {
  if (urls.length <= maxPerBatch) return [urls];
  const n = urls.length;
  const batches = Math.ceil(n / maxPerBatch);
  const base = Math.floor(n / batches);
  let extra = n % batches;
  const out = [];
  let i = 0;
  for (let b = 0; b < batches; b++) {
    const size = base + (extra > 0 ? 1 : 0);
    if (extra > 0) extra--;
    out.push(urls.slice(i, i + size));
    i += size;
  }
  return out;
}

function concatVideos(ffmpeg, clipPaths, outPath) {
  const listPath = outPath.replace(/\.mp4$/, "_concat.txt");
  fs.writeFileSync(listPath, clipPaths.map((p) => `file '${p}'`).join("\n"));
  execSync(
    `"${ffmpeg}" -y -f concat -safe 0 -i "${listPath}" -c copy -loglevel error "${outPath}"`,
    { stdio: "inherit" }
  );
}

function resolveProvider(cfg) {
  const explicit = process.env.SEEDANCE_PROVIDER || cfg.provider;
  if (explicit && explicit !== "auto") return explicit;

  if (isCliAuthenticated(cfg)) return "cli";
  if (isMcpConfigured(cfg)) return "mcp";
  return "api";
}

async function generateBatch(provider, imageUrls, prompt, cfg) {
  if (provider === "cli") {
    return generateSeedanceViaCli({ imageUrls, prompt, cfg });
  }
  if (provider === "mcp") {
    return generateSeedanceViaMcp({ imageUrls, prompt, cfg });
  }
  return generateSeedanceClip({ imageUrls, prompt, cfg });
}

export function isSeedanceConfigured(cfg) {
  if (cfg.enabled === false) return false;
  return isCliAuthenticated(cfg) || isMcpConfigured(cfg) || Boolean(
    process.env.HF_CREDENTIALS ||
      (process.env.HIGGSFIELD_API_KEY && process.env.HIGGSFIELD_API_SECRET)
  );
}

export async function renderSeedance({ object_id, image_keys, cfg }) {
  if (!isSeedanceConfigured(cfg)) {
    console.warn("Seedance skipped: no Higgsfield CLI auth, MCP token, or API credentials");
    return null;
  }

  if (!image_keys?.length) {
    throw new Error("Seedance: no images selected");
  }

  const provider = resolveProvider(cfg);
  console.log(`Seedance provider: ${provider}`);

  const ffmpeg = resolveFfmpeg();
  if (!ffmpeg) {
    throw new Error("Seedance: ffmpeg required for multi-batch concat");
  }

  const imageUrls = image_keys.map(publicUrlForKey);
  const maxBatch = cfg.max_images_per_request || 9;
  const batches = chunkImages(imageUrls, maxBatch);
  const template =
    cfg.prompt_template ||
    "Vertical 9:16 real estate video from references {tags}: a montage of {count} separate static shots, one per reference, in order. Each shot starts as an exact frozen replica of its reference image, then a very slow smooth stabilized dolly-in straight forward only. Plain hard cuts between shots, no morphing, no invented transitions or objects, no people. Photorealistic.";

  const tmpDir = `/tmp/seedance-${object_id}-${Date.now()}`;
  fs.mkdirSync(tmpDir, { recursive: true });

  try {
    const clipPaths = [];

    for (let b = 0; b < batches.length; b++) {
      const batchUrls = batches[b];
      const prompt = buildPrompt(template, batchUrls.length);
      console.log(`Seedance batch ${b + 1}/${batches.length}: ${batchUrls.length} images`);

      const remoteUrl = await generateBatch(provider, batchUrls, prompt, {
        ...cfg,
        batch_duration_seconds: cfg.batch_duration_seconds || Math.min(15, batchUrls.length + 4),
      });

      const clipPath = path.join(tmpDir, `batch_${b}.mp4`);
      await downloadToFile(remoteUrl, clipPath);
      clipPaths.push(clipPath);
    }

    const finalLocal = path.join(tmpDir, "video_seedance_9x16.mp4");
    if (clipPaths.length === 1) {
      fs.copyFileSync(clipPaths[0], finalLocal);
    } else {
      concatVideos(ffmpeg, clipPaths, finalLocal);
    }

    const r2Key = `${object_id}/video_seedance_9x16.mp4`;
    const url = await uploadVideoToR2(finalLocal, r2Key);
    console.log(`✓ seedance 9x16: ${url}`);
    return { "9x16": url, image_count: image_keys.length, batches: batches.length, provider };
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

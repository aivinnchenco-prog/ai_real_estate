/**
 * Higgsfield Seedance via official CLI (seedance_2_0).
 * Requires: higgsfield auth login (OAuth) on the host once.
 */
import { execFileSync, spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { buildPrompt } from "./higgsfieldClient.mjs";

function cliBin(cfg) {
  return cfg.cli_bin || process.env.HIGGSFIELD_CLI_BIN || "higgsfield";
}

export function isCliAuthenticated(cfg) {
  const bin = cliBin(cfg);
  const res = spawnSync(bin, ["auth", "token"], { encoding: "utf8", timeout: 10_000 });
  if (res.status !== 0) return false;
  return Boolean(res.stdout?.trim());
}

async function downloadImages(imageUrls, tmpDir) {
  fs.mkdirSync(tmpDir, { recursive: true });
  const paths = [];
  for (let i = 0; i < imageUrls.length; i++) {
    const out = path.join(tmpDir, `ref_${String(i).padStart(2, "0")}.jpg`);
    const res = await fetch(imageUrls[i]);
    if (!res.ok) throw new Error(`Download image ${i + 1}: HTTP ${res.status}`);
    fs.writeFileSync(out, Buffer.from(await res.arrayBuffer()));
    paths.push(out);
  }
  return paths;
}

function extractVideoUrl(output) {
  const text = output.trim();
  try {
    const data = JSON.parse(text);
    const candidates = [
      data.result_url,
      data.video?.url,
      data.video_url,
      data.jobs?.[0]?.results?.raw?.url,
      data.jobs?.[0]?.result_url,
    ].filter(Boolean);
    if (candidates.length) return candidates[0];
  } catch {
    /* plain text output */
  }
  const urlMatch = text.match(/https:\/\/[^\s"'`]+\.mp4[^\s"'`]*/);
  if (urlMatch) return urlMatch[0];
  const anyHttps = text.match(/https:\/\/[^\s"'`]+/);
  if (anyHttps) return anyHttps[0];
  throw new Error(`CLI output has no video URL: ${text.slice(0, 400)}`);
}

export async function generateSeedanceViaCli({ imageUrls, prompt, cfg }) {
  const bin = cliBin(cfg);
  if (!isCliAuthenticated(cfg)) {
    throw new Error("Higgsfield CLI not authenticated — run: higgsfield auth login");
  }

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "hf-seedance-"));
  try {
    const localPaths = await downloadImages(imageUrls, tmpDir);
    const args = [
      "generate",
      "create",
      cfg.model || "seedance_2_0",
      "--prompt",
      prompt,
      "--aspect_ratio",
      cfg.aspect_ratio || "9:16",
      "--duration",
      String(cfg.batch_duration_seconds || cfg.duration_seconds || 12),
      "--resolution",
      cfg.resolution || "720p",
      "--mode",
      cfg.mode || "std",
      "--wait",
      "--wait-timeout",
      cfg.cli_wait_timeout || "20m",
      "--wait-interval",
      cfg.cli_wait_interval || "5s",
      "--json",
      "--no-color",
    ];

    if (cfg.genre) args.push("--genre", cfg.genre);

    for (const p of localPaths) {
      args.push("--image", p);
    }

    console.log(`Higgsfield CLI: seedance_2_0, ${localPaths.length} images`);
    const out = execFileSync(bin, args, {
      encoding: "utf8",
      maxBuffer: 20 * 1024 * 1024,
      timeout: (cfg.timeout_seconds || 1200) * 1000,
    });

    return extractVideoUrl(out);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

export function buildSeedancePrompt(template, imageCount) {
  return buildPrompt(template, imageCount);
}

/**
 * Higgsfield Seedance via official CLI (seedance_2_0).
 * Requires: higgsfield auth login (OAuth) on the host once.
 *
 * Local --image paths make the CLI auto-PUT to Higgsfield S3 and hit
 * SignatureDoesNotMatch (empty Content-Type). Upload first, then pass UUIDs.
 */
import { execFileSync, spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { buildPrompt } from "./higgsfieldClient.mjs";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const UUID_FIND_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

function cliBin(cfg) {
  return cfg.cli_bin || process.env.HIGGSFIELD_CLI_BIN || "higgsfield";
}

export function isCliAuthenticated(cfg) {
  const bin = cliBin(cfg);
  const res = spawnSync(bin, ["auth", "token"], { encoding: "utf8", timeout: 10_000 });
  if (res.status !== 0) return false;
  return Boolean(res.stdout?.trim());
}

function imageExtFromBytes(buf) {
  if (buf.length >= 2 && buf[0] === 0xff && buf[1] === 0xd8) return ".jpg";
  if (
    buf.length >= 8 &&
    buf[0] === 0x89 &&
    buf[1] === 0x50 &&
    buf[2] === 0x4e &&
    buf[3] === 0x47
  ) {
    return ".png";
  }
  if (
    buf.length >= 12 &&
    buf.slice(0, 4).toString("ascii") === "RIFF" &&
    buf.slice(8, 12).toString("ascii") === "WEBP"
  ) {
    return ".webp";
  }
  return ".jpg";
}

async function downloadImages(imageUrls, tmpDir) {
  fs.mkdirSync(tmpDir, { recursive: true });
  const paths = [];
  for (let i = 0; i < imageUrls.length; i++) {
    const res = await fetch(imageUrls[i]);
    if (!res.ok) throw new Error(`Download image ${i + 1}: HTTP ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    const ext = imageExtFromBytes(buf);
    const out = path.join(tmpDir, `ref_${String(i).padStart(2, "0")}${ext}`);
    fs.writeFileSync(out, buf);
    paths.push(out);
  }
  return paths;
}

function collectUploadIds(node, into) {
  if (node == null) return;
  if (typeof node === "string") {
    if (UUID_RE.test(node)) into.push(node);
    return;
  }
  if (Array.isArray(node)) {
    for (const item of node) collectUploadIds(item, into);
    return;
  }
  if (typeof node !== "object") return;
  for (const key of ["id", "upload_id", "media_id", "image_id"]) {
    if (typeof node[key] === "string") into.push(node[key]);
  }
  for (const nested of [node.item, node.items, node.data, node.upload, node.result]) {
    collectUploadIds(nested, into);
  }
}

/** Parse `higgsfield upload create --json` (or list item) into a media UUID. */
export function extractUploadId(output) {
  const text = String(output || "").trim();
  if (!text) throw new Error("Higgsfield upload: empty CLI output");

  let data = null;
  try {
    data = JSON.parse(text);
  } catch {
    data = null;
  }

  const candidates = [];
  collectUploadIds(data, candidates);
  const id = candidates.find((c) => UUID_RE.test(c));
  if (id) return id;

  const match = text.match(UUID_FIND_RE);
  if (match) return match[0];
  throw new Error(`Higgsfield upload: no id in output: ${text.slice(0, 300)}`);
}

function runCli(bin, args, { timeout, maxBuffer } = {}) {
  const res = spawnSync(bin, args, {
    encoding: "utf8",
    maxBuffer: maxBuffer || 20 * 1024 * 1024,
    timeout: timeout || 120_000,
  });
  const out = `${res.stdout || ""}${res.stderr || ""}`;
  if (res.error) throw res.error;
  if (res.status !== 0) {
    throw new Error(`Command failed: ${bin} ${args.join(" ")}\n${out}`.trim());
  }
  return out;
}

function uploadLocalImage(bin, filePath, timeoutMs) {
  const out = runCli(bin, ["upload", "create", filePath, "--json", "--no-color"], {
    timeout: timeoutMs || 120_000,
    maxBuffer: 2 * 1024 * 1024,
  });
  return extractUploadId(out);
}

function extractVideoUrl(output) {
  const text = output.trim();

  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = null;
  }

  const jobs = Array.isArray(data) ? data : data?.jobs ? data.jobs : data ? [data] : [];
  for (const job of jobs) {
    const status = String(job?.status || "").toLowerCase();
    if (["failed", "nsfw", "error", "cancelled", "canceled"].includes(status)) {
      throw new Error(`Higgsfield job ${status}${job?.id ? ` (${job.id})` : ""}`);
    }
    const url =
      job?.result_url ||
      job?.video?.url ||
      job?.video_url ||
      job?.results?.raw?.url ||
      job?.results?.min?.url;
    if (url) return url;
  }

  const urlMatch = text.match(/https:\/\/[^\s"'`]+\.mp4[^\s"'`]*/i);
  if (urlMatch) return urlMatch[0];

  throw new Error(`CLI output has no video URL: ${text.slice(0, 400)}`);
}

function shortenCliError(err) {
  const msg = String(err?.message || err);
  if (/SignatureDoesNotMatch/i.test(msg)) {
    return new Error(
      "Higgsfield S3 SignatureDoesNotMatch while uploading reference images. " +
        "CLI must pass upload UUIDs, not local file paths."
    );
  }
  return err instanceof Error ? err : new Error(msg);
}

export async function generateSeedanceViaCli({ imageUrls, prompt, cfg }) {
  const bin = cliBin(cfg);
  if (!isCliAuthenticated(cfg)) {
    throw new Error("Higgsfield CLI not authenticated — run: higgsfield auth login");
  }

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "hf-seedance-"));
  try {
    const localPaths = await downloadImages(imageUrls, tmpDir);
    const uploadIds = [];
    for (let i = 0; i < localPaths.length; i++) {
      try {
        uploadIds.push(uploadLocalImage(bin, localPaths[i]));
      } catch (err) {
        throw shortenCliError(
          new Error(`Higgsfield upload image ${i + 1}/${localPaths.length}: ${err.message}`)
        );
      }
    }

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
    if (cfg.generate_audio === false) args.push("--generate_audio", "false");

    for (const id of uploadIds) {
      args.push("--image", id);
    }

    console.log(`Higgsfield CLI: seedance_2_0, ${uploadIds.length} images (pre-uploaded)`);
    let out;
    try {
      out = execFileSync(bin, args, {
        encoding: "utf8",
        maxBuffer: 20 * 1024 * 1024,
        timeout: (cfg.timeout_seconds || 1200) * 1000,
      });
    } catch (err) {
      throw shortenCliError(err);
    }

    return extractVideoUrl(out);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

export function buildSeedancePrompt(template, imageCount) {
  return buildPrompt(template, imageCount);
}

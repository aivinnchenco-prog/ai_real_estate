/**
 * Higgsfield / Seedance API client.
 * MCP provider hook — заполнится когда подключишь MCP (SEEDANCE_PROVIDER=mcp).
 */
import * as fs from "fs";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function apiKey() {
  return process.env.HIGGSFIELD_API_KEY || process.env.HF_API_KEY;
}

function apiSecret() {
  return process.env.HIGGSFIELD_API_SECRET || process.env.HF_API_SECRET || process.env.HF_SECRET;
}

function credentials() {
  if (process.env.HF_CREDENTIALS || process.env.HF_KEY) {
    return process.env.HF_CREDENTIALS || process.env.HF_KEY;
  }
  const id = apiKey();
  const secret = apiSecret();
  if (id && secret) return `${id}:${secret}`;
  return null;
}

function authHeaders() {
  const creds = credentials();
  if (!creds) throw new Error("Higgsfield credentials not set (HIGGSFIELD_API_KEY + HIGGSFIELD_API_SECRET)");
  return {
    Authorization: `Key ${creds}`,
    "Content-Type": "application/json",
  };
}

export function buildPrompt(template, imageCount) {
  const tags = Array.from({ length: imageCount }, (_, i) => `@Image${i + 1}`).join(", ");
  return template.replace("{tags}", tags).replace("{count}", String(imageCount));
}

export async function submitSeedanceJob({ imageUrls, prompt, cfg }) {
  const provider = process.env.SEEDANCE_PROVIDER || cfg.provider || "api";
  if (provider === "mcp") {
    throw new Error("SEEDANCE_PROVIDER=mcp not wired yet — provide MCP schema");
  }

  const base = cfg.api_base || "https://platform.higgsfield.ai";
  const path = cfg.submit_path || "/seedance/v2/reference-to-video";
  const url = `${base.replace(/\/$/, "")}${path}`;

  const body = {
    prompt,
    image_urls: imageUrls,
    aspect_ratio: cfg.aspect_ratio || "9:16",
    duration: cfg.batch_duration_seconds || cfg.duration_seconds || 8,
    quality: cfg.quality || "720p",
    generate_audio: cfg.generate_audio !== false,
  };

  if (cfg.use_input_images) {
    body.input_images = imageUrls.map((u) => ({ url: u }));
    delete body.image_urls;
  }

  const res = await fetch(url, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });

  const text = await res.text();
  if (!res.ok) {
    throw new Error(`Higgsfield submit HTTP ${res.status}: ${text.slice(0, 500)}`);
  }

  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`Higgsfield invalid JSON: ${text.slice(0, 200)}`);
  }

  return {
    requestId: data.request_id || data.id || data.job_id,
    statusUrl: data.status_url || data.urls?.status || null,
    videoUrl: data.video_url || data.output?.video_url || null,
    raw: data,
  };
}

export async function pollSeedanceJob({ requestId, statusUrl, cfg }) {
  const base = cfg.api_base || "https://platform.higgsfield.ai";
  const pollPath = cfg.status_path || "/requests/{id}/status";
  const interval = (cfg.poll_interval_seconds || 10) * 1000;
  const timeout = (cfg.timeout_seconds || 600) * 1000;
  const started = Date.now();

  while (Date.now() - started < timeout) {
    const url =
      statusUrl ||
      `${base.replace(/\/$/, "")}${pollPath.replace("{id}", encodeURIComponent(requestId))}`;

    const res = await fetch(url, { headers: authHeaders() });
    const text = await res.text();
    if (!res.ok) {
      throw new Error(`Higgsfield poll HTTP ${res.status}: ${text.slice(0, 300)}`);
    }

    const data = JSON.parse(text);
    const status = (data.status || data.state || "").toLowerCase();

    if (["completed", "succeeded", "success", "done"].includes(status)) {
      const videoUrl =
        data.video_url ||
        data.output?.video_url ||
        data.result?.video_url ||
        data.urls?.download;
      if (!videoUrl) throw new Error(`Higgsfield completed but no video_url: ${text.slice(0, 300)}`);
      return videoUrl;
    }

    if (["failed", "error", "cancelled", "canceled"].includes(status)) {
      throw new Error(`Higgsfield job failed: ${data.error || data.message || text.slice(0, 300)}`);
    }

    await sleep(interval);
  }

  throw new Error(`Higgsfield timeout after ${cfg.timeout_seconds || 600}s (request ${requestId})`);
}

export async function generateSeedanceClip({ imageUrls, prompt, cfg }) {
  const job = await submitSeedanceJob({ imageUrls, prompt, cfg });
  if (job.videoUrl) return job.videoUrl;
  if (!job.requestId && !job.statusUrl) {
    throw new Error("Higgsfield response missing request_id and video_url");
  }
  return pollSeedanceJob({
    requestId: job.requestId,
    statusUrl: job.statusUrl,
    cfg,
  });
}

export async function downloadToFile(url, outPath) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download ${url}: HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(outPath, buf);
}

export function isSeedanceConfigured(cfg) {
  if (cfg.enabled === false) return false;
  const provider = process.env.SEEDANCE_PROVIDER || cfg.provider || "api";
  if (provider === "mcp") return Boolean(process.env.HIGGSFIELD_MCP_URL);
  return Boolean(credentials());
}

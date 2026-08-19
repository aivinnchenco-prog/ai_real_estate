/**
 * Higgsfield Platform API (cloud.higgsfield.ai) — API Key + Secret.
 * Seedance 2.0 multi-ref доступен только через OAuth (CLI/MCP).
 * Здесь: DoP image-to-video (1 фото → клип), без OAuth.
 */
import { HiggsfieldClient } from "@higgsfield/client";

function credentials() {
  if (process.env.HF_CREDENTIALS) {
    const [apiKey, apiSecret] = process.env.HF_CREDENTIALS.split(":");
    return { apiKey, apiSecret };
  }
  const apiKey = process.env.HIGGSFIELD_API_KEY;
  const apiSecret = process.env.HIGGSFIELD_API_SECRET;
  if (apiKey && apiSecret) return { apiKey, apiSecret };
  return null;
}

export function isPlatformConfigured() {
  return Boolean(credentials());
}

function client() {
  const creds = credentials();
  if (!creds) throw new Error("HIGGSFIELD_API_KEY + HIGGSFIELD_API_SECRET not set");
  return new HiggsfieldClient(creds);
}

function extractVideoUrl(jobSet) {
  const url =
    jobSet?.jobs?.[0]?.results?.raw?.url ||
    jobSet?.jobs?.[0]?.results?.min?.url;
  if (!url) {
    throw new Error(`Platform API: no video URL in response`);
  }
  return url;
}

export function buildImage2VideoParams({ imageUrl, prompt, cfg }) {
  const params = {
    model: cfg.api_model || "dop-turbo",
    prompt,
    input_images: [{ type: "image_url", image_url: imageUrl }],
    enhance_prompt: cfg.enhance_prompt !== false,
  };

  const resolution = cfg.api_resolution || cfg.resolution;
  if (resolution) params.resolution = resolution;
  return params;
}

export async function generateClipViaPlatform({ imageUrl, prompt, cfg }) {
  const params = buildImage2VideoParams({ imageUrl, prompt, cfg });

  console.log(
    `Higgsfield Platform API: ${params.model}, resolution ${params.resolution || "default"}`
  );

  const jobSet = await client().generate("/v1/image2video/dop", params, {
    withPolling: true,
  });

  return extractVideoUrl(jobSet);
}

export async function generateSeedanceViaPlatform({ imageUrls, prompt, cfg }) {
  if (!imageUrls?.length) throw new Error("Platform API: no images");
  return generateClipViaPlatform({ imageUrl: imageUrls[0], prompt, cfg });
}

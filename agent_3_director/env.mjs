import { readFileSync, existsSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(__dirname);

function applyEnvFile(p) {
  if (!existsSync(p)) return;
  for (const line of readFileSync(p, "utf8").split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#") || !t.includes("=")) continue;
    const i = t.indexOf("=");
    const k = t.slice(0, i).trim();
    const v = t.slice(i + 1).trim();
    if (!process.env[k]) process.env[k] = v;
  }
}

export function loadEnv() {
  applyEnvFile(process.env.OPENHOME_ENV_FILE || "/opt/openhome/.env");
  for (const name of [".env", ".env.local"]) {
    applyEnvFile(resolve(ROOT, name));
  }
  process.env.R2_ACCOUNT_ID = process.env.R2_ACCOUNT_ID || process.env.CLOUDFLARE_ACCOUNT_ID;
  process.env.R2_ACCESS_KEY_ID = process.env.R2_ACCESS_KEY_ID || process.env.CLOUDFLARE_ACCESS_KEY_ID;
  process.env.R2_SECRET_ACCESS_KEY =
    process.env.R2_SECRET_ACCESS_KEY || process.env.CLOUDFLARE_SECRET_ACCESS_KEY;
  process.env.R2_BUCKET = process.env.R2_BUCKET || process.env.CLOUDFLARE_BUCKET;
  process.env.R2_PUBLIC_BASE = process.env.R2_PUBLIC_BASE || process.env.CLOUDFLARE_PUBLIC_BASE_URL;
  if (!process.env.HF_CREDENTIALS && process.env.HIGGSFIELD_API_KEY && process.env.HIGGSFIELD_API_SECRET) {
    process.env.HF_CREDENTIALS = `${process.env.HIGGSFIELD_API_KEY}:${process.env.HIGGSFIELD_API_SECRET}`;
  }
}

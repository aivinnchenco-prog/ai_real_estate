import {
  S3Client,
  GetObjectCommand,
  PutObjectCommand,
} from "@aws-sdk/client-s3";
import * as fs from "fs";

function r2Config() {
  const accountId = process.env.R2_ACCOUNT_ID || process.env.CLOUDFLARE_ACCOUNT_ID;
  const accessKeyId = process.env.R2_ACCESS_KEY_ID || process.env.CLOUDFLARE_ACCESS_KEY_ID;
  const secretAccessKey =
    process.env.R2_SECRET_ACCESS_KEY || process.env.CLOUDFLARE_SECRET_ACCESS_KEY;
  const bucket = process.env.R2_BUCKET || process.env.CLOUDFLARE_BUCKET || "real-estate-propertiess";
  const publicBase = process.env.R2_PUBLIC_BASE || process.env.CLOUDFLARE_PUBLIC_BASE_URL;

  if (!accountId || !accessKeyId || !secretAccessKey) {
    throw new Error("R2 credentials not set (CLOUDFLARE_* or R2_* in .env)");
  }

  return {
    bucket,
    publicBase,
    client: new S3Client({
      region: "auto",
      endpoint: `https://${accountId}.r2.cloudflarestorage.com`,
      credentials: { accessKeyId, secretAccessKey },
    }),
  };
}

function isNotFoundError(err) {
  const status = err?.$metadata?.httpStatusCode;
  const name = err?.name || err?.Code || "";
  return (
    status === 404 ||
    name === "NoSuchKey" ||
    name === "NotFound" ||
    name === "NoSuchBucket"
  );
}

function isPreconditionFailed(err) {
  const status = err?.$metadata?.httpStatusCode;
  const name = err?.name || err?.Code || "";
  return status === 412 || name === "PreconditionFailed";
}

export function publicUrlForKey(key) {
  const { publicBase } = r2Config();
  if (!publicBase) throw new Error("R2_PUBLIC_BASE / CLOUDFLARE_PUBLIC_BASE_URL not set");
  return `${publicBase}/${key}`;
}

export async function downloadFromR2(key, outPath) {
  const { client, bucket } = r2Config();
  const res = await client.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
  const buffer = await res.Body.transformToByteArray();
  fs.writeFileSync(outPath, buffer);
}

/**
 * Read object body + ETag. Returns null when the key does not exist.
 */
export async function getR2TextObject(key) {
  const { client, bucket } = r2Config();
  try {
    const res = await client.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
    const body = await res.Body.transformToString();
    return { body, etag: res.ETag || null };
  } catch (err) {
    if (isNotFoundError(err)) return null;
    throw err;
  }
}

/**
 * Conditional JSON/text put.
 * - etag set  → IfMatch (update only if unchanged)
 * - etag null → IfNoneMatch "*" (create only if missing)
 * Returns true on success, false on precondition conflict (412).
 */
export async function putR2TextObject(key, body, { contentType = "application/json", etag = null } = {}) {
  const { client, bucket } = r2Config();
  const input = {
    Bucket: bucket,
    Key: key,
    Body: body,
    ContentType: contentType,
  };
  if (etag) input.IfMatch = etag;
  else input.IfNoneMatch = "*";

  try {
    await client.send(new PutObjectCommand(input));
    return true;
  } catch (err) {
    if (isPreconditionFailed(err)) return false;
    throw err;
  }
}

export async function uploadVideoToR2(filepath, key) {
  return uploadFileToR2(filepath, key, "video/mp4");
}

export async function uploadFileToR2(filepath, key, contentType) {
  const { client, bucket, publicBase } = r2Config();
  const data = fs.readFileSync(filepath);
  await client.send(
    new PutObjectCommand({
      Bucket: bucket,
      Key: key,
      Body: data,
      ContentType: contentType,
    })
  );
  return `${publicBase}/${key}`;
}

export { isNotFoundError, isPreconditionFailed };

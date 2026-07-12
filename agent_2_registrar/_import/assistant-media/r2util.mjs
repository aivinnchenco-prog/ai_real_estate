import {
  S3Client,
  GetObjectCommand,
  PutObjectCommand,
} from "@aws-sdk/client-s3";
import * as fs from "fs";

const {
  R2_ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID,
  R2_ACCESS_KEY_ID = process.env.CLOUDFLARE_ACCESS_KEY_ID,
  R2_SECRET_ACCESS_KEY = process.env.CLOUDFLARE_SECRET_ACCESS_KEY,
  R2_BUCKET = process.env.CLOUDFLARE_BUCKET || "real-estate-propertiess",
  R2_PUBLIC_BASE = process.env.CLOUDFLARE_PUBLIC_BASE_URL,
} = process.env;

export const r2PublicBase = R2_PUBLIC_BASE;

export const s3 = new S3Client({
  region: "auto",
  endpoint: `https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com`,
  credentials: {
    accessKeyId: R2_ACCESS_KEY_ID,
    secretAccessKey: R2_SECRET_ACCESS_KEY,
  },
});

export async function downloadFromR2(key, outPath) {
  const res = await s3.send(new GetObjectCommand({ Bucket: R2_BUCKET, Key: key }));
  const buffer = await res.Body.transformToByteArray();
  fs.writeFileSync(outPath, buffer);
}

export async function uploadVideoToR2(filepath, key) {
  const data = fs.readFileSync(filepath);
  await s3.send(
    new PutObjectCommand({
      Bucket: R2_BUCKET,
      Key: key,
      Body: data,
      ContentType: "video/mp4",
    })
  );
  return `${R2_PUBLIC_BASE}/${key}`;
}

export function publicUrlForKey(key) {
  return `${R2_PUBLIC_BASE}/${key}`;
}

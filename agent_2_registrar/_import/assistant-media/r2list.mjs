// r2list.mjs — листинг и presigned-URL для R2. Отдельный файл специально,
// чтобы не задевать уже проверенный renderReel.mjs.
import {
 S3Client,
 ListObjectsV2Command,
 GetObjectCommand,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

// Ленивая инициализация: env читаем в момент вызова (loadEnv() в agent3_video
// выполняется после импорта модулей), с фолбэком на CLOUDFLARE_*-имена.
let _s3 = null;
let _bucket = "real-estate-propertiess";

function s3Client() {
 if (_s3) return _s3;
 const accountId = process.env.R2_ACCOUNT_ID || process.env.CLOUDFLARE_ACCOUNT_ID;
 const accessKeyId = process.env.R2_ACCESS_KEY_ID || process.env.CLOUDFLARE_ACCESS_KEY_ID;
 const secretAccessKey = process.env.R2_SECRET_ACCESS_KEY || process.env.CLOUDFLARE_SECRET_ACCESS_KEY;
 _bucket = process.env.R2_BUCKET || process.env.CLOUDFLARE_BUCKET || _bucket;
 _s3 = new S3Client({
  region: "auto",
  endpoint: `https://${accountId}.r2.cloudflarestorage.com`,
  credentials: { accessKeyId, secretAccessKey },
 });
 return _s3;
}

export async function listKeys(prefix) {
 const out = [];
 let token;
 do {
  const res = await s3Client().send(
   new ListObjectsV2Command({
    Bucket: _bucket,
    Prefix: prefix,
    ContinuationToken: token,
   })
  );
  for (const o of res.Contents ?? []) out.push(o.Key);
  token = res.IsTruncated ? res.NextContinuationToken : undefined;
 } while (token);
 return out;
}

export async function presignedGet(key, expiresIn = 1800) {
 return getSignedUrl(
  s3Client(),
  new GetObjectCommand({ Bucket: _bucket, Key: key }),
  { expiresIn }
 );
}

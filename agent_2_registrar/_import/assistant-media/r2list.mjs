// r2list.mjs — листинг и presigned-URL для R2. Отдельный файл специально,
// чтобы не задевать уже проверенный renderReel.mjs.
import {
 S3Client,
 ListObjectsV2Command,
 GetObjectCommand,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

const {
 R2_ACCOUNT_ID,
 R2_ACCESS_KEY_ID,
 R2_SECRET_ACCESS_KEY,
 R2_BUCKET = "real-estate-propertiess",
} = process.env;

const s3 = new S3Client({
 region: "auto",
 endpoint: `https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com`,
 credentials: {
  accessKeyId: R2_ACCESS_KEY_ID,
  secretAccessKey: R2_SECRET_ACCESS_KEY,
 },
});

export async function listKeys(prefix) {
 const out = [];
 let token;
 do {
  const res = await s3.send(
   new ListObjectsV2Command({
    Bucket: R2_BUCKET,
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
  s3,
  new GetObjectCommand({ Bucket: R2_BUCKET, Key: key }),
  { expiresIn }
 );
}

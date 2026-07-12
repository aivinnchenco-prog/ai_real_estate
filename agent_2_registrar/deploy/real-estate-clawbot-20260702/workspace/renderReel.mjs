import {
  S3Client,
  GetObjectCommand,
  PutObjectCommand,
} from "@aws-sdk/client-s3";
import { execSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { listKeys } from "./r2list.mjs";

const {
  R2_ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID,
  R2_ACCESS_KEY_ID = process.env.CLOUDFLARE_ACCESS_KEY_ID,
  R2_SECRET_ACCESS_KEY = process.env.CLOUDFLARE_SECRET_ACCESS_KEY,
  R2_BUCKET = process.env.CLOUDFLARE_BUCKET || "real-estate-propertiess",
  R2_PUBLIC_BASE = process.env.CLOUDFLARE_PUBLIC_BASE_URL,
} = process.env;

const s3 = new S3Client({
  region: "auto",
  endpoint: `https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com`,
  credentials: {
    accessKeyId: R2_ACCESS_KEY_ID,
    secretAccessKey: R2_SECRET_ACCESS_KEY,
  },
});

const FORMATS = [
  { name: "square", width: 1080, height: 1080, suffix: "1x1" },
  { name: "portrait", width: 1080, height: 1440, suffix: "3x4" },
  { name: "reels", width: 1080, height: 1920, suffix: "9x16" },
];

async function downloadFile(key, outPath) {
  const res = await s3.send(new GetObjectCommand({ Bucket: R2_BUCKET, Key: key }));
  const buffer = await res.Body.transformToByteArray();
  fs.writeFileSync(outPath, buffer);
}

async function uploadFile(filepath, key) {
  const data = fs.readFileSync(filepath);
  await s3.send(
    new PutObjectCommand({
      Bucket: R2_BUCKET,
      Key: key,
      Body: data,
      ContentType: "video/mp4",
    })
  );
}

function buildZoomFilter(zoomIn, frames, w, h) {
  if (zoomIn) {
    return `zoompan=z='min(zoom+0.002,1.15)':x='(iw-ow)/2':y='(ih-oh)/2':d=${frames}:s=${w}x${h},format=yuv420p`;
  }
  return `zoompan=z='if(eq(on,1),1.15,max(1,zoom-0.002))':x='(iw-ow)/2':y='(ih-oh)/2':d=${frames}:s=${w}x${h},format=yuv420p`;
}

export async function renderReel({
  object_id,
  image_keys,
  music_key,
  frame_duration = 2.5,
  min_frames = 6,
}) {
  if (!image_keys?.length) {
    throw new Error("No images to render");
  }

  let keys = [...image_keys];
  while (keys.length < min_frames) {
    keys.push(keys[keys.length % image_keys.length]);
  }

  const tmpDir = `/tmp/reel-${object_id}-${Date.now()}`;
  fs.mkdirSync(tmpDir, { recursive: true });
  const fps = 30;
  const frameDurationFrames = Math.ceil(frame_duration * fps);

  try {
    const imgPaths = [];
    for (let i = 0; i < keys.length; i++) {
      const imgPath = path.join(tmpDir, `img_${String(i).padStart(3, "0")}.jpg`);
      await downloadFile(keys[i], imgPath);
      imgPaths.push(imgPath);
    }

    const musicPath = path.join(tmpDir, "music.mp3");
    await downloadFile(music_key, musicPath);

    const results = {};

    for (const fmt of FORMATS) {
      const clipPaths = [];
      for (let i = 0; i < imgPaths.length; i++) {
        const zoomIn = Math.random() > 0.5;
        const clipPath = path.join(tmpDir, `clip_${fmt.suffix}_${i}.mp4`);
        const vf = buildZoomFilter(zoomIn, frameDurationFrames, fmt.width, fmt.height);
        const cmd = `ffmpeg -y -loop 1 -i "${imgPaths[i]}" -vf "${vf}" -t ${frame_duration} -r ${fps} -c:v libx264 -pix_fmt yuv420p -loglevel error "${clipPath}"`;
        execSync(cmd, { stdio: "inherit" });
        clipPaths.push(clipPath);
      }

      const concatList = path.join(tmpDir, `concat_${fmt.suffix}.txt`);
      fs.writeFileSync(concatList, clipPaths.map((p) => `file '${p}'`).join("\n"));

      const silentVideo = path.join(tmpDir, `silent_${fmt.suffix}.mp4`);
      execSync(
        `ffmpeg -y -f concat -safe 0 -i "${concatList}" -c copy -loglevel error "${silentVideo}"`,
        { stdio: "inherit" }
      );

      const outputVideo = path.join(tmpDir, `reel_${fmt.suffix}.mp4`);
      execSync(
        `ffmpeg -y -i "${silentVideo}" -i "${musicPath}" -c:v copy -c:a aac -shortest -loglevel error "${outputVideo}"`,
        { stdio: "inherit" }
      );

      const videoKey = `${object_id}/video_${fmt.suffix}.mp4`;
      await uploadFile(outputVideo, videoKey);
      results[fmt.suffix] = `${R2_PUBLIC_BASE}/${videoKey}`;
      console.log(`✓ ${fmt.suffix}: ${results[fmt.suffix]}`);
    }

    fs.rmSync(tmpDir, { recursive: true, force: true });
    return results;
  } catch (err) {
    fs.rmSync(tmpDir, { recursive: true, force: true });
    throw err;
  }
}

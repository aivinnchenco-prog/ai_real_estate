#!/usr/bin/env node
/**
 * Rename local MP3s to track_XXX.mp3 and upload to R2 music/ prefix.
 * Usage: node scripts/upload_music_library.mjs "/path/to/audio/folder"
 */
import * as fs from "fs";
import * as path from "path";
import { loadEnv, ROOT } from "../env.mjs";
import { listKeys } from "../r2list.mjs";
import { uploadFileToR2 } from "../r2util.mjs";

loadEnv();

function sortTracks(files) {
  const rank = (name) => {
    const base = path.basename(name);
    const lower = base.toLowerCase();

    const audio = lower.match(/^аудио\s*(\d+)/);
    if (audio) {
      const n = Number(audio[1]);
      const variant = lower.includes("(1)") ? 1 : 0;
      return [0, n, variant, base];
    }
    if (lower === "0816.mp3") return [1, 0, 0, base];
    const aug = lower.match(/^0816\((\d+)\)\.mp3$/);
    if (aug) return [1, Number(aug[1]), 0, base];
    return [2, 0, 0, base];
  };

  return [...files].sort((a, b) => {
    const ra = rank(a);
    const rb = rank(b);
    for (let i = 0; i < ra.length; i++) {
      if (ra[i] < rb[i]) return -1;
      if (ra[i] > rb[i]) return 1;
    }
    return 0;
  });
}

async function main() {
  const srcDir = process.argv[2];
  if (!srcDir) {
    console.error("Usage: node scripts/upload_music_library.mjs <audio-folder>");
    process.exit(1);
  }
  const absDir = path.resolve(srcDir);
  if (!fs.existsSync(absDir)) {
    console.error(`Folder not found: ${absDir}`);
    process.exit(1);
  }

  const mp3s = fs
    .readdirSync(absDir)
    .filter((f) => /\.mp3$/i.test(f) && !f.startsWith("track_"))
    .map((f) => path.join(absDir, f));

  if (!mp3s.length) {
    console.error("No MP3 files to process (track_*.mp3 already renamed?)");
    process.exit(1);
  }

  const ordered = sortTracks(mp3s);
  const width = String(ordered.length).length;
  const pad = (n) => String(n).padStart(Math.max(3, width), "0");

  console.log(`Found ${ordered.length} tracks. Order:`);
  const mapping = ordered.map((src, i) => {
    const num = i + 1;
    const newName = `track_${pad(num)}.mp3`;
    const renamedPath = path.join(absDir, newName);
    return { src, renamedPath, newName, key: `music/${newName}` };
  });
  mapping.forEach((m, i) => console.log(`  ${pad(i + 1)}: ${path.basename(m.src)} -> ${m.newName}`));

  const staging = path.join(absDir, ".upload_staging");
  fs.mkdirSync(staging, { recursive: true });

  for (const m of mapping) {
    fs.copyFileSync(m.src, path.join(staging, m.newName));
  }

  console.log("\nUploading to R2…");
  for (const m of mapping) {
    const staged = path.join(staging, m.newName);
    const url = await uploadFileToR2(staged, m.key, "audio/mpeg");
    console.log(`  uploaded ${m.key}`);
  }

  console.log("\nRenaming local files…");
  for (const m of mapping) {
    if (path.resolve(m.src) === path.resolve(m.renamedPath)) continue;
    if (fs.existsSync(m.renamedPath)) fs.unlinkSync(m.renamedPath);
    fs.renameSync(m.src, m.renamedPath);
  }
  fs.rmSync(staging, { recursive: true, force: true });

  const keys = (await listKeys("music/"))
    .filter((k) => /\.(mp3|m4a|aac|wav|ogg)$/i.test(k))
    .sort();
  console.log(`\nR2 music/ now has ${keys.length} audio file(s).`);
  keys.forEach((k) => console.log(`  ${k}`));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

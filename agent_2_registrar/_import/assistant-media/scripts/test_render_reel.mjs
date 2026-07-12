#!/usr/bin/env node
/**
 * Local render tests — filter strings + optional ffmpeg clip render.
 * Usage: node scripts/test_render_reel.mjs
 */
import { execSync, spawnSync } from "child_process";
import { createRequire } from "module";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import { buildClipFilter } from "../reelFilters.mjs";

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

function resolveFfmpeg() {
  if (spawnSync("which", ["ffmpeg"]).status === 0) return "ffmpeg";
  try {
    return require("@ffmpeg-installer/ffmpeg").path;
  } catch {
    return null;
  }
}

function resolveFfprobe(ffmpegPath) {
  if (spawnSync("which", ["ffprobe"]).status === 0) return "ffprobe";
  const dir = path.dirname(ffmpegPath);
  const probe = path.join(dir, "ffprobe");
  return fs.existsSync(probe) ? probe : null;
}

const tests = [];

function assert(name, cond, detail = "") {
  tests.push({ name, ok: cond, detail });
  console.log(cond ? `✓ ${name}` : `✗ ${name} ${detail}`);
}

// --- Unit: filter chain ---
const vfSquareIn = buildClipFilter(true, 75, 1080, 1080, 30);
assert("cover crop before zoompan", vfSquareIn.includes("force_original_aspect_ratio=increase"));
assert("center x formula", vfSquareIn.includes("trunc(iw/2-(iw/zoom/2))"));
assert("center y formula", vfSquareIn.includes("trunc(ih/2-(ih/zoom/2))"));
assert("no old pan formula", !vfSquareIn.includes("(iw-ow)/2"));
assert("zoom in linear", vfSquareIn.includes("1+0.15*on/"));
assert("zoom out linear", buildClipFilter(false, 75, 1080, 1920, 30).includes("1.15-0.15*on/"));

const vf916 = buildClipFilter(true, 75, 1080, 1920, 30);
assert("9x16 output size", vf916.includes(":s=1080x1920"));

// --- Find sample image ---
const fixtureDir = path.join(ROOT, "data/test_renders");
const candidates = [
  path.join(fixtureDir, "fixture_landscape.png"),
  path.join(fixtureDir, "fixture_portrait.png"),
  path.join(ROOT, "data/sessions/test_legendary_20260702/photos/photo_001.png"),
];

let sampleImage = candidates.find((p) => fs.existsSync(p));

if (!sampleImage) {
  const assets = "/Users/lifefmg/.cursor/projects/Users-lifefmg-Desktop-Real-Estate-Agent-agent-4/assets";
  if (fs.existsSync(assets)) {
    const found = fs.readdirSync(assets).find((f) => f.endsWith(".png"));
    if (found) sampleImage = path.join(assets, found);
  }
}

const ffmpeg = resolveFfmpeg();
const ffprobe = ffmpeg ? resolveFfprobe(ffmpeg) : null;

if (!ffmpeg) {
  console.log("\n⚠ ffmpeg not installed — skipping render tests");
} else if (!sampleImage) {
  console.log("\n⚠ No sample image — skipping render tests");
} else {
  console.log(`\nRender test image: ${sampleImage}`);
  const outDir = path.join(ROOT, "data/test_renders");
  fs.mkdirSync(outDir, { recursive: true });

  const formats = [{ label: "9x16", w: 1080, h: 1920 }];

  for (const fmt of formats) {
    for (const [label, zoomIn] of [
      ["zoom_in", true],
      ["zoom_out", false],
    ]) {
      const out = path.join(outDir, `test_${fmt.label}_${label}.mp4`);
      const vf = buildClipFilter(zoomIn, 75, fmt.w, fmt.h, 30);
      try {
        execSync(
          `"${ffmpeg}" -y -loop 1 -i "${sampleImage}" -vf "${vf}" -t 2.5 -r 30 -c:v libx264 -pix_fmt yuv420p -loglevel error "${out}"`,
          { stdio: "pipe" }
        );
        const stat = fs.statSync(out);
        assert(`render ${fmt.label} ${label}`, stat.size > 500, `size=${stat.size}`);
        console.log(`  → ${out}`);
      } catch (e) {
        assert(`render ${fmt.label} ${label}`, false, e.message?.slice(0, 120));
      }
    }
  }

  // Probe aspect: first frame should match target (no stretch artifacts via dimensions)
  const probeOut = path.join(outDir, "probe_9x16.png");
  const vfProbe = buildClipFilter(true, 1, 1080, 1920, 30);
  execSync(
    `"${ffmpeg}" -y -loop 1 -i "${sampleImage}" -vf "${vfProbe}" -frames:v 1 -loglevel error "${probeOut}"`,
    { stdio: "pipe" }
  );
  if (ffprobe) {
    const probe = spawnSync(
      ffprobe,
      ["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", probeOut],
      { encoding: "utf8" }
    );
    const [pw, ph] = (probe.stdout || "").trim().split(",").map(Number);
    assert("probe frame 1080x1920", pw === 1080 && ph === 1920, `got ${pw}x${ph}`);
  } else {
    assert("probe frame written", fs.existsSync(probeOut) && fs.statSync(probeOut).size > 100);
  }
}

const failed = tests.filter((t) => !t.ok);
console.log(`\n${tests.length - failed.length}/${tests.length} passed`);
process.exit(failed.length ? 1 : 0);

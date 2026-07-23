#!/usr/bin/env node
import { buildPrompt } from "../higgsfieldClient.mjs";
import { isSeedanceConfigured } from "../renderSeedance.mjs";
import {
  fallbackSelectDiverse,
  inferPhotoCategory,
  isExteriorCategory,
  selectWithExteriorRatio,
  topUpSelection,
} from "../selectPhotosSeedance.mjs";
import { normalizeGeminiSelection, isGeminiSelectorAvailable } from "../selectPhotosGemini.mjs";

let ok = 0;
let fail = 0;

function assert(name, cond) {
  if (cond) {
    console.log(`✓ ${name}`);
    ok++;
  } else {
    console.log(`✗ ${name}`);
    fail++;
  }
}

const prompt = buildPrompt("Tour with {tags} total {count}", 3);
assert("prompt tags", prompt.includes("@Image1") && prompt.includes("@Image3"));
assert("prompt count", prompt.includes("3"));

const cfgOff = { enabled: false };
delete process.env.HIGGSFIELD_API_KEY;
delete process.env.HIGGSFIELD_API_SECRET;
delete process.env.HF_CREDENTIALS;
assert("not configured when disabled", !isSeedanceConfigured(cfgOff));

process.env.HIGGSFIELD_API_KEY = "test-key-id";
process.env.HIGGSFIELD_API_SECRET = "test-secret";
assert("configured with api key+secret", isSeedanceConfigured({ enabled: true }));

const sample = Array.from({ length: 20 }, (_, i) => ({
  key: `photo_${String(i + 1).padStart(3, "0")}.jpg`,
  url: `https://example.com/${i}.jpg`,
}));
const picked = fallbackSelectDiverse(sample, 9, 1);
assert("fallback picks 9", picked.length === 9);
assert("exterior first is photo_001", picked[0] === "photo_001.jpg");
assert("no duplicate picks", new Set(picked).size === picked.length);
assert("infer exterior", inferPhotoCategory("building_exterior.jpg") === "exterior");
assert("infer kitchen", inferPhotoCategory("modern_kitchen.jpg") === "kitchen");
assert("pool is exterior for carousel", isExteriorCategory("pool"));

const carouselCandidates = [
  { key: "front.jpg", category: "exterior" },
  { key: "view.jpg", category: "view" },
  { key: "pool.jpg", category: "pool" },
  ...Array.from({ length: 8 }, (_, i) => ({ key: `room_${i}.jpg`, category: "living" })),
];
const carouselPicked = selectWithExteriorRatio(carouselCandidates, 9, 0.3);
assert("carousel 9 selects 3 exterior", carouselPicked.filter((i) => isExteriorCategory(i.category)).length === 3);
assert("carousel 9 selects 6 interior", carouselPicked.filter((i) => !isExteriorCategory(i.category)).length === 6);

// --- topUpSelection: добор до topK после куратора ---
const allKeys = Array.from({ length: 12 }, (_, i) => `photo_${String(i + 1).padStart(3, "0")}.jpg`);
const curatorPicked = ["photo_001.jpg", "photo_004.jpg", "photo_007.jpg"];
const curatorRejected = ["photo_002.jpg", "photo_011.jpg"];
const topped = topUpSelection(curatorPicked, allKeys, curatorRejected, 6);
assert("topup reaches topK", topped.length === 6);
assert("topup keeps curator picks first", topped.slice(0, 3).join() === curatorPicked.join());
assert("topup skips rejected", !topped.includes("photo_002.jpg") && !topped.includes("photo_011.jpg"));
assert("topup no duplicates", new Set(topped).size === topped.length);
assert("topup no overflow when enough", topUpSelection(curatorPicked, allKeys, [], 3).length === 3);
assert(
  "topup caps at available pool",
  topUpSelection(["photo_001.jpg"], ["photo_001.jpg", "photo_002.jpg"], [], 9).length === 2
);

// --- normalizeGeminiSelection: валидация ответа Gemini ---
const pool = ["photo_001.jpg", "photo_002.jpg", "photo_003.jpg", "photo_004.jpg"];
const gemini = normalizeGeminiSelection(
  {
    selected: [
      { key: "photo_002.jpg", category: "kitchen" },
      { key: "photo_001.jpg", category: "exterior" },
      { key: "photo_002.jpg", category: "kitchen" }, // дубль
      { key: "hacker.jpg", category: "living" }, // не из пула
      { key: "photo_003.jpg", category: "weird-cat" }, // неизвестная категория
    ],
  },
  pool,
  9
);
assert("gemini drops dup + foreign keys", gemini.length === 3);
assert("gemini exterior moved first", gemini[0].key === "photo_001.jpg");
assert("gemini unknown category → other", gemini.find((i) => i.key === "photo_003.jpg").category === "other");
assert(
  "gemini respects topK",
  normalizeGeminiSelection({ selected: pool.map((key) => ({ key, category: "living" })) }, pool, 2).length === 2
);
let threw = false;
try {
  normalizeGeminiSelection({ nope: true }, pool, 9);
} catch {
  threw = true;
}
assert("gemini bad shape throws", threw);

const savedGemini = process.env.GEMINI_API_KEY;
process.env.GEMINI_API_KEY = "test";
assert("gemini available with key", isGeminiSelectorAvailable({}));
assert("gemini disabled via config", !isGeminiSelectorAvailable({ gemini_selector: { enabled: false } }));
delete process.env.GEMINI_API_KEY;
assert("gemini unavailable without key", !isGeminiSelectorAvailable({}));
if (savedGemini) process.env.GEMINI_API_KEY = savedGemini;

console.log(`\n${ok}/${ok + fail} passed`);
process.exit(fail ? 1 : 0);

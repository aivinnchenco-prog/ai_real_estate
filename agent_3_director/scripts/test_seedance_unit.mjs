#!/usr/bin/env node
import { buildPrompt } from "../higgsfieldClient.mjs";
import { isSeedanceConfigured } from "../renderSeedance.mjs";
import { fallbackSelectDiverse, inferPhotoCategory } from "../selectPhotosSeedance.mjs";

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

console.log(`\n${ok}/${ok + fail} passed`);
process.exit(fail ? 1 : 0);

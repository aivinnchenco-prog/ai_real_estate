#!/usr/bin/env node
/** Production image-to-video (Higgsfield) must request 720p — config + API payload. */
import { readFileSync } from "fs";
import { resolve } from "path";
import { ROOT } from "../env.mjs";
import { buildImage2VideoParams } from "../higgsfieldPlatform.mjs";

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

const cfg = JSON.parse(readFileSync(resolve(ROOT, "config/seedance.json"), "utf8"));
assert("seedance.json resolution", cfg.resolution === "720p");
assert("seedance.json api_resolution", cfg.api_resolution === "720p");

const params = buildImage2VideoParams({
  imageUrl: "https://example.com/photo.jpg",
  prompt: "test",
  cfg,
});
assert("platform image2video resolution", params.resolution === "720p");
assert("platform preserves model", params.model === (cfg.api_model || "dop-turbo"));

console.log(`\n${ok}/${ok + fail} passed`);
process.exit(fail ? 1 : 0);

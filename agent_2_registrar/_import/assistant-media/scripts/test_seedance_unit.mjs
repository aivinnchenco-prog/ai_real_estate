#!/usr/bin/env node
import { buildPrompt } from "../higgsfieldClient.mjs";
import { isSeedanceConfigured } from "../renderSeedance.mjs";

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

console.log(`\n${ok}/${ok + fail} passed`);
process.exit(fail ? 1 : 0);

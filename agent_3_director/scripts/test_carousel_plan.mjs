#!/usr/bin/env node
import {
  assertUniqueCarouselSources,
  formatPhoneLabel,
  PHONE_LABEL,
  planCarouselSlides,
} from "../carouselSlides.mjs";

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

const meta = { bedrooms: "3", bathrooms: "2", housing_type: "квартира" };

function keys(n) {
  return Array.from({ length: n }, (_, i) => `obj/photos/photo_${String(i + 1).padStart(3, "0")}.jpg`);
}

function locals(n) {
  return Array.from({ length: n }, (_, i) => `/tmp/photo_${i + 1}.jpg`);
}

// 1. withHook=true: slide_01 = [0], slide_02 = [1] + badges, no duplicate sources
{
  const photoKeys = keys(9);
  const photoLocals = locals(9);
  const { plan, names } = planCarouselSlides({
    withHook: true,
    photoKeys,
    photoLocals,
    carouselMeta: meta,
  });
  const hook = plan.find((p) => p.kind === "hook");
  const photos = plan.filter((p) => p.kind === "photo");
  assert("withHook: 9 slides total", names.length === 9);
  assert("withHook: slide_01 uses photoLocals[0]", hook?.sourceKey === photoKeys[0]);
  assert("withHook: slide_02 uses photoLocals[1]", photos[0]?.sourceKey === photoKeys[1]);
  assert("withHook: badges on first photo slide", photos[0]?.badges.includes("Bedrooms"));
  assert("withHook: no badge on slide_03", photos[1]?.badges === "");
  const sources = plan.map((p) => p.sourceKey);
  assert("withHook: unique sources", new Set(sources).size === sources.length);
}

// 2. withHook=false: slide_01 = [0] + badges
{
  const photoKeys = keys(5);
  const photoLocals = locals(5);
  const { plan, names } = planCarouselSlides({
    withHook: false,
    photoKeys,
    photoLocals,
    carouselMeta: meta,
  });
  const photos = plan.filter((p) => p.kind === "photo");
  assert("noHook: 5 slides", names.length === 5);
  assert("noHook: slide_01 uses photoLocals[0]", photos[0]?.sourceKey === photoKeys[0]);
  assert("noHook: badges on slide_01", photos[0]?.badges.includes("Bedrooms"));
  const sources = plan.map((p) => p.sourceKey);
  assert("noHook: unique sources", new Set(sources).size === sources.length);
}

// 3. 8 unique photos + hook → 8 slides, no artificial 9th duplicate
{
  const photoKeys = keys(8);
  const photoLocals = locals(8);
  const { plan, names } = planCarouselSlides({
    withHook: true,
    photoKeys,
    photoLocals,
    carouselMeta: meta,
  });
  assert("8 photos + hook → 8 slides", names.length === 8);
  assert("8 photos + hook → not 9 slides", names.length !== 9);
  const sources = plan.map((p) => p.sourceKey);
  assert("8 photos + hook: no duplicate source", new Set(sources).size === sources.length);
  assert(
    "8 photos + hook: hero not repeated in photo slides",
    !plan.filter((p) => p.kind === "photo").some((p) => p.sourceKey === photoKeys[0])
  );
}

// 4. duplicate source assignment throws before upload
{
  let threw = false;
  try {
    assertUniqueCarouselSources([
      { sourceKey: "obj/photos/a.jpg", slideNo: 1 },
      { sourceKey: "obj/photos/a.jpg", slideNo: 2 },
    ]);
  } catch (err) {
    threw = err.message.includes("duplicate source photo");
  }
  assert("duplicate source throws clear error", threw);
}

assert(
  "carousel footer phone is 4002, not 4001",
  PHONE_LABEL === "+66 62 512 4002" && !PHONE_LABEL.includes("4001")
);
assert(
  "project whatsapp formats to carousel footer",
  formatPhoneLabel("+66625124002") === "+66 62 512 4002"
);
assert("old public number 4001 is not used", formatPhoneLabel("+66625124001") !== PHONE_LABEL);

console.log(`\n${ok}/${ok + fail} passed`);
process.exit(fail ? 1 : 0);

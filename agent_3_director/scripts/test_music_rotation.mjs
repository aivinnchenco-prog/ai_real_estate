#!/usr/bin/env node
/**
 * Unit tests for R2-backed music round-robin (no network).
 */
import {
  chooseMusicTrack,
  defaultRotationKey,
  emptyMusicState,
  filterMusicTracks,
  normalizeMusicState,
  resolveRotationKey,
} from "../musicRotation.mjs";

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

assert("default rotation key", defaultRotationKey("music/") === "music/_rotation.json");
assert("default rotation key no slash", defaultRotationKey("music") === "music/_rotation.json");
assert(
  "resolve from cfg",
  resolveRotationKey({ r2_prefix: "music/", rotation_key: "meta/rot.json" }) === "meta/rot.json"
);

const keys = [
  "music/track_001.mp3",
  "music/track_002.mp3",
  "music/_rotation.json",
  "music/readme.txt",
  "music/track_003.MP3",
];
const filtered = filterMusicTracks(keys, "music/_rotation.json");
assert("filter drops rotation json", !filtered.includes("music/_rotation.json"));
assert("filter drops txt", !filtered.includes("music/readme.txt"));
assert("filter keeps mp3", filtered.includes("music/track_001.mp3"));
assert("filter keeps MP3", filtered.includes("music/track_003.MP3"));
assert("filter count", filtered.length === 3);

const tracks = ["music/track_002.mp3", "music/track_001.mp3", "music/track_003.mp3"];

const a = chooseMusicTrack("A", tracks, emptyMusicState());
assert("first pick is sorted[0]", a.track === "music/track_001.mp3");
assert("first index 0", a.state.index === 0);
assert("first changed", a.changed === true);

const b = chooseMusicTrack("B", tracks, a.state);
assert("second pick next", b.track === "music/track_002.mp3");
assert("second index 1", b.state.index === 1);

const sticky = chooseMusicTrack("A", tracks, b.state);
assert("sticky same track", sticky.track === "music/track_001.mp3");
assert("sticky no change", sticky.changed === false);
assert("sticky index untouched", sticky.state.index === b.state.index);

const c = chooseMusicTrack("C", tracks, b.state);
const d = chooseMusicTrack("D", tracks, c.state);
assert("wrap to first", d.track === "music/track_001.mp3");
assert("wrap index 0", d.state.index === 0);

const norm = normalizeMusicState({ index: "2", assigned: { X: "music/track_003.mp3" } });
assert("normalize index number", norm.index === 2);
assert("normalize assigned", norm.assigned.X === "music/track_003.mp3");
assert("normalize junk", normalizeMusicState(null).index === -1);

// remount after track removed from pool → assign next
const orphanState = {
  index: 0,
  assigned: { Z: "music/track_gone.mp3" },
};
const orphan = chooseMusicTrack("Z", tracks, orphanState);
assert("orphan reassigned", orphan.changed === true);
assert("orphan gets next after index", orphan.track === "music/track_002.mp3");

console.log(`\n${ok} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

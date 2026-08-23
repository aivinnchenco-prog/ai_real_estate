/**
 * Round-robin music queue persisted in R2 (survives app deploys).
 *
 * State key default: music/_rotation.json  (same prefix as tracks, excluded from pool)
 * Shape: { index: number, assigned: { [objectId]: "music/track_NNN.mp3" } }
 *
 * Local data/music_rotation.json is only a one-shot seed if R2 state is missing.
 */
import * as fs from "fs";
import * as path from "path";
import { ROOT } from "./env.mjs";
import { getR2TextObject, putR2TextObject } from "./r2util.mjs";
import { listKeys } from "./r2list.mjs";

const AUDIO_RE = /\.(mp3|m4a|aac|wav|ogg)$/i;
const DEFAULT_PREFIX = "music/";
const DEFAULT_ROTATION_NAME = "_rotation.json";
const MAX_CAS_RETRIES = 8;

export function defaultRotationKey(prefix = DEFAULT_PREFIX) {
  const base = String(prefix || DEFAULT_PREFIX).replace(/\/?$/, "/");
  return `${base}${DEFAULT_ROTATION_NAME}`;
}

export function resolveRotationKey(musicCfg = {}) {
  if (musicCfg.rotation_key) return String(musicCfg.rotation_key);
  return defaultRotationKey(musicCfg.r2_prefix || DEFAULT_PREFIX);
}

export function emptyMusicState() {
  return { index: -1, assigned: {} };
}

export function normalizeMusicState(raw) {
  const base = emptyMusicState();
  if (!raw || typeof raw !== "object") return base;
  const assigned =
    raw.assigned && typeof raw.assigned === "object" && !Array.isArray(raw.assigned)
      ? { ...raw.assigned }
      : {};
  const index = Number.isFinite(Number(raw.index)) ? Number(raw.index) : -1;
  return { index, assigned };
}

/** Pure: pick track + next state. changed=false when sticky remount. */
export function chooseMusicTrack(objectId, tracks, state) {
  const sorted = [...tracks].sort();
  if (!sorted.length) {
    throw new Error("chooseMusicTrack: empty track list");
  }
  const current = normalizeMusicState(state);
  const previous = current.assigned[objectId];
  if (previous && sorted.includes(previous)) {
    return { track: previous, state: current, changed: false };
  }

  const next = (Number(current.index) + 1) % sorted.length;
  const track = sorted[next];
  const nextState = {
    index: next,
    assigned: { ...current.assigned, [objectId]: track },
  };
  return { track, state: nextState, changed: true };
}

export function filterMusicTracks(keys, rotationKey) {
  return (keys || []).filter(
    (k) => AUDIO_RE.test(k) && k !== rotationKey && !k.endsWith(`/${DEFAULT_ROTATION_NAME}`)
  );
}

export async function listMusicTracks(prefix, rotationKey) {
  const key = rotationKey || defaultRotationKey(prefix);
  const keys = await listKeys(prefix);
  return filterMusicTracks(keys, key);
}

function localLegacyStatePath() {
  return path.resolve(ROOT, "data/music_rotation.json");
}

function readLocalLegacyState() {
  const p = localLegacyStatePath();
  try {
    if (!fs.existsSync(p)) return null;
    return normalizeMusicState(JSON.parse(fs.readFileSync(p, "utf8")));
  } catch {
    return null;
  }
}

async function loadRotationFromR2(rotationKey) {
  const obj = await getR2TextObject(rotationKey);
  if (!obj) return { state: null, etag: null };
  try {
    return { state: normalizeMusicState(JSON.parse(obj.body)), etag: obj.etag };
  } catch (err) {
    throw new Error(`Music rotation: invalid JSON at ${rotationKey}: ${err.message}`);
  }
}

/**
 * Load state from R2. If missing, one-shot seed from local legacy file (if any),
 * then create the R2 object (IfNoneMatch). Concurrent first writers: loser retries.
 */
async function loadOrSeedRotationState(rotationKey) {
  const remote = await loadRotationFromR2(rotationKey);
  if (remote.state) return remote;

  const legacy = readLocalLegacyState();
  const seed = legacy || emptyMusicState();
  if (legacy) {
    console.log(
      `Music rotation: R2 ${rotationKey} missing — seeding from local data/music_rotation.json ` +
        `(index=${seed.index}, assigned=${Object.keys(seed.assigned).length})`
    );
  }

  const body = JSON.stringify(seed, null, 2);
  const created = await putR2TextObject(rotationKey, body, { etag: null });
  if (created) {
    console.log(`Music rotation: created ${rotationKey} in R2`);
  }

  // Winner or loser of the create race — always re-read for a fresh ETag.
  const again = await loadRotationFromR2(rotationKey);
  if (again.state) return again;
  return { state: seed, etag: null };
}

async function persistRotationState(rotationKey, state, etag) {
  const body = JSON.stringify(state, null, 2);
  return putR2TextObject(rotationKey, body, { etag });
}

/**
 * Round-robin pick with R2 CAS. Sticky remount does not write.
 */
export async function pickMusicTrackRoundRobin(objectId, tracks, musicCfg = {}) {
  if (!objectId) throw new Error("pickMusicTrackRoundRobin: objectId required");
  const rotationKey = resolveRotationKey(musicCfg);
  const sorted = filterMusicTracks(tracks, rotationKey);
  if (!sorted.length) {
    throw new Error("pickMusicTrackRoundRobin: no audio tracks");
  }

  let lastErr = null;

  for (let attempt = 1; attempt <= MAX_CAS_RETRIES; attempt++) {
    const { state, etag } = await loadOrSeedRotationState(rotationKey);
    const { track, state: nextState, changed } = chooseMusicTrack(objectId, sorted, state);

    if (!changed) {
      console.log(`Music rotation: sticky ${objectId} → ${track}`);
      return track;
    }

    const ok = await persistRotationState(rotationKey, nextState, etag);
    if (ok) {
      console.log(
        `Music rotation: ${objectId} → ${track} (index=${nextState.index}/${sorted.length}, r2=${rotationKey})`
      );
      return track;
    }

    lastErr = new Error(`Music rotation CAS conflict on ${rotationKey} (attempt ${attempt})`);
  }

  throw lastErr || new Error(`Music rotation: failed to persist after ${MAX_CAS_RETRIES} retries`);
}

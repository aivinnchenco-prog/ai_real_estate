import { geminiSelectDiverse, isGeminiSelectorAvailable } from "./selectPhotosGemini.mjs";

const JUNK_RE =
  /kettle|dryer|cutting|board|meeting|playroom|pinball|arcade|foosball|index\.html|logo|floorplan|план|схем/i;

const CATEGORY_RULES = [
  { cat: "exterior", re: /exterior|facade|building|entrance|outside|outdoor|фасад|экстер|въезд|территор|комплекс/i },
  { cat: "view", re: /view|panorama|skyline|вид|панорам|landscape/i },
  { cat: "pool", re: /pool|swim|бассейн/i },
  { cat: "living", re: /living|lounge|salon|гостин|sofa/i },
  { cat: "dining", re: /dining|столов/i },
  { cat: "kitchen", re: /kitchen|кухн/i },
  { cat: "bedroom", re: /bedroom|bed_|спальн|master.?bed/i },
  { cat: "bathroom", re: /bath|shower|toilet|сануз|ванн|wc/i },
  { cat: "garden", re: /garden|terrace|balcony|балкон|террас|сад|patio/i },
];

const CATEGORY_ORDER = [
  "exterior",
  "view",
  "pool",
  "living",
  "dining",
  "kitchen",
  "bedroom",
  "bathroom",
  "garden",
];

function photoNum(key) {
  const m = key.match(/(\d+)/);
  return m ? Number(m[1]) : 0;
}

export function inferPhotoCategory(key) {
  const name = String(key).toLowerCase();
  for (const { cat, re } of CATEGORY_RULES) {
    if (re.test(name)) return cat;
  }
  return null;
}

function segmentCategory(index, total, topK) {
  const segmentSize = Math.max(1, Math.floor(total / topK));
  return `segment_${Math.floor(index / segmentSize)}`;
}

function enrichItems(imageItems, topK) {
  const sorted = [...imageItems].sort((a, b) => photoNum(a.key) - photoNum(b.key));
  return sorted.map((item, index) => ({
    ...item,
    category: inferPhotoCategory(item.key) || segmentCategory(index, sorted.length, topK),
    index,
  }));
}

export function orderAndCapCategories(items, topK, maxPerCategory = 1) {
  const pool = items.map((item) =>
    typeof item === "string"
      ? { key: item, category: inferPhotoCategory(item) || "other" }
      : { key: item.key, category: item.category || inferPhotoCategory(item.key) || "other" }
  );

  const picked = [];
  const used = new Set();
  const catCount = new Map();

  const take = (item) => {
    if (!item || used.has(item.key) || picked.length >= topK) return false;
    const cat = item.category || "other";
    if ((catCount.get(cat) || 0) >= maxPerCategory) return false;
    picked.push(item);
    used.add(item.key);
    catCount.set(cat, (catCount.get(cat) || 0) + 1);
    return true;
  };

  for (const cat of ["exterior", "view"]) {
    const cand = pool.find((i) => i.category === cat);
    if (take(cand)) break;
  }
  if (!picked.length && pool.length) take(pool[0]);

  for (const cat of CATEGORY_ORDER) {
    if (picked.length >= topK) break;
    const cand = pool.find((i) => i.category === cat && !used.has(i.key));
    take(cand);
  }

  for (const item of pool) {
    if (picked.length >= topK) break;
    take(item);
  }

  return picked.slice(0, topK);
}

export function fallbackSelectDiverse(imageItems, topK, maxPerCategory = 1) {
  const clean = imageItems.filter((i) => !JUNK_RE.test(i.key));
  const pool = clean.length >= topK ? clean : imageItems;
  const enriched = enrichItems(pool, topK);

  if (enriched.length <= topK) {
    return orderAndCapCategories(enriched, topK, maxPerCategory).map((i) => i.key);
  }

  const byCategory = new Map();
  for (const item of enriched) {
    if (!byCategory.has(item.category)) byCategory.set(item.category, []);
    byCategory.get(item.category).push(item);
  }

  const selected = [];
  const used = new Set();
  const catCount = new Map();

  const takeItem = (item) => {
    if (!item || used.has(item.key) || selected.length >= topK) return;
    const cat = item.category;
    if ((catCount.get(cat) || 0) >= maxPerCategory) return;
    selected.push(item);
    used.add(item.key);
    catCount.set(cat, (catCount.get(cat) || 0) + 1);
  };

  const exteriorCandidates = [
    ...(byCategory.get("exterior") || []),
    ...(byCategory.get("view") || []),
  ];
  if (exteriorCandidates.length) {
    takeItem(exteriorCandidates[0]);
  } else {
    takeItem(enriched[0]);
  }

  for (const cat of CATEGORY_ORDER) {
    if (selected.length >= topK) break;
    const group = byCategory.get(cat) || [];
    const cand = group.find((i) => !used.has(i.key));
    if (cand) takeItem(cand);
  }

  while (selected.length < topK) {
    let best = null;
    let bestScore = -1;
    for (const item of enriched) {
      if (used.has(item.key)) continue;
      if ((catCount.get(item.category) || 0) >= maxPerCategory) continue;

      const minIndexGap = selected.length
        ? Math.min(...selected.map((s) => Math.abs(s.index - item.index)))
        : item.index;
      const categoryBonus = CATEGORY_ORDER.includes(item.category) ? 2 : 0;
      const score = minIndexGap + categoryBonus;
      if (score > bestScore) {
        bestScore = score;
        best = item;
      }
    }
    if (!best) break;
    takeItem(best);
  }

  return orderAndCapCategories(selected, topK, maxPerCategory).map((i) => i.key);
}

/**
 * Добор до topK, когда куратор вернул меньше (жёсткие пороги перцентиля/MMR).
 * Кандидаты — фото, которые куратор НЕ отбраковал явно (не мусор, не брак),
 * а просто не взял в топ. Жадно берём кадр с максимальным разрывом по номеру
 * от уже выбранных — меньше шанс почти-дубля соседнего кадра.
 * Экспортирована для юнит-тестов.
 */
export function topUpSelection(selectedKeys, allKeys, rejectedKeys, topK) {
  const result = [...selectedKeys];
  if (result.length >= topK) return result.slice(0, topK);

  const excluded = new Set([...selectedKeys, ...rejectedKeys]);
  const candidates = allKeys.filter((k) => !excluded.has(k));

  while (result.length < topK && candidates.length) {
    let bestIdx = 0;
    let bestGap = -1;
    for (let i = 0; i < candidates.length; i++) {
      const num = photoNum(candidates[i]);
      const gap = Math.min(...result.map((k) => Math.abs(photoNum(k) - num)));
      if (gap > bestGap) {
        bestGap = gap;
        bestIdx = i;
      }
    }
    result.push(candidates.splice(bestIdx, 1)[0]);
  }
  return result;
}

async function curatorHealthy(healthUrl) {
  try {
    const res = await fetch(healthUrl, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}

async function curatorSelectDiverse(imageItems, cfg, diverseUrl) {
  const topK = Math.min(cfg.image_count || 9, cfg.max_images_per_request || 9);
  const maxPerCategory = cfg.max_per_category ?? 1;
  const body = {
    images: imageItems,
    top_k: topK,
    max_per_category: maxPerCategory,
    max_similarity: cfg.max_similarity ?? 0.88,
    mmr_lambda: cfg.mmr_lambda ?? 0.6,
  };
  const res = await fetch(diverseUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Curator diverse HTTP ${res.status}`);
  const data = await res.json();
  if (data.warning) console.warn(`Curator diverse: ${data.warning}`);
  const keys = orderAndCapCategories(data.selected, topK, maxPerCategory).map((s) => s.key);
  const rejectedKeys = (data.rejected || []).map((r) => r.key);
  return { keys, rejectedKeys };
}

function resolveDiverseUrl(cfg) {
  // На сервере (docker-compose) куратор доступен как http://curator:8077 —
  // env-переопределение важнее локального 127.0.0.1 из config/*.json
  const base = (process.env.CURATOR_BASE_URL || "").replace(/\/$/, "");
  if (base) return `${base}/select-diverse`;
  return cfg.curator_diverse_url;
}

/**
 * Каскад отбора (каждая ступень автономна, ручной запуск не нужен):
 *   1. CLIP-куратор (curator_service.py) — если жив по health;
 *      при нехватке до topK добираем из неотбракованных кадров.
 *   2. Gemini vision (GEMINI_API_KEY) — работает без локальных сервисов.
 *   3. Fallback по имени файла — последний рубеж (photo_NNN без категорий).
 */
export async function selectPhotosSeedance(imageItems, cfg, { curatorFallback = true } = {}) {
  const topK = Math.min(cfg.image_count || 9, cfg.max_images_per_request || 9);
  const maxPerCategory = cfg.max_per_category ?? 1;
  const diverseUrl = resolveDiverseUrl(cfg);
  const healthUrl = diverseUrl?.replace(/\/select-diverse$/, "/health");

  if (diverseUrl && healthUrl && (await curatorHealthy(healthUrl))) {
    try {
      const { keys, rejectedKeys } = await curatorSelectDiverse(imageItems, cfg, diverseUrl);
      if (!keys.length) {
        throw new Error("curator returned 0 images (all rejected)");
      }
      const full = topUpSelection(keys, imageItems.map((i) => i.key), rejectedKeys, topK);
      if (full.length > keys.length) {
        console.warn(`Curator diverse: topped up ${keys.length} → ${full.length} images`);
      }
      console.log(`Photo select: curator diverse, ${full.length} images (max ${maxPerCategory}/category)`);
      return full;
    } catch (err) {
      if (!curatorFallback) throw err;
      console.warn("Curator diverse failed, trying next selector:", err.message);
    }
  } else if (!curatorFallback && diverseUrl) {
    throw new Error(`Curator unavailable at ${healthUrl}`);
  } else if (diverseUrl) {
    console.warn("Curator offline — trying Gemini selector");
  }

  if (isGeminiSelectorAvailable(cfg)) {
    try {
      const picked = await geminiSelectDiverse(imageItems, cfg);
      // max_per_category тут не применяем: у виллы может быть несколько
      // спален (разные локации) — за «1 кадр на локацию» отвечает промпт
      const keys = picked.map((i) => i.key);
      console.log(`Photo select: gemini vision, ${keys.length} images`);
      return keys;
    } catch (err) {
      console.warn("Gemini selector failed, using filename fallback:", err.message);
    }
  } else {
    console.warn("Gemini selector unavailable (no GEMINI_API_KEY) — filename fallback");
  }

  const keys = fallbackSelectDiverse(imageItems, topK, maxPerCategory);
  console.log(`Photo select: fallback diverse, ${keys.length} images (exterior first, max ${maxPerCategory}/category)`);
  return keys;
}

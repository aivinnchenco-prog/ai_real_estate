/**
 * selectPhotosGemini.mjs — отбор лучших фото «мозгами Gemini» (vision).
 *
 * Автономная ступень отбора: не требует локального CLIP-сервиса (torch),
 * нужен только GEMINI_API_KEY (корневой .env → sync_env.py → agent_3_director/.env).
 * Скачивает фото по публичным R2-URL, шлёт байты в Gemini (inline_data)
 * и получает JSON со списком выбранных ключей + категориями.
 */

const GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models";
const DEFAULT_MODEL = "gemini-2.5-flash";

// Лимиты запроса generateContent: ~20MB на весь payload
const MAX_IMAGE_BYTES = 6 * 1024 * 1024;
const MAX_TOTAL_BYTES = 18 * 1024 * 1024;
const DOWNLOAD_CONCURRENCY = 5;

const KNOWN_CATEGORIES = new Set([
  "exterior",
  "view",
  "pool",
  "living",
  "dining",
  "kitchen",
  "bedroom",
  "bathroom",
  "garden",
  "other",
]);

export function isGeminiSelectorAvailable(cfg = {}) {
  if (cfg.gemini_selector?.enabled === false) return false;
  return Boolean(process.env.GEMINI_API_KEY);
}

/**
 * Валидация ответа Gemini: только ключи из пула, без дублей, не больше topK.
 * Экспортирована отдельно для юнит-тестов (чистая функция, без сети).
 */
export function normalizeGeminiSelection(raw, poolKeys, topK) {
  const list = Array.isArray(raw) ? raw : raw?.selected;
  if (!Array.isArray(list)) {
    throw new Error("Gemini selector: response has no 'selected' array");
  }
  const poolSet = new Set(poolKeys);
  const used = new Set();
  const out = [];
  for (const item of list) {
    const key = typeof item === "string" ? item : item?.key;
    if (!key || !poolSet.has(key) || used.has(key)) continue;
    const rawCat = typeof item === "object" ? String(item?.category || "").toLowerCase() : "";
    out.push({ key, category: KNOWN_CATEGORIES.has(rawCat) ? rawCat : "other" });
    used.add(key);
    if (out.length >= topK) break;
  }
  // 1-й кадр — экстерьер/вид, если Gemini выбрал такой, но поставил не первым
  const heroIdx = out.findIndex((i) => i.category === "exterior" || i.category === "view");
  if (heroIdx > 0) {
    const [hero] = out.splice(heroIdx, 1);
    out.unshift(hero);
  }
  return out;
}

function guessMime(key) {
  if (/\.png$/i.test(key)) return "image/png";
  if (/\.webp$/i.test(key)) return "image/webp";
  return "image/jpeg";
}

async function downloadImages(imageItems) {
  const results = [];
  for (let i = 0; i < imageItems.length; i += DOWNLOAD_CONCURRENCY) {
    const batch = imageItems.slice(i, i + DOWNLOAD_CONCURRENCY);
    const settled = await Promise.all(
      batch.map(async (item) => {
        try {
          const res = await fetch(item.url, { signal: AbortSignal.timeout(30000) });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const buf = Buffer.from(await res.arrayBuffer());
          return { key: item.key, buf };
        } catch (err) {
          console.warn(`Gemini selector: skip ${item.key} (download: ${err.message})`);
          return null;
        }
      })
    );
    results.push(...settled.filter(Boolean));
  }
  return results;
}

function buildPrompt(topK) {
  return [
    "You are a photo curator for a vertical (9:16) Instagram real-estate video.",
    `From the property photos below, select the BEST ${topK} photos (fewer only if there are not enough acceptable ones).`,
    "",
    "Rules:",
    "1. Instagram quality only: sharp, bright, well-composed, spacious-looking shots.",
    "2. REJECT junk: close-ups of appliances (hair dryer, kettle), dishes/cutlery, a single chair or piece of furniture, power outlets, documents, screenshots, maps, floor plans, logos, blurry or dark photos, random trees, empty beach or street shots that do not showcase the property.",
    "3. The FIRST selected photo must be the building exterior or the best view shot, if any acceptable one exists.",
    "4. Maximum ONE photo per physical location. Two different bedrooms are different locations; two angles of the same room are duplicates — pick the better one.",
    "5. No near-duplicate frames.",
    "6. Prefer covering diverse spaces: exterior, view, pool, living room, dining, kitchen, bedrooms, bathroom, terrace/garden.",
    "",
    "Each image is preceded by a line 'KEY: <filename>'. Return the exact keys.",
    'Category must be one of: exterior, view, pool, living, dining, kitchen, bedroom, bathroom, garden, other.',
    "Order the selected array in the sequence the photos should appear in the video (exterior/view first, then a natural walkthrough).",
  ].join("\n");
}

const RESPONSE_SCHEMA = {
  type: "object",
  properties: {
    selected: {
      type: "array",
      items: {
        type: "object",
        properties: {
          key: { type: "string" },
          category: { type: "string" },
        },
        required: ["key", "category"],
      },
    },
  },
  required: ["selected"],
};

async function callGemini(model, apiKey, body) {
  const url = `${GEMINI_API}/${model}:generateContent?key=${apiKey}`;
  for (let attempt = 1; ; attempt++) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(120000),
    });
    if (res.ok) return res.json();
    const retryable = [429, 500, 503].includes(res.status);
    const text = (await res.text()).slice(0, 300);
    if (!retryable || attempt >= 2) {
      throw new Error(`Gemini HTTP ${res.status}: ${text}`);
    }
    console.warn(`Gemini selector: HTTP ${res.status}, retry in 3s`);
    await new Promise((r) => setTimeout(r, 3000));
  }
}

/**
 * @param {{key: string, url: string}[]} imageItems
 * @param {object} cfg — seedance.json / wan.json
 * @returns {Promise<{key: string, category: string}[]>}
 */
export async function geminiSelectDiverse(imageItems, cfg) {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) throw new Error("GEMINI_API_KEY not set");
  const model = cfg.gemini_selector?.model || process.env.GEMINI_MODEL || DEFAULT_MODEL;
  const topK = Math.min(cfg.image_count || 9, cfg.max_images_per_request || 9);

  const downloaded = await downloadImages(imageItems);
  if (!downloaded.length) throw new Error("Gemini selector: no images downloaded");

  const parts = [{ text: buildPrompt(topK) }];
  let total = 0;
  let included = 0;
  for (const { key, buf } of downloaded) {
    if (buf.length > MAX_IMAGE_BYTES) {
      console.warn(`Gemini selector: skip ${key} (too large: ${buf.length} bytes)`);
      continue;
    }
    if (total + buf.length > MAX_TOTAL_BYTES) {
      console.warn(`Gemini selector: payload limit reached, sending first ${included} images`);
      break;
    }
    parts.push({ text: `KEY: ${key}` });
    parts.push({ inline_data: { mime_type: guessMime(key), data: buf.toString("base64") } });
    total += buf.length;
    included++;
  }
  if (!included) throw new Error("Gemini selector: no images fit into request");

  const body = {
    contents: [{ role: "user", parts }],
    generationConfig: {
      temperature: 0.2,
      responseMimeType: "application/json",
      responseSchema: RESPONSE_SCHEMA,
    },
  };

  const data = await callGemini(model, apiKey, body);
  const text = data?.candidates?.[0]?.content?.parts?.map((p) => p.text || "").join("");
  if (!text) throw new Error("Gemini selector: empty response");

  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error(`Gemini selector: non-JSON response: ${text.slice(0, 200)}`);
  }

  const poolKeys = downloaded.map((d) => d.key);
  const selection = normalizeGeminiSelection(parsed, poolKeys, topK);
  if (!selection.length) throw new Error("Gemini selector: 0 valid keys in response");
  return selection;
}

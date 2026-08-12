import { readFileSync } from "fs";
import { resolve } from "path";
import { ROOT } from "./env.mjs";

const NOTION_VERSION = () => process.env.NOTION_API_VERSION || "2022-06-28";

export function loadNotionConfig() {
  return JSON.parse(readFileSync(resolve(ROOT, "config/notion.json"), "utf8"));
}

export function formatDbId(raw) {
  const id = raw || process.env.NOTION_DB_ID || process.env.NOTION_DATABASE_ID;
  if (!id) throw new Error("NOTION_DB_ID not set");
  if (id.includes("-")) return id;
  return `${id.slice(0, 8)}-${id.slice(8, 12)}-${id.slice(12, 16)}-${id.slice(16, 20)}-${id.slice(20)}`;
}

function notionHeaders() {
  const key = process.env.NOTION_API_KEY;
  if (!key) throw new Error("NOTION_API_KEY not set");
  return {
    Authorization: `Bearer ${key}`,
    "Notion-Version": NOTION_VERSION(),
    "Content-Type": "application/json",
  };
}

export async function queryByObjectId(objectId, fields) {
  const res = await fetch(`https://api.notion.com/v1/databases/${formatDbId()}/query`, {
    method: "POST",
    headers: notionHeaders(),
    body: JSON.stringify({
      filter: { property: fields.object_id, rich_text: { equals: objectId } },
    }),
  });
  if (!res.ok) throw new Error(`Notion query: ${await res.text()}`);
  const data = await res.json();
  return data.results[0] || null;
}

export async function updatePage(pageId, properties) {
  const res = await fetch(`https://api.notion.com/v1/pages/${pageId}`, {
    method: "PATCH",
    headers: notionHeaders(),
    body: JSON.stringify({ properties }),
  });
  if (!res.ok) throw new Error(`Notion update: ${await res.text()}`);
}

function readErrorCount(page) {
  const n = page?.properties?.error_count?.number;
  return typeof n === "number" ? n : 0;
}

export async function setSeedanceUrl(page, fields, url, statusName = null) {
  const properties = {
    [fields.video_seedance]: { url },
    [fields.last_error]: { rich_text: [] },
  };
  if (statusName) properties[fields.status] = { status: { name: statusName } };
  await updatePage(page.id, properties);
}

export async function setCarouselUrl(page, fields, url) {
  await updatePage(page.id, {
    [fields.carousel_url || "carousel_url"]: { url },
  });
}

/** Ссылка на все брендированные фото: R2 {id}/brand_open_home/index.html. */
export async function setBrandOpenHomeUrl(page, fields, url) {
  await updatePage(page.id, {
    [fields.brand_open_home_url || "brand_open_home_url"]: { url },
  });
}

export async function setMontageStart(page, fields, statusName) {
  await updatePage(page.id, {
    [fields.status]: { status: { name: statusName } },
    [fields.last_error]: { rich_text: [] },
  });
}

export async function setError(page, fields, message, statusName = null) {
  const properties = {
    [fields.last_error]: {
      rich_text: [{ text: { content: String(message).slice(0, 2000) } }],
    },
    error_count: { number: readErrorCount(page) + 1 },
  };
  if (statusName) properties[fields.status] = { status: { name: statusName } };
  await updatePage(page.id, properties);
}

export function pageStatus(page, fields) {
  return page.properties?.[fields.status]?.status?.name || null;
}

export function pageSeedanceUrl(page, fields) {
  return page.properties?.[fields.video_seedance]?.url || null;
}

export function pageTitle(page, fields) {
  return page.properties?.[fields.title]?.title?.[0]?.plain_text || "";
}

function readRichText(page, fieldName) {
  const parts = page.properties?.[fieldName]?.rich_text || [];
  return parts.map((p) => p.plain_text || "").join("").trim();
}

function readNumber(page, fieldName) {
  const n = page.properties?.[fieldName]?.number;
  return typeof n === "number" ? n : null;
}

function readSelect(page, fieldName) {
  return page.properties?.[fieldName]?.select?.name || null;
}

function readMultiSelect(page, fieldName) {
  const opts = page.properties?.[fieldName]?.multi_select || [];
  return opts.map((o) => o.name || "").filter(Boolean);
}

const MONTHS_RU = [
  "",
  "январь",
  "февраль",
  "март",
  "апрель",
  "май",
  "июнь",
  "июль",
  "август",
  "сентябрь",
  "октябрь",
  "ноябрь",
  "декабрь",
];

const MONTHLY_CHIP_RE = /^(\d{4}-\d{2})\s*([·≈~])\s*([\d\s\u00a0]+)/;

/** Парсит плашки multi_select monthly_prices → { "2026-08": { price, status } }. */
export function parseMonthlyPrices(page, fieldName) {
  const prop = page?.properties?.[fieldName];
  if (!prop || prop.type !== "multi_select") return {};
  const result = {};
  for (const opt of prop.multi_select || []) {
    const m = String(opt.name || "").trim().match(MONTHLY_CHIP_RE);
    if (!m) continue;
    const digits = m[3].replace(/\D/g, "");
    if (!digits) continue;
    result[m[1]] = {
      price: Number(digits),
      status: m[2] === "·" ? "monthly" : "prorated",
    };
  }
  return result;
}

/** Первый месяц строго после текущего календарного, у которого есть цена. */
export function firstUpcomingMonthKey(monthly, now = new Date()) {
  const y = now.getFullYear();
  const m = now.getMonth() + 1;
  const current = `${y}-${String(m).padStart(2, "0")}`;
  for (const key of Object.keys(monthly || {}).sort()) {
    if (key <= current) continue;
    if (monthly[key]?.price) return key;
  }
  return null;
}

function monthNameRu(yyyyMm) {
  const month = Number(String(yyyyMm || "").split("-")[1]);
  return MONTHS_RU[month] || "";
}

/** Объекты Facebook (F_*). */
export function isFbObject(objectId) {
  return /^F[_-]/i.test(String(objectId || "").trim());
}

/** Объекты Airbnb (A_*). */
export function isAirbnbObject(objectId) {
  return /^A[_-]/i.test(String(objectId || "").trim());
}

/**
 * Цена для хука/оверлея — только колонка «Цена за месяц».
 * Без monthly_prices, без «Цена за год» и без деления /12.
 */
function formatOverlayPrice(page, priceField, _fallbackField, _monthlyField, _objectId) {
  const monthly = priceField ? readNumber(page, priceField) : null;
  if (monthly) {
    return { price: monthly.toLocaleString("en-US"), period: "в месяц" };
  }
  return { price: "", period: "" };
}

export function pageVideoOverlayMeta(page, fields, overlayFields = {}, objectId = null) {
  const housingField = overlayFields.housing_type || fields.housing_type || "Тип жилья";
  const roomsField = overlayFields.rooms || fields.rooms || "Количество комнат";
  const priceField = overlayFields.price || fields.price_monthly || "Цена за месяц";
  const priceFallback = overlayFields.price_fallback || fields.price_yearly || "Цена за год";
  const monthlyField =
    overlayFields.monthly_prices || fields.monthly_prices || "monthly_prices";
  const districtField = overlayFields.district || fields.district || "Район";

  const oid = objectId || pageObjectId(page, fields);

  const housingType = readSelect(page, housingField);
  const rooms = readNumber(page, roomsField);
  const district = readRichText(page, districtField);
  const { price, period } = formatOverlayPrice(
    page,
    priceField,
    priceFallback,
    monthlyField,
    oid
  );

  return {
    title_phrase: housingType || "",
    bedrooms: rooms != null ? String(rooms) : "",
    price,
    period,
    district,
  };
}

/** Метаданные для бейджей дизайн-карусели: спальни, санузлы, тип жилья, удобства. */
export function pageCarouselMeta(page, fields) {
  const rooms = readNumber(page, fields.rooms || "Количество комнат");
  const bathrooms = readNumber(page, fields.bathrooms || "Количество сан.узлов");
  return {
    bedrooms: rooms != null ? String(rooms) : "",
    bathrooms: bathrooms != null ? String(bathrooms) : "",
    housing_type: readSelect(page, fields.housing_type || "Тип жилья") || "",
    amenities: readMultiSelect(page, fields.amenities || "Удобства"),
    view: readSelect(page, fields.view || "Вид") || "",
  };
}

export function pageObjectId(page, fields) {
  return page.properties?.[fields.object_id]?.rich_text?.[0]?.plain_text?.trim() || null;
}

// «Монтаж»: только явное ДА включает монтаж; пусто и НЕТ — пропуск.
const MONTAGE_ON = new Set(["ДА", "YES", "DA"]);
export function pageMontageEnabled(page, fields) {
  if (!fields.montage) return false;
  const value = (readSelect(page, fields.montage) || "").trim().toUpperCase();
  return MONTAGE_ON.has(value);
}

/** «Видео-движок»: Seedance 2.0 → seedance, Wan 2.7 → wan. */
export function pageVideoEngineId(page, fields) {
  if (!fields.video_engine) return null;
  const value = (readSelect(page, fields.video_engine) || "").trim();
  if (value === "Seedance 2.0") return "seedance";
  if (value === "Wan 2.7") return "wan";
  return null;
}

export async function queryLatestPages(fields, { limit = 20 } = {}) {
  const sorts = fields.created_at
    ? [{ property: fields.created_at, direction: "descending" }]
    : [{ timestamp: "created_time", direction: "descending" }];

  const res = await fetch(`https://api.notion.com/v1/databases/${formatDbId()}/query`, {
    method: "POST",
    headers: notionHeaders(),
    body: JSON.stringify({ sorts, page_size: limit }),
  });
  if (!res.ok) throw new Error(`Notion query: ${await res.text()}`);
  const data = await res.json();
  return data.results || [];
}

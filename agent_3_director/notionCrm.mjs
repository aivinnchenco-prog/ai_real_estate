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

export async function setSeedanceUrl(page, fields, url) {
  await updatePage(page.id, {
    [fields.video_seedance]: { url },
    [fields.last_error]: { rich_text: [] },
  });
}

export async function setError(page, fields, message) {
  await updatePage(page.id, {
    [fields.last_error]: {
      rich_text: [{ text: { content: String(message).slice(0, 2000) } }],
    },
    error_count: { number: readErrorCount(page) + 1 },
  });
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

function formatOverlayPrice(page, priceField, fallbackField) {
  const monthly = priceField ? readNumber(page, priceField) : null;
  const yearly = fallbackField ? readNumber(page, fallbackField) : null;

  if (monthly) {
    return { price: monthly.toLocaleString("en-US"), period: "в месяц" };
  }
  if (yearly) {
    return { price: yearly.toLocaleString("en-US"), period: "в год" };
  }
  return { price: "", period: "" };
}

export function pageVideoOverlayMeta(page, fields, overlayFields = {}) {
  const housingField = overlayFields.housing_type || fields.housing_type || "Тип жилья";
  const roomsField = overlayFields.rooms || fields.rooms || "Количество комнат";
  const priceField = overlayFields.price || fields.price_monthly || "Цена за месяц";
  const priceFallback = overlayFields.price_fallback || fields.price_yearly || "Цена за год";
  const districtField = overlayFields.district || fields.district || "Район";

  const housingType = readSelect(page, housingField);
  const rooms = readNumber(page, roomsField);
  const district = readRichText(page, districtField);
  const { price, period } = formatOverlayPrice(page, priceField, priceFallback);

  return {
    title_phrase: housingType || "",
    bedrooms: rooms != null ? String(rooms) : "",
    price,
    period,
    district,
  };
}

export function pageObjectId(page, fields) {
  return page.properties?.[fields.object_id]?.rich_text?.[0]?.plain_text?.trim() || null;
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

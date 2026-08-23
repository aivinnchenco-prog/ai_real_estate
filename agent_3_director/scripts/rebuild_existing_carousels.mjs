#!/usr/bin/env node
/**
 * Пересобрать дизайн-карусель только у объектов, у которых в Notion
 * уже заполнен carousel_url. Остальные объекты не трогает.
 *
 *   node scripts/rebuild_existing_carousels.mjs --list-only
 *   node scripts/rebuild_existing_carousels.mjs
 */
import { spawnSync } from "child_process";
import { resolve } from "path";
import { loadEnv, ROOT } from "../env.mjs";
import { loadNotionConfig, formatDbId, pageObjectId } from "../notionCrm.mjs";

function parseArgs() {
  return { listOnly: process.argv.includes("--list-only") };
}

async function listPagesWithCarouselUrl(fields) {
  const headers = {
    Authorization: `Bearer ${process.env.NOTION_API_KEY}`,
    "Notion-Version": process.env.NOTION_API_VERSION || "2022-06-28",
    "Content-Type": "application/json",
  };
  const carouselField = fields.carousel_url || "carousel_url";
  const pages = [];
  let cursor;
  do {
    const body = {
      page_size: 100,
      filter: { property: carouselField, url: { is_not_empty: true } },
    };
    if (cursor) body.start_cursor = cursor;
    const res = await fetch(`https://api.notion.com/v1/databases/${formatDbId()}/query`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`Notion query: ${await res.text()}`);
    const data = await res.json();
    pages.push(...(data.results || []));
    cursor = data.has_more ? data.next_cursor : null;
  } while (cursor);
  return pages;
}

function rebuildOne(objectId) {
  const script = resolve(ROOT, "scripts/build_carousel.mjs");
  const proc = spawnSync(process.execPath, [script, "--object-id", objectId, "--force"], {
    cwd: ROOT,
    stdio: "inherit",
    env: process.env,
  });
  return proc.status === 0;
}

async function main() {
  loadEnv();
  if (!process.env.NOTION_API_KEY) throw new Error("NOTION_API_KEY not set");
  const { listOnly } = parseArgs();
  const { fields } = loadNotionConfig();
  const pages = await listPagesWithCarouselUrl(fields);
  const ids = pages
    .map((page) => pageObjectId(page, fields))
    .filter(Boolean);
  console.log(`Objects with carousel_url: ${ids.length}`);
  for (const id of ids) console.log(`  ${id}`);
  if (listOnly) return;

  let ok = 0;
  let fail = 0;
  for (const id of ids) {
    console.log(`\n===== rebuild carousel ${id} =====`);
    if (rebuildOne(id)) ok += 1;
    else {
      fail += 1;
      console.error(`FAILED ${id}`);
    }
  }
  console.log(`\nDone: ${ok} rebuilt, ${fail} failed, ${ids.length} total`);
  if (fail) process.exit(1);
}

main().catch((err) => {
  console.error("REBUILD FAILED:", err.message);
  process.exit(1);
});

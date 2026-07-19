#!/usr/bin/env node
/**
 * Unit: хук берёт цену + название первого предстоящего месяца из monthly_prices.
 */
import assert from "assert";
import {
  parseMonthlyPrices,
  firstUpcomingMonthKey,
  pageVideoOverlayMeta,
} from "../notionCrm.mjs";

const page = {
  properties: {
    "Тип жилья": { select: { name: "Вилла" } },
    "Количество комнат": { number: 4 },
    "Цена за месяц": { number: 40000 },
    "Цена за год": { number: null },
    Район: { rich_text: [{ plain_text: "Thalang" }] },
    monthly_prices: {
      type: "multi_select",
      multi_select: [
        { name: "2026-07 · 40 000 ฿" },
        { name: "2026-08 · 55 000 ฿" },
        { name: "2026-09 ≈ 60 000 ฿" },
      ],
    },
  },
};

const parsed = parseMonthlyPrices(page, "monthly_prices");
assert.strictEqual(parsed["2026-08"].price, 55000);
assert.strictEqual(
  firstUpcomingMonthKey(parsed, new Date("2026-07-16T12:00:00")),
  "2026-08"
);

const fields = {
  housing_type: "Тип жилья",
  rooms: "Количество комнат",
  price_monthly: "Цена за месяц",
  price_yearly: "Цена за год",
  monthly_prices: "monthly_prices",
  district: "Район",
};
const meta = pageVideoOverlayMeta(page, fields, {
  ...fields,
  price: "Цена за месяц",
  price_fallback: "Цена за год",
});

// Monkey-patch Date for meta would be hard; call format path via
// firstUpcomingMonthKey already tested. Re-run overlay with frozen now:
const orig = Date;
global.Date = class extends orig {
  constructor(...args) {
    if (args.length === 0) return new orig("2026-07-16T12:00:00Z");
    return new orig(...args);
  }
  static now() {
    return new orig("2026-07-16T12:00:00Z").getTime();
  }
};
const metaJuly = pageVideoOverlayMeta(page, fields, {
  price: "Цена за месяц",
  price_fallback: "Цена за год",
  monthly_prices: "monthly_prices",
  housing_type: "Тип жилья",
  rooms: "Количество комнат",
  district: "Район",
});
global.Date = orig;

assert.strictEqual(metaJuly.price, "55,000");
assert.strictEqual(metaJuly.period, "август");
assert.strictEqual(metaJuly.title_phrase, "Вилла");
console.log("OK overlay month:", metaJuly);

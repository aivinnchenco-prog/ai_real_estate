#!/usr/bin/env node
/**
 * Unit: хук с ценой — Airbnb (Цена за месяц + календарный месяц) и FB (годовой контракт).
 */
import assert from "assert";
import {
  parseMonthlyPrices,
  firstUpcomingMonthKey,
  nextCalendarMonthKey,
  pageVideoOverlayMeta,
  isFbObject,
  isAirbnbObject,
} from "../notionCrm.mjs";

const airbnbPage = {
  properties: {
    "Объект ID": { rich_text: [{ plain_text: "A_20260720_001" }] },
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

const parsed = parseMonthlyPrices(airbnbPage, "monthly_prices");
assert.strictEqual(parsed["2026-08"].price, 55000);
assert.strictEqual(
  firstUpcomingMonthKey(parsed, new Date("2026-07-16T12:00:00")),
  "2026-08"
);
assert.strictEqual(
  nextCalendarMonthKey(new Date("2026-07-16T12:00:00")),
  "2026-08"
);
assert.strictEqual(
  nextCalendarMonthKey(new Date("2026-12-15T12:00:00")),
  "2027-01"
);

const fields = {
  object_id: "Объект ID",
  housing_type: "Тип жилья",
  rooms: "Количество комнат",
  price_monthly: "Цена за месяц",
  price_yearly: "Цена за год",
  monthly_prices: "monthly_prices",
  district: "Район",
};

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

const airbnbMeta = pageVideoOverlayMeta(
  airbnbPage,
  fields,
  {
    price: "Цена за месяц",
    price_fallback: "Цена за год",
    monthly_prices: "monthly_prices",
    housing_type: "Тип жилья",
    rooms: "Количество комнат",
    district: "Район",
  },
  "A_20260720_001"
);
global.Date = orig;

assert.strictEqual(airbnbMeta.price, "40,000");
assert.strictEqual(airbnbMeta.period, "в месяц · август");
assert.ok(isAirbnbObject("A_20260720_001"));

const fbPage = {
  properties: {
    "Объект ID": { rich_text: [{ plain_text: "F_20260720_001" }] },
    "Тип жилья": { select: { name: "Кондоминиум" } },
    "Количество комнат": { number: 2 },
    "Цена за месяц": { number: null },
    "Цена за год": { number: 600000 },
    Район: { rich_text: [{ plain_text: "Bang Tao" }] },
    monthly_prices: {
      type: "multi_select",
      multi_select: [{ name: "2026-08 · 99 000 ฿" }],
    },
  },
};

const fbMeta = pageVideoOverlayMeta(fbPage, fields, {}, "F_20260720_001");
assert.strictEqual(fbMeta.price, "50,000");
assert.strictEqual(fbMeta.period, "в месяц · годовой контракт");
assert.ok(isFbObject("F_20260720_001"));

console.log("OK Airbnb overlay:", airbnbMeta);
console.log("OK FB overlay:", fbMeta);

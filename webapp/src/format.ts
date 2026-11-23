import type { DishCard, Meta, Tag } from "./api";

export const STATUS_LABEL: Record<string, string> = {
  can_cook: "вміємо",
  want_to_try: "хочемо спробувати",
  archived: "в архіві",
};

export const KIND_LABEL: Record<string, string> = {
  meal: "Прийом їжі",
  course: "Тип страви",
  ingredient: "Продукт",
  trait: "Особливості",
  cuisine: "Кухня",
};

export function cookedAgo(iso: string | null | undefined): string {
  if (!iso) return "ще не готували";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "готували сьогодні";
  if (days === 1) return "готували вчора";
  return `готували ${days} дн. тому`;
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("uk-UA", { day: "numeric", month: "long" });
}

export function tagIndex(meta: Meta): Map<number, Tag> {
  const m = new Map<number, Tag>();
  Object.values(meta.tags).forEach((list) => list.forEach((t) => m.set(t.id, t)));
  return m;
}

export function cookNames(meta: Meta, ids: number[]): string {
  return meta.members.filter((m) => ids.includes(m.id)).map((m) => m.name).join(", ");
}

/** Емодзі страви: тип страви, інакше основний продукт, інакше тарілка. */
export function dishEmoji(meta: Meta, d: DishCard): string {
  const idx = tagIndex(meta);
  const course = d.tag_ids.map((id) => idx.get(id)).find((t) => t?.kind === "course" && t.emoji);
  const main = d.main_ingredient_id ? idx.get(d.main_ingredient_id) : undefined;
  return course?.emoji ?? main?.emoji ?? "🍽";
}

/** Прийом їжі за часом: до 11 — сніданок, 11–16 — обід, далі вечеря. */
export function mealByTime(date = new Date()): string {
  const h = date.getHours();
  return h < 11 ? "breakfast" : h < 16 ? "lunch" : "dinner";
}

export function sourceLabel(url: string, type: string): string {
  const names: Record<string, string> = { tiktok: "TikTok", instagram: "Instagram", youtube: "YouTube" };
  if (names[type]) return names[type];
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

const FRACTIONS: Record<number, string> = { 0.25: "¼", 0.5: "½", 0.75: "¾" };
const METRIC = new Set(["г", "мл", "кг", "л"]);
/** Крок округлення для «штучних» одиниць; решта (шт., зубчик, без одиниці…) — по ½. */
const COUNT_STEP: Record<string, number> = { "ч. л.": 0.25, "склянка": 0.25, "щіпка": 1, "пучок": 1 };

function roundTo(q: number, step: number): number {
  return Math.max(step, Math.round(q / step) * step);
}

/** Кількість для іншої кількості порцій, округлена так, як відміряють на кухні. */
export function scaleQty(qty: number, unit: string | null | undefined, factor: number): number {
  const q = qty * factor;
  if (factor === 1) return qty;
  if (unit === "г" || unit === "мл") {
    if (q < 1) return Math.round(q * 10) / 10 || 0.1;
    return roundTo(q, q < 20 ? 1 : q < 100 ? 5 : q < 500 ? 10 : 50);
  }
  if (unit === "кг" || unit === "л") return roundTo(q, q < 1 ? 0.05 : 0.1);
  return roundTo(q, COUNT_STEP[unit ?? ""] ?? 0.5);
}

/** «1½ ст. л.», «250 г», «0,15 кг»; без кількості — лише одиниця («за смаком»). */
export function fmtQty(q: number | null | undefined, unit: string | null | undefined): string {
  if (q === null || q === undefined) return unit ?? "";
  let n: string;
  const whole = Math.floor(q);
  const frac = FRACTIONS[Math.round((q - whole) * 100) / 100];
  if (!METRIC.has(unit ?? "") && frac) n = (whole ? String(whole) : "") + frac;
  else n = String(Math.round(q * 100) / 100).replace(".", ",");
  return unit ? `${n} ${unit}` : n;
}

/** «1 порція», «2 порції», «5 порцій». */
export function portions(n: number): string {
  const d10 = n % 10, d100 = n % 100;
  const word = d10 === 1 && d100 !== 11 ? "порція"
    : d10 >= 2 && d10 <= 4 && (d100 < 12 || d100 > 14) ? "порції" : "порцій";
  return `${n} ${word}`;
}

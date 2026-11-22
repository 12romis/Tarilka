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

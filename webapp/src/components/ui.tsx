import type { ComponentChildren } from "preact";
import type { DishCard, Meta } from "../api";
import { cookNames, cookedAgo, dishEmoji } from "../format";
import { navigate } from "../router";
import { haptic, tg } from "../tg";

export function Chip(props: {
  active?: boolean;
  onClick: () => void;
  children: ComponentChildren;
  title?: string;
}) {
  return (
    <button
      type="button"
      class={"chip" + (props.active ? " chip--on" : "")}
      aria-pressed={props.active}
      title={props.title}
      onClick={() => {
        haptic.select();
        props.onClick();
      }}
    >
      {props.children}
    </button>
  );
}

export function Loader({ text = "Завантажую…" }: { text?: string }) {
  return <div class="muted center pad">{text}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : "Щось пішло не так";
  return <div class="error pad">😕 {msg}</div>;
}

/** Нижня кнопка-замінник MainButton, коли сторінку відкрито поза Telegram. */
export function FallbackMainButton(props: { text: string; onClick: () => void; busy?: boolean }) {
  if (tg) return null;
  return (
    <div class="bottombar">
      <button class="btn btn--primary btn--wide" disabled={props.busy} onClick={props.onClick}>
        {props.busy ? "…" : props.text}
      </button>
    </div>
  );
}

export function Cover({ dish, meta, big = false }: { dish: DishCard; meta: Meta; big?: boolean }) {
  const src = dish.cover ? (big ? dish.cover.url : dish.cover.thumb_url ?? dish.cover.url) : null;
  return src ? (
    <img class="cover" src={src} alt="" loading="lazy" decoding="async" />
  ) : (
    <div class="cover cover--empty" aria-hidden="true">{dishEmoji(meta, dish)}</div>
  );
}

/** Плитка в сітці каталогу та стрічках головної. */
export function DishTile({ dish, meta }: { dish: DishCard; meta: Meta }) {
  return (
    <a
      class="tile"
      href={`/dish/${dish.id}`}
      onClick={(e) => {
        e.preventDefault();
        navigate(`/dish/${dish.id}`);
      }}
    >
      <Cover dish={dish} meta={meta} />
      <div class="tile__body">
        <div class="tile__title">{dish.title}</div>
        <div class="tile__meta">
          {dish.status === "want_to_try" ? "🆕 хочемо спробувати" : cookedAgo(dish.last_cooked_at)}
        </div>
        {dish.cook_ids.length > 0 && (
          <div class="tile__meta">👤 {cookNames(meta, dish.cook_ids)}</div>
        )}
      </div>
    </a>
  );
}

export function Stars({ value, onChange }: { value: number | null; onChange?: (v: number) => void }) {
  return (
    <span class="stars" role={onChange ? "radiogroup" : undefined} aria-label="Оцінка">
      {[1, 2, 3, 4, 5].map((n) =>
        onChange ? (
          <button
            type="button"
            class={"star" + (value && n <= value ? " star--on" : "")}
            aria-label={`${n} з 5`}
            aria-checked={value === n}
            role="radio"
            onClick={() => onChange(n)}
          >
            ★
          </button>
        ) : (
          <span class={"star" + (value && n <= value ? " star--on" : "")}>★</span>
        ),
      )}
    </span>
  );
}

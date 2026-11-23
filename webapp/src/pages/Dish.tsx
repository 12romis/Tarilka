import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { ErrorBox, FallbackMainButton, Loader, Stars } from "../components/ui";
import {
  cookNames, cookedAgo, fmtQty, portions, scaleQty, shortDate, sourceLabel, STATUS_LABEL, tagIndex,
} from "../format";
import { invalidateMeta, useAsync, useMainButton, useMeta } from "../hooks";
import { navigate } from "../router";
import { haptic, openSource } from "../tg";

const DIFFICULTY = ["", "легко", "середньо", "складно"];

type Ingredient = { name: string; qty?: number | null; unit?: string | null };

/** Інгредієнти з перемикачем порцій; без вказаних порцій — як у рецепті й підказка. */
function Ingredients({ items, servings }: { items: Ingredient[]; servings: number | null }) {
  const [n, setN] = useState(servings ?? 0);
  useEffect(() => setN(servings ?? 0), [servings]);
  const factor = servings ? n / servings : 1;
  const change = (delta: number) => {
    setN((v) => Math.min(50, Math.max(1, v + delta)));
    haptic.select();
  };
  const hasQty = items.some((i) => i.qty);

  return (
    <section class="section">
      <div class="section__head">
        <h2>Інгредієнти</h2>
        {servings && hasQty && (
          <div class="stepper">
            <button class="btn btn--icon" onClick={() => change(-1)} disabled={n <= 1} aria-label="Менше порцій">−</button>
            <span class="stepper__value">{portions(n)}</span>
            <button class="btn btn--icon" onClick={() => change(1)} disabled={n >= 50} aria-label="Більше порцій">+</button>
          </div>
        )}
      </div>
      {servings && factor !== 1 && (
        <p class="muted small">
          Рецепт на {portions(servings)} ·{" "}
          <button class="linkbtn" onClick={() => setN(servings)}>повернути</button>
        </p>
      )}
      {!servings && hasQty && (
        <p class="muted small">Вкажи кількість порцій у редагуванні — тоді їх можна буде перерахувати.</p>
      )}
      <ul class="ingredients">
        {items.map((i) => (
          <li>
            <span>{i.name}</span>
            <span class={factor !== 1 && i.qty ? "qty--scaled" : "muted"}>
              {fmtQty(i.qty ? scaleQty(i.qty, i.unit, factor) : i.qty, i.unit)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Dish({ id }: { id: number }) {
  const meta = useMeta();
  const { data: dish, error, setData } = useAsync(() => api.dish(id), [id]);
  const [busy, setBusy] = useState(false);
  const [photo, setPhoto] = useState(0);

  const cook = async () => {
    if (!dish || busy) return;
    setBusy(true);
    try {
      setData(await api.cooked(dish.id));
      haptic.success();
      invalidateMeta();
    } finally {
      setBusy(false);
    }
  };
  useMainButton(dish && dish.status !== "archived" ? "👨‍🍳 Готую!" : null, cook, { progress: busy });

  if (error) return <ErrorBox error={error} />;
  if (!dish || !meta) return <Loader />;

  const tags = tagIndex(meta);
  const rate = async (r: number) => setData(await api.update(dish.id, { rating: r }));
  const facts = [
    dish.cook_time_min ? `⏱ ${dish.cook_time_min} хв` : null,
    dish.difficulty ? `📶 ${DIFFICULTY[dish.difficulty]}` : null,
  ].filter(Boolean);

  return (
    <main class="page page--dish">
      {dish.images.length > 0 && (
        <div class="gallery">
          <img class="gallery__main" src={dish.images[photo]?.url} alt={dish.title} />
          {dish.images.length > 1 && (
            <div class="gallery__thumbs">
              {dish.images.map((img, i) => (
                <button class={"thumb" + (i === photo ? " thumb--on" : "")} onClick={() => setPhoto(i)}
                  aria-label={`Фото ${i + 1}`}>
                  <img src={img.thumb_url ?? img.url} alt="" loading="lazy" />
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <h1>{dish.title}</h1>
      <div class="row row--wrap">
        <span class={`badge badge--${dish.status}`}>{STATUS_LABEL[dish.status]}</span>
        <Stars value={dish.rating ?? null} onChange={rate} />
      </div>
      {dish.description && <p>{dish.description}</p>}

      <div class="facts">
        {dish.cook_ids.length > 0 && <div>👤 Вміє: {cookNames(meta, dish.cook_ids)}</div>}
        <div>🕓 {cookedAgo(dish.last_cooked_at)}{dish.cooked_count ? ` · разів: ${dish.cooked_count}` : ""}</div>
        {facts.length > 0 && <div>{facts.join(" · ")}</div>}
      </div>

      {dish.tag_ids.length > 0 && (
        <div class="chips chips--static">
          {dish.tag_ids.map((t) => tags.get(t)).filter(Boolean).map((t) => (
            <span class={"chip chip--static" + (t!.id === dish.main_ingredient_id ? " chip--main" : "")}>
              {t!.emoji} {t!.label}
            </span>
          ))}
        </div>
      )}

      {dish.source_url && (
        <button class="btn btn--wide" onClick={() => openSource(dish.source_url!)}>
          🔗 Відкрити в {sourceLabel(dish.source_url, dish.source_type)}
          {dish.source_author ? ` · ${dish.source_author}` : ""}
        </button>
      )}

      {dish.ingredients.length > 0 && (
        <Ingredients items={dish.ingredients} servings={dish.servings ?? null} />
      )}

      {dish.steps.length > 0 && (
        <section class="section">
          <h2>Як готувати</h2>
          <ol class="steps">{dish.steps.map((s) => <li>{s}</li>)}</ol>
        </section>
      )}

      {dish.notes && (
        <section class="section">
          <h2>Нотатки</h2>
          <p class="prewrap">{dish.notes}</p>
        </section>
      )}

      {dish.cook_log.length > 0 && (
        <section class="section">
          <h2>Історія</h2>
          <ul class="history">
            {dish.cook_log.map((e) => (
              <li>
                {shortDate(e.cooked_at)} — {e.member_id ? cookNames(meta, [e.member_id]) || "хтось" : "хтось"}
                {e.note ? `: ${e.note}` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div class="actions">
        <button class="btn" onClick={() => navigate(`/dish/${dish.id}/edit`)}>✏️ Редагувати</button>
      </div>
      <FallbackMainButton text="👨‍🍳 Готую!" onClick={cook} busy={busy} />
    </main>
  );
}


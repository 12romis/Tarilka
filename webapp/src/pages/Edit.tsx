import { useEffect, useRef, useState } from "preact/hooks";
import { api, ApiError, type DishFull, type DishIn, type Ingredient } from "../api";
import { Chip, ErrorBox, FallbackMainButton, Loader, Stars } from "../components/ui";
import { KIND_LABEL } from "../format";
import { invalidateMeta, useMainButton, useMeta } from "../hooks";
import { navigate } from "../router";
import { alertMsg, confirm, haptic } from "../tg";

const KINDS = ["meal", "course", "ingredient", "trait", "cuisine"] as const;

type Form = {
  title: string;
  status: "can_cook" | "want_to_try";
  description: string;
  cook_time_min: string;
  difficulty: number | null;
  servings: string;
  rating: number | null;
  notes: string;
  source_url: string;
  steps: string;
  ingredients: Ingredient[];
  tag_ids: number[];
  cook_ids: number[];
  main_ingredient_id: number | null;
};

function toForm(d?: DishFull): Form {
  return {
    title: d?.title ?? "",
    status: d?.status === "want_to_try" ? "want_to_try" : "can_cook",
    description: d?.description ?? "",
    cook_time_min: d?.cook_time_min ? String(d.cook_time_min) : "",
    difficulty: d?.difficulty ?? null,
    servings: d?.servings ? String(d.servings) : "",
    rating: d?.rating ?? null,
    notes: d?.notes ?? "",
    source_url: d?.source_url ?? "",
    steps: (d?.steps ?? []).join("\n"),
    ingredients: d?.ingredients ?? [],
    tag_ids: (d?.tag_ids ?? []).filter((t) => t !== d?.main_ingredient_id),
    cook_ids: d?.cook_ids ?? [],
    main_ingredient_id: d?.main_ingredient_id ?? null,
  };
}

function toBody(f: Form): DishIn {
  const num = (s: string) => (s.trim() ? Number(s) : null);
  return {
    title: f.title.trim(),
    status: f.status,
    description: f.description.trim() || null,
    cook_time_min: num(f.cook_time_min),
    difficulty: f.difficulty,
    servings: num(f.servings),
    rating: f.rating,
    notes: f.notes.trim() || null,
    source_url: f.source_url.trim() || null,
    steps: f.steps.split("\n").map((s) => s.trim()).filter(Boolean),
    ingredients: f.ingredients.filter((i) => i.name.trim()),
    tag_ids: f.tag_ids,
    cook_ids: f.cook_ids,
    main_ingredient_id: f.main_ingredient_id,
  };
}

const toggled = (list: number[], id: number) =>
  list.includes(id) ? list.filter((x) => x !== id) : [...list, id];

export function Edit({ id }: { id?: number }) {
  const meta = useMeta();
  const [dish, setDish] = useState<DishFull | null>(null);
  const [form, setForm] = useState<Form | null>(id ? null : toForm());
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!id) return;
    api.dish(id).then((d) => {
      setDish(d);
      setForm(toForm(d));
    }, setError);
  }, [id]);

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => f && { ...f, [key]: value });

  const save = async () => {
    if (!form || busy) return;
    if (!form.title.trim()) return alertMsg("Потрібна назва страви");
    setBusy(true);
    try {
      const saved = id ? await api.update(id, toBody(form)) : await api.create(toBody(form));
      haptic.success();
      invalidateMeta();
      navigate(`/dish/${saved.id}`, { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const existing = (e.detail as { existing_id?: number })?.existing_id;
        if (existing && (await confirm("Така страва вже є. Відкрити її?"))) navigate(`/dish/${existing}`);
      } else {
        alertMsg(e instanceof ApiError && e.status === 422 ? "Перевір поля: є некоректні значення" : "Не вдалося зберегти");
      }
    } finally {
      setBusy(false);
    }
  };
  useMainButton(form ? "Зберегти" : null, save, { progress: busy });

  if (error) return <ErrorBox error={error} />;
  if (!meta || !form) return <Loader />;

  const photoAction = async (fn: () => Promise<DishFull>) => {
    setBusy(true);
    try {
      setDish(await fn());
    } catch (e) {
      alertMsg(e instanceof ApiError && e.status === 413 ? "Фото більше 10 МБ" : "Не вдалося обробити фото");
    } finally {
      setBusy(false);
    }
  };

  const archive = async () => {
    if (!id || !(await confirm("Прибрати страву в архів? Її не буде в каталозі й пропозиціях."))) return;
    await api.archive(id);
    invalidateMeta();
    navigate("/catalog", { replace: true });
  };

  const ing = (i: number, patch: Partial<Ingredient>) =>
    set("ingredients", form.ingredients.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  return (
    <main class="page">
      <h1>{id ? "✏️ Редагування" : "➕ Нова страва"}</h1>

      <label class="field">
        <span class="label">Назва</span>
        <input class="input" value={form.title} maxLength={200}
          onInput={(e) => set("title", (e.target as HTMLInputElement).value)} />
      </label>

      <div class="field">
        <span class="label">Статус</span>
        <div class="chips">
          <Chip active={form.status === "can_cook"} onClick={() => set("status", "can_cook")}>✅ Вміємо</Chip>
          <Chip active={form.status === "want_to_try"} onClick={() => set("status", "want_to_try")}>🆕 Хочемо спробувати</Chip>
        </div>
      </div>

      <div class="field">
        <span class="label">Хто вміє</span>
        <div class="chips">
          {meta.members.map((m) => (
            <Chip active={form.cook_ids.includes(m.id)} onClick={() => set("cook_ids", toggled(form.cook_ids, m.id))}>
              👤 {m.name}
            </Chip>
          ))}
        </div>
      </div>

      <label class="field">
        <span class="label">Основний продукт</span>
        <select class="select" value={form.main_ingredient_id ?? ""}
          onChange={(e) => {
            const v = (e.target as HTMLSelectElement).value;
            set("main_ingredient_id", v ? Number(v) : null);
          }}>
          <option value="">—</option>
          {meta.tags.ingredient.map((t) => <option value={t.id}>{t.emoji} {t.label}</option>)}
        </select>
      </label>

      {KINDS.map((kind) => (
        <div class="field">
          <span class="label">{kind === "ingredient" ? "Інші продукти" : KIND_LABEL[kind]}</span>
          <div class="chips">
            {meta.tags[kind].filter((t) => t.id !== form.main_ingredient_id).map((t) => (
              <Chip active={form.tag_ids.includes(t.id)} onClick={() => set("tag_ids", toggled(form.tag_ids, t.id))}>
                {t.emoji} {t.label}
              </Chip>
            ))}
          </div>
        </div>
      ))}

      <div class="row row--3">
        <label class="field">
          <span class="label">Час, хв</span>
          <input class="input" inputMode="numeric" value={form.cook_time_min}
            onInput={(e) => set("cook_time_min", (e.target as HTMLInputElement).value.replace(/\D/g, ""))} />
        </label>
        <label class="field">
          <span class="label">Порцій</span>
          <input class="input" inputMode="numeric" value={form.servings}
            onInput={(e) => set("servings", (e.target as HTMLInputElement).value.replace(/\D/g, ""))} />
        </label>
        <label class="field">
          <span class="label">Складність</span>
          <select class="select" value={form.difficulty ?? ""}
            onChange={(e) => {
              const v = (e.target as HTMLSelectElement).value;
              set("difficulty", v ? Number(v) : null);
            }}>
            <option value="">—</option>
            <option value="1">легко</option>
            <option value="2">середньо</option>
            <option value="3">складно</option>
          </select>
        </label>
      </div>

      <div class="field">
        <span class="label">Оцінка</span>
        <Stars value={form.rating} onChange={(v) => set("rating", form.rating === v ? null : v)} />
      </div>

      <label class="field">
        <span class="label">Опис</span>
        <textarea class="input" rows={2} value={form.description}
          onInput={(e) => set("description", (e.target as HTMLTextAreaElement).value)} />
      </label>

      <div class="field">
        <span class="label">Інгредієнти</span>
        {form.ingredients.map((x, i) => (
          <div class="ingrow">
            <input class="input" placeholder="назва" value={x.name}
              onInput={(e) => ing(i, { name: (e.target as HTMLInputElement).value })} />
            <input class="input input--qty" placeholder="к-сть" inputMode="decimal" value={x.qty ?? ""}
              onInput={(e) => {
                const v = (e.target as HTMLInputElement).value.replace(",", ".");
                ing(i, { qty: v === "" || isNaN(Number(v)) ? null : Number(v) });
              }} />
            <input class="input input--unit" placeholder="од." value={x.unit ?? ""}
              onInput={(e) => ing(i, { unit: (e.target as HTMLInputElement).value || null })} />
            <button class="btn btn--icon" aria-label="Прибрати"
              onClick={() => set("ingredients", form.ingredients.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <button class="btn" onClick={() => set("ingredients", [...form.ingredients, { name: "", qty: null, unit: null }])}>
          ➕ Інгредієнт
        </button>
      </div>

      <label class="field">
        <span class="label">Кроки (кожен з нового рядка)</span>
        <textarea class="input" rows={5} value={form.steps}
          onInput={(e) => set("steps", (e.target as HTMLTextAreaElement).value)} />
      </label>

      <label class="field">
        <span class="label">Нотатки</span>
        <textarea class="input" rows={2} value={form.notes}
          onInput={(e) => set("notes", (e.target as HTMLTextAreaElement).value)} />
      </label>

      <label class="field">
        <span class="label">Посилання на рецепт або відео</span>
        <input class="input" type="url" inputMode="url" value={form.source_url}
          onInput={(e) => set("source_url", (e.target as HTMLInputElement).value)} />
      </label>

      {id && dish && (
        <div class="field">
          <span class="label">Фото</span>
          <div class="photos">
            {dish.images.map((img) => (
              <div class={"photo" + (img.is_cover ? " photo--cover" : "")}>
                <img src={img.thumb_url ?? img.url} alt="" loading="lazy" />
                <div class="photo__actions">
                  {!img.is_cover && (
                    <button class="btn btn--icon" aria-label="Зробити обкладинкою"
                      onClick={() => photoAction(() => api.setCover(id, img.id))}>⭐</button>
                  )}
                  <button class="btn btn--icon" aria-label="Видалити фото"
                    onClick={async () => (await confirm("Видалити фото?")) && photoAction(() => api.deleteImage(id, img.id))}>🗑</button>
                </div>
              </div>
            ))}
            <button class="photo photo--add" disabled={busy} onClick={() => fileInput.current?.click()}>
              {busy ? "…" : "📷 Додати"}
            </button>
          </div>
          <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp,image/heic,image/heif" hidden
            onChange={(e) => {
              const file = (e.target as HTMLInputElement).files?.[0];
              (e.target as HTMLInputElement).value = "";
              if (file) photoAction(() => api.upload(id, file));
            }} />
        </div>
      )}
      {!id && <p class="muted small">Фото можна додати після збереження.</p>}

      {id && (
        <button class="btn btn--danger btn--wide" onClick={archive}>🗄 Прибрати в архів</button>
      )}
      <FallbackMainButton text="Зберегти" onClick={save} busy={busy} />
    </main>
  );
}

import { useEffect, useState } from "preact/hooks";
import { api, type CookOption, type Pantry } from "../api";
import { ErrorBox, Loader } from "../components/ui";
import { navigate } from "../router";
import { alertMsg, haptic } from "../tg";

const COOK_SHOWN = 5;

function CookRow({ o, onShop }: { o: CookOption; onShop: (o: CookOption) => void }) {
  const open = (e: Event) => (e.preventDefault(), navigate(`/dish/${o.dish_id}`));
  return (
    <li class="cook">
      <a class="cook__link" href={`/dish/${o.dish_id}`} onClick={open}>
        {o.thumb
          ? <img class="cook__img" src={o.thumb} alt="" loading="lazy" />
          : <span class="cook__img cook__img--empty" aria-hidden="true">🍽</span>}
        <span class="cook__body">
          <span class="cook__title">{o.title}{o.status === "want_to_try" ? " 🆕" : ""}</span>
          {o.missing.length
            ? <span class="cook__miss">бракує: {o.missing.join(", ")}</span>
            : <span class="cook__ok">✓ усе є</span>}
        </span>
      </a>
      {o.missing.length > 0 && (
        <button class="iconbtn" aria-label={`Відсутнє для «${o.title}» — у список покупок`}
          onClick={() => onShop(o)}>🛒</button>
      )}
    </li>
  );
}

export function Fridge() {
  const [data, setData] = useState<Pantry | null>(null);
  const [cook, setCook] = useState<CookOption[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [allCook, setAllCook] = useState(false);

  const loadCook = () => api.cookFromPantry().then(setCook, () => setCook([]));
  const load = () => {
    api.pantry().then(setData, setError);
    loadCook();
  };
  useEffect(() => {
    load();
    const onVisible = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  if (error && !data) return <ErrorBox error={error} />;
  if (!data) return <Loader />;

  const run = async (p: Promise<Pantry>) => {
    try {
      setData(await p);
      loadCook();
      return true;
    } catch {
      alertMsg("Не вдалося — перевір інтернет");
      return false;
    }
  };
  const add = async (e: Event) => {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    if (await run(api.pantryAddText(text.trim()))) {
      haptic.success();
      setText("");
    }
    setBusy(false);
  };
  const remove = (productId: number, toList: boolean) => {
    haptic.select();
    run(api.pantryRemove(productId, toList));
  };
  const shopMissing = async (o: CookOption) => {
    try {
      // уся страва йде в список; те, що вже є вдома, список сам відкладе окремо
      await api.shopAddDish(o.dish_id, null);
      haptic.success();
      alertMsg(`У списку покупок: ${o.missing.join(", ")}`);
    } catch {
      alertMsg("Не вдалося — перевір інтернет");
    }
  };

  const shownCook = cook && (allCook ? cook : cook.slice(0, COOK_SHOWN));

  return (
    <main class="page">
      <h1>🧊 Вдома{data.count ? ` · ${data.count}` : ""}</h1>

      <form class="row" onSubmit={add}>
        <input class="input" value={text} placeholder="яйця, сир, помідори" aria-label="Що є вдома"
          onInput={(e) => setText((e.target as HTMLInputElement).value)} />
        <button class="btn btn--primary" type="submit" disabled={busy || !text.trim()}>
          {busy ? "…" : "Додати"}
        </button>
      </form>

      {data.count === 0 && (
        <p class="hint">
          Познач, що є в холодильнику й шафі, — без кількостей. Покажу, що з цього можна приготувати,
          а в списку покупок відкладу те, що вже є. Куплене зі списку потрапляє сюди саме. Боту можна
          написати: <i>є вдома яйця, сир</i> або <i>закінчилось молоко</i>.
        </p>
      )}

      {data.count > 0 && (
        <section class="section">
          <h2>🍳 Що приготувати</h2>
          {!cook && <p class="muted small">Шукаю страви…</p>}
          {cook && cook.length === 0 && (
            <p class="muted small">Поки нічого не складається — додай ще продуктів.</p>
          )}
          {shownCook && shownCook.length > 0 && (
            <ul class="cooklist">{shownCook.map((o) => <CookRow o={o} onShop={shopMissing} />)}</ul>
          )}
          {cook && cook.length > COOK_SHOWN && (
            <button class="linkbtn" onClick={() => setAllCook(!allCook)}>
              {allCook ? "Згорнути" : `Ще ${cook.length - COOK_SHOWN}`}
            </button>
          )}
        </section>
      )}

      {data.aisles.map((a) => (
        <section class="section">
          <h2>{a.emoji} {a.label}</h2>
          <div class="chips">
            {a.items.map((it) => (
              <span class="chip chip--static pantry">
                {it.name}
                <button class="chip__act" aria-label={`${it.name} закінчилось — у список покупок`}
                  title="Закінчилось — у список" onClick={() => remove(it.product_id, true)}>🛒</button>
                <button class="chip__x" aria-label={`Прибрати ${it.name}`}
                  title="Прибрати" onClick={() => remove(it.product_id, false)}>×</button>
              </span>
            ))}
          </div>
        </section>
      ))}

      {data.count > 0 && (
        <p class="muted small">🛒 — закінчилось, додати в список покупок; × — просто прибрати.</p>
      )}
    </main>
  );
}

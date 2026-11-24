import { useEffect, useState } from "preact/hooks";
import { api, type CookOption, type Pantry, type PantryAisle, type PantryProduct } from "../api";
import { ErrorBox, Loader } from "../components/ui";
import { navigate } from "../router";
import { alertMsg, confirm, haptic } from "../tg";

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

type Act = (p: PantryProduct) => void;

/** Продукт, що є: дотик — «закінчилось». 🛒 — лише позначка, що вже в списку покупок. */
function PresentChip({ p, onOut }: { p: PantryProduct; onOut: Act }) {
  return (
    <button class="chip pchip" onClick={() => onOut(p)} aria-label={`${p.name}: є вдома. Натисни, якщо закінчилось`}>
      {p.name}
      {p.in_list && <span class="pchip__mark" title="уже в списку покупок"> 🛒</span>}
    </button>
  );
}

/** Закінчилось: дотик — «знову є»; «＋ У список» або позначка «у списку»; × — забути. */
function OutChip({ p, onBack, onList, onForget }: { p: PantryProduct; onBack: Act; onList: Act; onForget: Act }) {
  return (
    <span class="chip chip--static pchip pchip--out">
      <button class="pchip__name" onClick={() => onBack(p)} aria-label={`${p.name}: закінчилось. Натисни, якщо знову є`}>
        {p.name}
      </button>
      {p.in_list
        ? <span class="pchip__listed">🛒 у списку</span>
        : <button class="pchip__add" onClick={() => onList(p)} aria-label={`${p.name} — у список покупок`}>＋ У список</button>}
      <button class="chip__x" onClick={() => onForget(p)} aria-label={`Забути ${p.name}`} title="Забути продукт">×</button>
    </span>
  );
}

function Aisles({ aisles, render }: { aisles: PantryAisle[]; render: (p: PantryProduct) => preact.JSX.Element }) {
  return (
    <>
      {aisles.map((a) => (
        <div class="paisle">
          <div class="paisle__label">{a.emoji} {a.label}</div>
          <div class="chips">{a.items.map(render)}</div>
        </div>
      ))}
    </>
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
  const act = (call: (id: number) => Promise<Pantry>) => (p: PantryProduct) => {
    haptic.select();
    run(call(p.product_id));
  };
  const markOut = act(api.pantryOut);
  const markBack = act(api.pantryPresent);
  const toList = (p: PantryProduct) => {
    act(api.pantryToList)(p);
    haptic.success();
  };
  const forget = async (p: PantryProduct) => {
    if (await confirm(`Забути «${p.name}»? Продукт зникне зі сторінки.`)) act(api.pantryForget)(p);
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
      <h1>🧊 Вдома</h1>

      <form class="row" onSubmit={add}>
        <input class="input" value={text} placeholder="яйця, сир, помідори" aria-label="Що є вдома"
          onInput={(e) => setText((e.target as HTMLInputElement).value)} />
        <button class="btn btn--primary" type="submit" disabled={busy || !text.trim()}>
          {busy ? "…" : "Додати"}
        </button>
      </form>

      {data.count === 0 && data.out_count === 0 && (
        <p class="hint">
          Познач, що є в холодильнику й шафі, — без кількостей. Покажу, що з цього можна приготувати,
          а в списку покупок відкладу те, що вже є. Куплене зі списку потрапляє сюди саме. Боту можна
          написати: <i>є вдома яйця, сир</i> або <i>закінчилось молоко</i>.
        </p>
      )}

      {data.count > 0 && (
        <section class="section">
          <h2>✅ Є вдома · {data.count}</h2>
          <p class="muted small">Натисни на продукт, коли він закінчиться.</p>
          <Aisles aisles={data.present} render={(p) => <PresentChip p={p} onOut={markOut} />} />
        </section>
      )}

      {data.out_count > 0 && (
        <section class="section">
          <h2>⭕ Закінчилось · {data.out_count}</h2>
          <p class="muted small">Натисни на назву, якщо знову є; × — забути продукт.</p>
          <Aisles aisles={data.out}
            render={(p) => <OutChip p={p} onBack={markBack} onList={toList} onForget={forget} />} />
        </section>
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
    </main>
  );
}

import { useEffect, useState } from "preact/hooks";
import { api, type Shopping as ShoppingData, type ShoppingLine } from "../api";
import { ErrorBox, Loader } from "../components/ui";
import { fmtQty, portions, shopQty } from "../format";
import { navigate } from "../router";
import { alertMsg, confirm, haptic } from "../tg";

const REFRESH_MS = 15_000; // список спільний: підтягуємо зміни інших членів сім'ї

function amount(line: ShoppingLine): string {
  const parts = line.amounts.map((a) => fmtQty(shopQty(a.qty, a.unit), a.unit));
  return parts.join(" + ");
}

function Line({ line, onToggle }: { line: ShoppingLine; onToggle: (l: ShoppingLine) => void }) {
  return (
    <li class={"shop" + (line.checked ? " shop--done" : "")} onClick={() => onToggle(line)}>
      <span class="shop__box" aria-hidden="true">{line.checked ? "✓" : ""}</span>
      <span class="shop__body">
        <span class="shop__name">{line.name}</span>
        {line.sources.length > 0 && <span class="shop__src">{line.sources.join(", ")}</span>}
      </span>
      <span class="shop__qty">{amount(line)}</span>
    </li>
  );
}

/** Оптимістична відмітка: рядок одразу переїжджає, сервер потім підтверджує. */
function toggled(data: ShoppingData, line: ShoppingLine): ShoppingData {
  const moved = { ...line, checked: !line.checked };
  const drop = (ls: ShoppingLine[]) => ls.filter((l) => l.product_id !== line.product_id);
  return line.checked
    ? { ...data, checked: drop(data.checked), open_count: data.open_count + 1 }
    : { ...data, aisles: data.aisles.map((a) => ({ ...a, lines: drop(a.lines) })).filter((a) => a.lines.length),
        staples: drop(data.staples), at_home: drop(data.at_home), checked: [moved, ...data.checked],
        open_count: data.open_count - (line.at_home ? 0 : 1) };
}

export function Shopping() {
  const [data, setData] = useState<ShoppingData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [showStaples, setShowStaples] = useState(false);
  const [showHome, setShowHome] = useState(false);

  const load = () => api.shopping().then(setData, setError);
  useEffect(() => {
    load();
    const timer = setInterval(() => document.visibilityState === "visible" && load(), REFRESH_MS);
    const onVisible = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  if (error && !data) return <ErrorBox error={error} />;
  if (!data) return <Loader />;

  const run = async (p: Promise<ShoppingData>) => {
    try {
      setData(await p);
    } catch {
      alertMsg("Не вдалося — перевір інтернет");
      load();
    }
  };
  const toggle = (line: ShoppingLine) => {
    haptic.select();
    setData(toggled(data, line));
    run(api.shopCheck(line.product_id, !line.checked));
  };
  const add = async (e: Event) => {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    await run(api.shopAddText(text.trim()));
    haptic.success();
    setText("");
    setBusy(false);
  };
  const clearAll = async () => {
    if (await confirm("Очистити весь список покупок?")) run(api.shopClear(false));
  };

  const empty = !data.aisles.length && !data.staples.length && !data.at_home.length && !data.checked.length;

  return (
    <main class="page">
      <h1>🛒 Покупки{data.open_count ? ` · ${data.open_count}` : ""}</h1>

      <form class="row" onSubmit={add}>
        <input class="input" value={text} placeholder="молоко 2 л, хліб, яйця" aria-label="Що купити"
          onInput={(e) => setText((e.target as HTMLInputElement).value)} />
        <button class="btn btn--primary" type="submit" disabled={busy || !text.trim()}>
          {busy ? "…" : "Додати"}
        </button>
      </form>

      {data.dishes.length > 0 && (
        <div class="chips">
          {data.dishes.map((d) => (
            <span class="chip chip--static">
              <a class="link" href={`/dish/${d.dish_id}`}
                onClick={(e) => (e.preventDefault(), navigate(`/dish/${d.dish_id}`))}>{d.title}</a>
              {d.servings ? <span class="muted"> · {portions(d.servings)}</span> : null}
              <button class="chip__x" aria-label={`Прибрати ${d.title}`}
                onClick={() => run(api.shopRemoveDish(d.dish_id))}>×</button>
            </span>
          ))}
        </div>
      )}

      {empty && (
        <p class="hint">
          Список порожній. Додай страву кнопкою «🛒 У список покупок» на її сторінці, впиши продукти
          вище або напиши боту: <i>купити молоко, хліб</i>.
        </p>
      )}

      {data.aisles.map((a) => (
        <section class="section">
          <h2>{a.emoji} {a.label}</h2>
          <ul class="shoplist">{a.lines.map((l) => <Line line={l} onToggle={toggle} />)}</ul>
        </section>
      ))}

      {data.staples.length > 0 && (
        <section class="section">
          <button class="section__toggle" onClick={() => setShowStaples(!showStaples)}>
            🧂 Мабуть, є вдома ({data.staples.length}) {showStaples ? "▾" : "▸"}
          </button>
          {showStaples
            ? <ul class="shoplist">{data.staples.map((l) => <Line line={l} onToggle={toggle} />)}</ul>
            : <p class="muted small">{data.staples.map((l) => l.name).join(", ")}</p>}
        </section>
      )}

      {data.at_home.length > 0 && (
        <section class="section">
          <button class="section__toggle" onClick={() => setShowHome(!showHome)}>
            🧊 Уже є вдома ({data.at_home.length}) {showHome ? "▾" : "▸"}
          </button>
          {showHome
            ? <ul class="shoplist">{data.at_home.map((l) => <Line line={l} onToggle={toggle} />)}</ul>
            : <p class="muted small">{data.at_home.map((l) => l.name).join(", ")}</p>}
        </section>
      )}

      {data.checked.length > 0 && (
        <section class="section">
          <div class="section__head">
            <h2>✓ Куплено ({data.checked.length})</h2>
            <button class="linkbtn" onClick={() => run(api.shopClear(true))}>Прибрати</button>
          </div>
          <p class="muted small">Куплене вже в холодильнику 🧊</p>
          <ul class="shoplist">{data.checked.map((l) => <Line line={l} onToggle={toggle} />)}</ul>
        </section>
      )}

      {!empty && (
        <div class="actions">
          <button class="btn btn--danger" onClick={clearAll}>Очистити весь список</button>
        </div>
      )}
    </main>
  );
}

import { useEffect, useRef, useState } from "preact/hooks";
import { api, type DishCard, type Meta, type SuggestIn } from "../api";
import { Cover, ErrorBox, Loader } from "../components/ui";
import { cookNames, cookedAgo } from "../format";
import { useMeta } from "../hooks";
import { navigate } from "../router";
import { alertMsg, haptic } from "../tg";

function filtersFrom(q: URLSearchParams): SuggestIn {
  return {
    meal: q.getAll("meal"),
    cook: q.getAll("cook").map(Number),
    quick: q.get("quick") === "1",
    include_new: q.get("new") === "1",
    at_home: q.get("home") === "1",
    exclude_ids: [],
  };
}

/** Картка пропозиції: свайп уліво — «не сьогодні». */
function SuggestCard(props: { dish: DishCard; meta: Meta; onSkip: () => void; onCook: () => void }) {
  const { dish, meta } = props;
  const start = useRef<number | null>(null);
  const [dx, setDx] = useState(0);
  const end = () => {
    if (dx < -90) props.onSkip();
    setDx(0);
    start.current = null;
  };
  return (
    <article
      class="scard"
      style={{ transform: `translateX(${dx}px) rotate(${dx / 40}deg)`, opacity: 1 + dx / 400 }}
      onTouchStart={(e) => (start.current = e.touches[0].clientX)}
      onTouchMove={(e) => start.current !== null && setDx(Math.min(0, e.touches[0].clientX - start.current))}
      onTouchEnd={end}
    >
      <div class="scard__img" onClick={() => navigate(`/dish/${dish.id}`)}>
        <Cover dish={dish} meta={meta} big />
      </div>
      <div class="scard__body" onClick={() => navigate(`/dish/${dish.id}`)}>
        <h2>{dish.title}</h2>
        <div class="muted">
          {[
            dish.cook_ids.length ? `👤 ${cookNames(meta, dish.cook_ids)}` : null,
            dish.cook_time_min ? `⏱ ${dish.cook_time_min} хв` : null,
            dish.status === "want_to_try" ? "🆕 нове" : cookedAgo(dish.last_cooked_at),
          ].filter(Boolean).join(" · ")}
        </div>
      </div>
      <div class="scard__actions">
        <button class="btn" onClick={props.onSkip} aria-label="Не сьогодні">✕ Не сьогодні</button>
        <button class="btn btn--primary" onClick={props.onCook}>👨‍🍳 Готую!</button>
      </div>
    </article>
  );
}

export function Suggest({ query }: { query: URLSearchParams }) {
  const meta = useMeta();
  const base = filtersFrom(query);
  const seen = useRef<Set<number>>(new Set());
  const [cards, setCards] = useState<DishCard[] | null>(null);
  const [info, setInfo] = useState({ total_candidates: 0, total_dishes: 0 });
  const [error, setError] = useState<unknown>(null);

  const fetchMore = async (keep: DishCard[], count: number) => {
    const res = await api.suggest({ ...base, exclude_ids: [...seen.current] });
    setInfo(res);
    const fresh = res.dishes.filter((d) => !keep.some((k) => k.id === d.id)).slice(0, count);
    fresh.forEach((d) => seen.current.add(d.id));
    return [...keep, ...fresh];
  };

  const reroll = () => {
    haptic.tap();
    setCards(null);
    fetchMore([], 3).then(setCards, setError);
  };

  useEffect(() => {
    seen.current = new Set();
    fetchMore([], 3).then(setCards, setError);
  }, [query.toString()]);

  if (error) return <ErrorBox error={error} />;
  if (!meta || !cards) return <Loader text="Підбираю…" />;

  const skip = (id: number) => {
    haptic.tap();
    const rest = cards.filter((c) => c.id !== id);
    setCards(rest);
    fetchMore(rest, 1).then(setCards, setError);
  };
  const cook = async (d: DishCard) => {
    await api.cooked(d.id).catch(setError);
    haptic.success();
    alertMsg(`Смачного! «${d.title}» записано як приготоване 👨‍🍳`);
    navigate(`/dish/${d.id}`);
  };

  return (
    <main class="page">
      <h1>🎲 Що приготувати</h1>
      {cards.length === 0 && !base.at_home && <p class="muted">Під ці фільтри нічого немає 🤷</p>}
      {cards.length === 0 && base.at_home && (
        <p class="hint">
          🧊 Немає страв, для яких усе є вдома (з урахуванням інших фільтрів). Познач, що є в{" "}
          <a class="link" href="/fridge" onClick={(e) => (e.preventDefault(), navigate("/fridge"))}>холодильнику</a>
          {" "}— там же видно страви, яким бракує 1–3 продуктів.
        </p>
      )}
      {cards.map((d) => (
        <SuggestCard key={d.id} dish={d} meta={meta} onSkip={() => skip(d.id)} onCook={() => cook(d)} />
      ))}
      {cards.length > 0 && cards.length < 3 && info.total_candidates < 3 && (
        <p class="muted">Під фільтри підходить лише {info.total_candidates}. Спробуй послабити фільтри.</p>
      )}
      {info.total_dishes < 10 && (
        <p class="hint">💡 Страв поки мало. Надішли боту список того, що вмієте, через кому — наприклад: «борщ, деруни, олів'є — вміє мама».</p>
      )}
      <div class="actions">
        <button class="btn btn--primary" onClick={reroll}>🎲 Інші 3</button>
        {(base.meal.length > 0 || base.cook.length > 0 || base.quick || base.at_home || !base.include_new) && (
          <button class="btn" onClick={() => navigate("/suggest?new=1", { replace: true })}>↺ Без фільтрів</button>
        )}
      </div>
    </main>
  );
}

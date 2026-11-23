import { useState } from "preact/hooks";
import { api, type Meta } from "../api";
import { Chip, DishTile, Loader } from "../components/ui";
import { mealByTime } from "../format";
import { useAsync, useMe, useMeta } from "../hooks";
import { navigate } from "../router";
import { haptic } from "../tg";

const MEALS: [string, string][] = [
  ["breakfast", "🌅 Сніданок"],
  ["lunch", "☀️ Обід"],
  ["dinner", "🌙 Вечеря"],
  ["any", "Будь-що"],
];

function Strip({ title, query, meta, more }: { title: string; query: string; meta: Meta; more: string }) {
  const { data } = useAsync(() => api.dishes(new URLSearchParams(query)), [query]);
  if (!data || data.items.length === 0) return null;
  return (
    <section class="section">
      <div class="section__head">
        <h2>{title}</h2>
        <a class="link" href={more} onClick={(e) => (e.preventDefault(), navigate(more))}>усі →</a>
      </div>
      <div class="strip">
        {data.items.map((d) => <DishTile key={d.id} dish={d} meta={meta} />)}
      </div>
    </section>
  );
}

export function Home() {
  const meta = useMeta();
  const me = useMe();
  const [meal, setMeal] = useState(mealByTime());
  const [cook, setCook] = useState<number[]>([]);
  const [quick, setQuick] = useState(false);
  const [withNew, setWithNew] = useState(false);

  if (!meta) return <Loader />;

  const go = () => {
    haptic.tap();
    const q = new URLSearchParams();
    if (meal !== "any") q.set("meal", meal);
    cook.forEach((c) => q.append("cook", String(c)));
    if (quick) q.set("quick", "1");
    if (withNew) q.set("new", "1");
    navigate(`/suggest?${q}`);
  };

  return (
    <main class="page">
      <header class="hero">
        <h1>Тарілка 🍽</h1>
        <div class="muted">{meta.total_dishes} страв у сімейному каталозі</div>
      </header>

      <div class="chips" role="group" aria-label="Прийом їжі">
        {MEALS.map(([slug, label]) => (
          <Chip key={slug} active={meal === slug} onClick={() => setMeal(slug)}>{label}</Chip>
        ))}
      </div>
      <div class="chips" role="group" aria-label="Хто готує і особливості">
        {meta.members.map((m) => (
          <Chip key={m.id} active={cook.includes(m.id)}
            onClick={() => setCook(cook.includes(m.id) ? cook.filter((c) => c !== m.id) : [...cook, m.id])}>
            👤 {m.name}
          </Chip>
        ))}
        <Chip active={quick} onClick={() => setQuick(!quick)}>⚡ Швидко</Chip>
        <Chip active={withNew} onClick={() => setWithNew(!withNew)}>🆕 З новим</Chip>
      </div>

      <button class="bigbutton" onClick={go}>🎲 Що приготувати?</button>

      <Strip title="Давно не готували" query="sort=stale&status=can_cook&limit=8" meta={meta}
        more="/catalog?sort=stale&status=can_cook" />
      <Strip title="Нещодавно додані" query="sort=new&limit=8" meta={meta} more="/catalog?sort=new" />

      <nav class="actions">
        <button class="btn" onClick={() => navigate("/catalog")}>📖 Каталог</button>
        <button class="btn" onClick={() => navigate("/new")}>➕ Додати</button>
        <button class="btn" onClick={() => navigate("/app")} aria-label="Застосунок">📲</button>
        {me?.is_admin && <button class="btn" onClick={() => navigate("/settings")}>⚙️</button>}
      </nav>
    </main>
  );
}

import { useEffect, useState } from "preact/hooks";
import { api, type DishCard } from "../api";
import { Chip, DishTile, ErrorBox, Loader } from "../components/ui";
import { KIND_LABEL, STATUS_LABEL } from "../format";
import { useMeta } from "../hooks";
import { navigate } from "../router";

const SORTS: [string, string][] = [
  ["stale", "Давно не готували"],
  ["new", "Нові"],
  ["rating", "За оцінкою"],
  ["title", "За абеткою"],
];
const TIMES = [15, 30, 60];
const PAGE = 30;
const KINDS = ["meal", "course", "ingredient", "trait", "cuisine"] as const;

/** Фільтри живуть у query рядка адреси: їх видно, ними можна поділитися, «Назад» працює. */
function setQuery(q: URLSearchParams) {
  navigate(`/catalog?${q}`, { replace: true });
}

function toggle(q: URLSearchParams, key: string, value: string): URLSearchParams {
  const next = new URLSearchParams(q);
  const values = next.getAll(key);
  next.delete(key);
  (values.includes(value) ? values.filter((v) => v !== value) : [...values, value])
    .forEach((v) => next.append(key, v));
  return next;
}

export function Catalog({ query }: { query: URLSearchParams }) {
  const meta = useMeta();
  const [items, setItems] = useState<DishCard[] | null>(null);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState(query.get("q") ?? "");
  const qs = query.toString();

  useEffect(() => {
    const t = setTimeout(() => {
      if ((query.get("q") ?? "") === search) return;
      const next = new URLSearchParams(query);
      search ? next.set("q", search) : next.delete("q");
      setQuery(next);
    }, 350);
    return () => clearTimeout(t);
  }, [search]);

  const load = (from: number, keep: DishCard[]) => {
    const q = new URLSearchParams(query);
    q.set("limit", String(PAGE));
    q.set("cursor", String(from));
    api.dishes(q).then((page) => {
      setItems([...keep, ...page.items]);
      setTotal(page.total);
      setCursor(page.next_cursor ?? null);
    }, setError);
  };

  useEffect(() => {
    setItems(null);
    load(0, []);
  }, [qs]);

  if (error) return <ErrorBox error={error} />;
  if (!meta) return <Loader />;

  const active = [...query.keys()].filter((k) => !["q", "sort"].includes(k)).length;
  const sort = query.get("sort") ?? "stale";

  return (
    <main class="page">
      <h1>📖 Каталог</h1>
      <input class="input" type="search" placeholder="Пошук за назвою" value={search}
        onInput={(e) => setSearch((e.target as HTMLInputElement).value)} />
      <div class="row">
        <button class="btn" onClick={() => setOpen(!open)} aria-expanded={open}>
          ⚙️ Фільтри{active ? ` (${active})` : ""}
        </button>
        <select class="select" value={sort} aria-label="Сортування"
          onChange={(e) => {
            const next = new URLSearchParams(query);
            next.set("sort", (e.target as HTMLSelectElement).value);
            setQuery(next);
          }}>
          {SORTS.map(([v, l]) => <option value={v}>{l}</option>)}
        </select>
      </div>

      {open && (
        <div class="filters">
          <div class="filters__group">
            <div class="label">Статус</div>
            <div class="chips">
              {Object.entries(STATUS_LABEL).map(([v, l]) => (
                <Chip active={query.getAll("status").includes(v)} onClick={() => setQuery(toggle(query, "status", v))}>{l}</Chip>
              ))}
            </div>
          </div>
          {KINDS.map((kind) => (
            <div class="filters__group">
              <div class="label">{KIND_LABEL[kind]}</div>
              <div class="chips">
                {meta.tags[kind].filter((t) => t.count > 0 || query.getAll(kind).includes(t.slug)).map((t) => (
                  <Chip active={query.getAll(kind).includes(t.slug)} onClick={() => setQuery(toggle(query, kind, t.slug))}>
                    {t.emoji} {t.label} <span class="muted">{t.count}</span>
                  </Chip>
                ))}
              </div>
            </div>
          ))}
          <div class="filters__group">
            <div class="label">Хто вміє</div>
            <div class="chips">
              {meta.members.map((m) => (
                <Chip active={query.getAll("cook").includes(String(m.id))} onClick={() => setQuery(toggle(query, "cook", String(m.id)))}>
                  👤 {m.name}
                </Chip>
              ))}
            </div>
          </div>
          <div class="filters__group">
            <div class="label">Час</div>
            <div class="chips">
              {TIMES.map((t) => (
                <Chip active={query.get("max_time") === String(t)} onClick={() => {
                  const next = new URLSearchParams(query);
                  next.get("max_time") === String(t) ? next.delete("max_time") : next.set("max_time", String(t));
                  setQuery(next);
                }}>≤ {t} хв</Chip>
              ))}
            </div>
          </div>
          {active > 0 && (
            <button class="btn" onClick={() => setQuery(new URLSearchParams(sort !== "stale" ? { sort } : {}))}>
              ↺ Скинути фільтри
            </button>
          )}
        </div>
      )}

      {items === null ? <Loader /> : (
        <>
          <div class="muted small">Знайдено: {total}</div>
          {items.length === 0 && <p class="muted center pad">Нічого не знайшлося 🤷</p>}
          <div class="grid">
            {items.map((d) => <DishTile key={d.id} dish={d} meta={meta} />)}
          </div>
          {cursor !== null && (
            <button class="btn btn--wide" onClick={() => load(cursor, items)}>Показати ще</button>
          )}
        </>
      )}
    </main>
  );
}

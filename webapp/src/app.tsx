import { useEffect } from "preact/hooks";
import { useBackButton } from "./hooks";
import { Catalog } from "./pages/Catalog";
import { Dish } from "./pages/Dish";
import { Edit } from "./pages/Edit";
import { Home } from "./pages/Home";
import { Settings } from "./pages/Settings";
import { Suggest } from "./pages/Suggest";
import { navigate, useLocation } from "./router";
import { tg } from "./tg";

export function App() {
  const { route, query, path } = useLocation();
  useBackButton(path);

  // t.me/<bot>/app?startapp=dish_5 → одразу сторінка страви
  useEffect(() => {
    const m = tg?.initDataUnsafe.start_param?.match(/^dish_(\d+)$/);
    if (m && location.pathname === "/") navigate(`/dish/${m[1]}`, { replace: true });
  }, []);

  if (!tg && !import.meta.env.DEV) {
    return (
      <main class="page center">
        <h1>Тарілка 🍽</h1>
        <p class="muted">Відкрий вітрину з Telegram — кнопка «🍽 Страви» в боті.</p>
      </main>
    );
  }

  switch (route.name) {
    case "suggest": return <Suggest query={query} />;
    case "catalog": return <Catalog query={query} />;
    case "dish": return <Dish key={route.id} id={route.id} />;
    case "edit": return <Edit key={route.id} id={route.id} />;
    case "new": return <Edit />;
    case "settings": return <Settings />;
    default: return <Home />;
  }
}

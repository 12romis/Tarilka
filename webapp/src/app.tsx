import { useEffect, useState } from "preact/hooks";
import { api, ApiError, onAuthRequired } from "./api";
import { Loader } from "./components/ui";
import { useBackButton } from "./hooks";
import { AppPage } from "./pages/AppPage";
import { Catalog } from "./pages/Catalog";
import { Dish } from "./pages/Dish";
import { Edit } from "./pages/Edit";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { Settings } from "./pages/Settings";
import { Shopping } from "./pages/Shopping";
import { Suggest } from "./pages/Suggest";
import { atRoot, navigate, useLocation } from "./router";
import { tg } from "./tg";

type Auth = "ok" | "checking" | "login";

/** Поза Telegram: перевіряємо cookie-сесію; немає — сторінка входу. */
function useBrowserAuth(): Auth {
  const [auth, setAuth] = useState<Auth>(tg || import.meta.env.DEV ? "ok" : "checking");
  useEffect(() => {
    if (auth !== "checking") return;
    api.me().then(
      () => setAuth("ok"),
      // офлайн — не причина викидати на вхід: сторінки покажуть кеш або помилку
      (e) => setAuth(e instanceof ApiError && e.status === 401 ? "login" : "ok"),
    );
  }, []);
  useEffect(() => onAuthRequired(() => setAuth("login")), []);
  return auth;
}

/** У Telegram «Назад» — системна кнопка; у браузері й PWA — своя. */
function TopBar() {
  if (tg || atRoot()) return null;
  const back = () => (history.length > 1 ? history.back() : navigate("/"));
  return (
    <header class="topbar">
      <button class="topbar__back" onClick={back}>‹ Назад</button>
    </header>
  );
}

export function App() {
  const { route, query, path } = useLocation();
  useBackButton(path);
  const auth = useBrowserAuth();

  // t.me/<bot>/app?startapp=dish_5 → одразу сторінка страви
  useEffect(() => {
    const m = tg?.initDataUnsafe.start_param?.match(/^dish_(\d+)$/);
    if (m && location.pathname === "/") navigate(`/dish/${m[1]}`, { replace: true });
  }, []);

  if (route.name === "login" && !tg) return <Login token={query.get("t")} />;
  if (auth === "checking") return <Loader />;
  if (auth === "login") return <Login token={null} />;

  return (
    <>
      <TopBar />
      {page(route, query)}
    </>
  );
}

function page(route: ReturnType<typeof useLocation>["route"], query: URLSearchParams) {
  switch (route.name) {
    case "suggest": return <Suggest query={query} />;
    case "catalog": return <Catalog query={query} />;
    case "dish": return <Dish key={route.id} id={route.id} />;
    case "edit": return <Edit key={route.id} id={route.id} />;
    case "new": return <Edit />;
    case "settings": return <Settings />;
    case "app": return <AppPage />;
    case "shopping": return <Shopping />;
    default: return <Home />;
  }
}

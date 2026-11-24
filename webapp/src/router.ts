// Мінімальний роутер на History API: шлях + query; без залежностей.
import { useEffect, useState } from "preact/hooks";

export type Route =
  | { name: "home" }
  | { name: "suggest" }
  | { name: "catalog" }
  | { name: "dish"; id: number }
  | { name: "edit"; id: number }
  | { name: "new" }
  | { name: "settings" }
  | { name: "app" }
  | { name: "shopping" }
  | { name: "fridge" }
  | { name: "login" };

export function parse(pathname: string): Route {
  const m = pathname.match(/^\/dish\/(\d+)(\/edit)?\/?$/);
  if (m) return m[2] ? { name: "edit", id: +m[1] } : { name: "dish", id: +m[1] };
  switch (pathname.replace(/\/$/, "")) {
    case "/suggest": return { name: "suggest" };
    case "/catalog": return { name: "catalog" };
    case "/new": return { name: "new" };
    case "/settings": return { name: "settings" };
    case "/app": return { name: "app" };
    case "/shopping": return { name: "shopping" };
    case "/fridge": return { name: "fridge" };
    case "/login": return { name: "login" };
    default: return { name: "home" };
  }
}

const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());
window.addEventListener("popstate", notify);

export function navigate(to: string, { replace = false } = {}): void {
  if (replace) history.replaceState(null, "", to);
  else history.pushState(null, "", to);
  window.scrollTo(0, 0);
  notify();
}

export function useLocation(): { route: Route; query: URLSearchParams; path: string } {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    listeners.add(l);
    return () => void listeners.delete(l);
  }, []);
  return {
    route: parse(location.pathname),
    query: new URLSearchParams(location.search),
    path: location.pathname + location.search,
  };
}

export const atRoot = () => parse(location.pathname).name === "home";

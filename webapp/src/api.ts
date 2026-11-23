import type { components } from "./api-types";
import { tg } from "./tg";

export type Meta = components["schemas"]["MetaOut"];
export type Tag = components["schemas"]["TagOut"];
export type Member = components["schemas"]["MemberOut"];
export type DishCard = components["schemas"]["DishCard"];
export type DishFull = components["schemas"]["DishFull"];
export type DishPage = components["schemas"]["DishPage"];
export type DishIn = components["schemas"]["DishIn"];
export type SuggestIn = components["schemas"]["SuggestIn"];
export type SuggestOut = components["schemas"]["SuggestOut"];
export type Me = components["schemas"]["MeOut"];
export type Ingredient = components["schemas"]["IngredientIO"];
export type LoginLink = components["schemas"]["LinkOut"];
export type WebSession = components["schemas"]["SessionOut"];

// Поза Telegram сесія живе в cookie; 401 означає, що треба увійти (або сесію завершили).
const authListeners = new Set<() => void>();
export function onAuthRequired(cb: () => void): () => void {
  authListeners.add(cb);
  return () => void authListeners.delete(cb);
}

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (tg) headers.Authorization = `tma ${tg.initData}`;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (res.status === 401 && !tg && !path.startsWith("/api/auth/login")) {
    authListeners.forEach((l) => l());
  }
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? null);
  return data as T;
}

export const api = {
  me: () => request<Me>("GET", "/api/me"),
  meta: () => request<Meta>("GET", "/api/meta"),
  dishes: (query: URLSearchParams) => request<DishPage>("GET", `/api/dishes?${query}`),
  dish: (id: number) => request<DishFull>("GET", `/api/dishes/${id}`),
  create: (body: DishIn) => request<DishFull>("POST", "/api/dishes", body),
  update: (id: number, body: DishIn) => request<DishFull>("PATCH", `/api/dishes/${id}`, body),
  archive: (id: number) => request<void>("DELETE", `/api/dishes/${id}`),
  cooked: (id: number) => request<DishFull>("POST", `/api/dishes/${id}/cooked`, {}),
  suggest: (body: SuggestIn) => request<SuggestOut>("POST", "/api/suggest", body),
  upload: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<DishFull>("POST", `/api/dishes/${id}/images`, fd);
  },
  setCover: (id: number, imageId: number) =>
    request<DishFull>("PATCH", `/api/dishes/${id}/images/${imageId}`, { is_cover: true }),
  deleteImage: (id: number, imageId: number) =>
    request<DishFull>("DELETE", `/api/dishes/${id}/images/${imageId}`),
  addMember: (name: string, aliases: string[]) =>
    request<Member>("POST", "/api/members", { name, aliases }),
  updateMember: (id: number, body: { name?: string; aliases?: string[] }) =>
    request<Member>("PATCH", `/api/members/${id}`, body),
  webLink: () => request<LoginLink>("POST", "/api/auth/link", {}),
  login: (body: { token?: string; code?: string }) => request<void>("POST", "/api/auth/login", body),
  logout: () => request<void>("POST", "/api/auth/logout", {}),
  sessions: () => request<WebSession[]>("GET", "/api/auth/sessions"),
  revokeSession: (id: number) => request<void>("DELETE", `/api/auth/sessions/${id}`),
};

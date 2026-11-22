import { useEffect, useRef, useState } from "preact/hooks";
import { api, type Me, type Meta } from "./api";
import { atRoot } from "./router";
import { tg } from "./tg";

/**
 * Головна кнопка Telegram. Поза Telegram повертає false — тоді сторінка малює власну кнопку.
 * text=null ховає кнопку.
 */
export function useMainButton(
  text: string | null,
  onClick: () => void,
  { progress = false, disabled = false } = {},
): boolean {
  const handler = useRef(onClick);
  handler.current = onClick;
  useEffect(() => {
    if (!tg) return;
    const mb = tg.MainButton;
    const cb = () => handler.current();
    if (text === null) {
      mb.hide();
      return;
    }
    mb.setText(text);
    mb.onClick(cb);
    mb.show();
    return () => {
      mb.offClick(cb);
      mb.hide();
    };
  }, [text]);
  useEffect(() => {
    if (!tg || text === null) return;
    progress ? tg.MainButton.showProgress(false) : tg.MainButton.hideProgress();
    disabled ? tg.MainButton.disable() : tg.MainButton.enable();
  }, [progress, disabled, text]);
  return Boolean(tg);
}

/** BackButton Telegram: видно всюди, крім головної; повертає назад або на головну. */
export function useBackButton(path: string): void {
  useEffect(() => {
    if (!tg) return;
    const back = () => (history.length > 1 ? history.back() : location.assign("/"));
    if (atRoot()) tg.BackButton.hide();
    else tg.BackButton.show();
    tg.BackButton.onClick(back);
    return () => tg!.BackButton.offClick(back);
  }, [path]);
}

// Довідники кешуються на весь сеанс; invalidateMeta() — після змін, що впливають на лічильники.
let metaPromise: Promise<Meta> | null = null;
let mePromise: Promise<Me> | null = null;
const metaListeners = new Set<(m: Meta) => void>();

export function loadMeta(): Promise<Meta> {
  metaPromise ??= api.meta();
  return metaPromise;
}

export function invalidateMeta(): void {
  metaPromise = null;
  loadMeta().then((m) => metaListeners.forEach((l) => l(m)));
}

export function useMeta(): Meta | null {
  const [meta, setMeta] = useState<Meta | null>(null);
  useEffect(() => {
    let alive = true;
    loadMeta().then((m) => alive && setMeta(m));
    metaListeners.add(setMeta);
    return () => {
      alive = false;
      metaListeners.delete(setMeta);
    };
  }, []);
  return meta;
}

export function useMe(): Me | null {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    mePromise ??= api.me();
    mePromise.then(setMe).catch(() => setMe(null));
  }, []);
  return me;
}

/** Асинхронне завантаження з повторним запуском при зміні deps. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data: T | null; error: unknown; loading: boolean }>({
    data: null, error: null, loading: true,
  });
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    fn().then(
      (data) => alive && setState({ data, error: null, loading: false }),
      (error) => alive && setState({ data: null, error, loading: false }),
    );
    return () => void (alive = false);
  }, deps);
  return { ...state, setData: (data: T) => setState({ data, error: null, loading: false }) };
}

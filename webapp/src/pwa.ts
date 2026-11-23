// PWA поза Telegram: service worker (офлайн-кеш) і системний запит «Встановити застосунок».
import { useEffect, useState } from "preact/hooks";
import { tg } from "./tg";

interface InstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferred: InstallPromptEvent | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

export function initPwa(): void {
  // У Telegram вебв'ю свій кеш і своя кнопка меню — service worker там не потрібен.
  if (tg || import.meta.env.DEV || !("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e as InstallPromptEvent;
    notify();
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    notify();
  });
}

/** Функція, що показує системний діалог встановлення, або null (iOS, вже встановлено…). */
export function useInstallPrompt(): (() => Promise<void>) | null {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    listeners.add(l);
    return () => void listeners.delete(l);
  }, []);
  if (!deferred) return null;
  return async () => {
    await deferred!.prompt();
    await deferred!.userChoice;
    deferred = null;
    notify();
  };
}

/** Після виходу дані сім'ї не мають лишатися в офлайн-кеші пристрою. */
export async function clearOfflineCache(): Promise<void> {
  if (!("caches" in window)) return;
  const keys = await caches.keys();
  await Promise.all(keys.filter((k) => k.startsWith("tarilka-")).map((k) => caches.delete(k)));
}

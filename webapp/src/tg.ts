// Тонка обгортка над window.Telegram.WebApp (скрипт telegram-web-app.js у index.html).
// Поза Telegram (звичайний браузер) усі виклики безпечно нічого не роблять.

type Haptic = "success" | "warning" | "error";

interface TgButton {
  setText(t: string): void;
  show(): void;
  hide(): void;
  enable(): void;
  disable(): void;
  showProgress(leaveActive?: boolean): void;
  hideProgress(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface TgWebApp {
  initData: string;
  initDataUnsafe: { start_param?: string; user?: { id: number; first_name: string } };
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  openLink(url: string): void;
  openTelegramLink(url: string): void;
  showConfirm(message: string, cb: (ok: boolean) => void): void;
  showAlert(message: string, cb?: () => void): void;
  MainButton: TgButton;
  BackButton: { show(): void; hide(): void; onClick(cb: () => void): void; offClick(cb: () => void): void };
  HapticFeedback: { notificationOccurred(t: Haptic): void; impactOccurred(s: "light" | "medium"): void; selectionChanged(): void };
}

declare global {
  interface Window {
    Telegram?: { WebApp: TgWebApp };
  }
}

export const tg: TgWebApp | undefined = window.Telegram?.WebApp?.initData
  ? window.Telegram.WebApp
  : undefined;

/** Відкрито як встановлений застосунок (PWA з головного екрана). */
export const isStandalone = (): boolean =>
  window.matchMedia("(display-mode: standalone)").matches ||
  (navigator as Navigator & { standalone?: boolean }).standalone === true;

export const isIOS = (): boolean =>
  /iPhone|iPad|iPod/.test(navigator.userAgent) ||
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

export const BOT_URL = "https://t.me/tarilkaa_bot";

export function initTelegram(): void {
  tg?.ready();
  tg?.expand();
}

export const haptic = {
  success: () => tg?.HapticFeedback.notificationOccurred("success"),
  tap: () => tg?.HapticFeedback.impactOccurred("light"),
  select: () => tg?.HapticFeedback.selectionChanged(),
};

export function openSource(url: string): void {
  if (!tg) return void window.open(url, "_blank", "noopener");
  if (/^https:\/\/t\.me\//.test(url)) tg.openTelegramLink(url);
  else tg.openLink(url);
}

export function confirm(message: string): Promise<boolean> {
  if (!tg) return Promise.resolve(window.confirm(message));
  return new Promise((resolve) => tg!.showConfirm(message, resolve));
}

export function alertMsg(message: string): void {
  if (tg) tg.showAlert(message);
  else window.alert(message);
}

import { useState } from "preact/hooks";
import { api, type LoginLink, type WebSession } from "../api";
import { ErrorBox, Loader } from "../components/ui";
import { shortDate } from "../format";
import { useAsync } from "../hooks";
import { clearOfflineCache, useInstallPrompt } from "../pwa";
import { confirm, haptic, isIOS, isStandalone, tg } from "../tg";

/** «iPhone · Safari» з User-Agent — щоб упізнати свій пристрій у списку. */
export function deviceLabel(ua: string | null | undefined): string {
  if (!ua) return "Невідомий пристрій";
  const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android"
    : /Windows/.test(ua) ? "Windows" : /Mac OS X/.test(ua) ? "Mac" : /Linux/.test(ua) ? "Linux" : "";
  const browser = /EdgA?\//.test(ua) ? "Edge" : /SamsungBrowser/.test(ua) ? "Samsung Internet"
    : /Firefox|FxiOS/.test(ua) ? "Firefox" : /Chrome|CriOS/.test(ua) ? "Chrome"
      : /Safari/.test(ua) ? "Safari" : "";
  return [os, browser].filter(Boolean).join(" · ") || "Браузер";
}

function OpenInBrowser() {
  const [link, setLink] = useState<LoginLink | null>(null);
  const [busy, setBusy] = useState(false);
  const open = async () => {
    setBusy(true);
    try {
      const l = await api.webLink();
      setLink(l);
      tg!.openLink(l.url);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section class="section">
      <p>
        Тарілку можна поставити на головний екран телефона чи комп'ютера: вона відкриватиметься
        без Telegram, а переглянуті рецепти будуть доступні й без інтернету.
      </p>
      <button class="btn btn--primary btn--wide" onClick={open} disabled={busy}>
        🌐 Відкрити в браузері
      </button>
      {link && (
        <p class="hint">
          У браузері вибери «Встановити застосунок» або «Додати на головний екран». Якщо
          застосунок попросить код — <b class="code">{link.code}</b> (діє 15 хв).
        </p>
      )}
    </section>
  );
}

function Install() {
  const install = useInstallPrompt();
  if (isStandalone()) return <p class="hint">✓ Тарілка встановлена на цей пристрій.</p>;
  return (
    <section class="section">
      {install ? (
        <button class="btn btn--primary btn--wide" onClick={install}>📲 Встановити застосунок</button>
      ) : isIOS() ? (
        <p class="hint">
          Щоб поставити на головний екран: у Safari натисни «Поділитися» (квадрат зі стрілкою вгору) →
          «На початковий екран». Якщо застосунок попросить код — надішли боту /web.
        </p>
      ) : (
        <p class="hint">
          Щоб поставити на головний екран: меню браузера <b>⋮</b> → «Встановити застосунок» або
          «Додати на головний екран».
        </p>
      )}
    </section>
  );
}

function Sessions() {
  const { data, error, setData } = useAsync(() => api.sessions(), []);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loader />;

  const end = async (s: WebSession) => {
    if (!(await confirm(`Завершити сесію «${deviceLabel(s.user_agent)}»?`))) return;
    await api.revokeSession(s.id);
    haptic.success();
    setData(data.filter((x) => x.id !== s.id));
  };
  const logout = async () => {
    if (!(await confirm("Вийти з Тарілки на цьому пристрої?"))) return;
    await api.logout().catch(() => undefined);
    await clearOfflineCache();
    location.replace("/");
  };

  return (
    <section class="section">
      <h2>Пристрої з входом у браузері</h2>
      {data.length === 0 && <p class="muted small">Поки ніде — лише Telegram.</p>}
      {data.map((s) => (
        <div class="card session">
          <div>
            <b>{deviceLabel(s.user_agent)}</b>
            {s.current && <span class="badge">цей пристрій</span>}
            <div class="muted small">вхід {shortDate(s.created_at)} · востаннє {shortDate(s.last_seen_at)}</div>
          </div>
          {s.current ? (
            <button class="btn btn--danger" onClick={logout}>Вийти</button>
          ) : (
            <button class="btn btn--danger" onClick={() => end(s)}>Завершити</button>
          )}
        </div>
      ))}
    </section>
  );
}

export function AppPage() {
  return (
    <main class="page">
      <h1>📲 Застосунок</h1>
      {tg ? <OpenInBrowser /> : <Install />}
      <Sessions />
    </main>
  );
}

import { useEffect, useState } from "preact/hooks";
import { api, ApiError } from "../api";
import { Loader } from "../components/ui";
import { BOT_URL } from "../tg";

/** Вхід поза Telegram: одноразове посилання з бота (?t=…) або 6-значний код. */
export function Login({ token }: { token: string | null }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(!!token);
  const [error, setError] = useState<string | null>(null);

  const submit = async (body: { token?: string; code?: string }) => {
    setBusy(true);
    setError(null);
    try {
      await api.login(body);
      location.replace("/"); // повне перезавантаження: кеші довідників — уже від імені сесії
    } catch (e) {
      setBusy(false);
      setError(
        e instanceof ApiError && e.status === 429
          ? "Забагато спроб. Зачекай 15 хвилин."
          : body.token
            ? "Посилання вже використане або прострочене. Введи код або попроси нове в боті."
            : "Код недійсний або прострочений. Попроси новий у боті.",
      );
    }
  };

  useEffect(() => {
    if (token) submit({ token });
  }, [token]);

  if (busy && token && !error) return <Loader />;

  return (
    <main class="page login">
      <h1>Тарілка 🍽</h1>
      <p class="muted">
        Щоб відкрити сімейний каталог без Telegram, надішли боту <b>/web</b> — він дасть посилання
        для входу й код.
      </p>
      <a class="btn btn--wide btn--primary login__bot" href={`${BOT_URL}?start=web`} target="_blank"
        rel="noopener">
        Отримати код у боті
      </a>
      <form class="card" onSubmit={(e) => (e.preventDefault(), submit({ code }))}>
        <label class="label" for="code">Код з бота</label>
        <input id="code" class="input login__code" inputMode="numeric" autoComplete="one-time-code"
          maxLength={6} placeholder="123456" value={code}
          onInput={(e) => setCode((e.target as HTMLInputElement).value.replace(/\D/g, ""))} />
        <button class="btn btn--primary" type="submit" disabled={busy || code.length !== 6}>
          {busy ? "…" : "Увійти"}
        </button>
      </form>
      {error && <p class="error">{error}</p>}
    </main>
  );
}

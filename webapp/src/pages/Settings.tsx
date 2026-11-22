import { useState } from "preact/hooks";
import { api, type Member } from "../api";
import { ErrorBox, Loader } from "../components/ui";
import { invalidateMeta, useMe, useMeta } from "../hooks";
import { alertMsg, haptic } from "../tg";

const parseAliases = (s: string) => s.split(",").map((a) => a.trim()).filter(Boolean);

function MemberRow({ member }: { member: Member }) {
  const [name, setName] = useState(member.name);
  const [aliases, setAliases] = useState(member.aliases.join(", "));
  const [saved, setSaved] = useState(true);
  const save = async () => {
    try {
      await api.updateMember(member.id, { name: name.trim(), aliases: parseAliases(aliases) });
      haptic.success();
      setSaved(true);
      invalidateMeta();
    } catch {
      alertMsg("Не вдалося зберегти");
    }
  };
  return (
    <div class="card">
      <div class="row">
        <input class="input" value={name} aria-label="Ім'я"
          onInput={(e) => (setName((e.target as HTMLInputElement).value), setSaved(false))} />
        <span class="muted small">{member.has_telegram ? "Telegram ✓" : "без Telegram"}{member.is_admin ? " · адмін" : ""}</span>
      </div>
      <input class="input" value={aliases} placeholder="синоніми через кому: мама, мамуся"
        aria-label="Синоніми"
        onInput={(e) => (setAliases((e.target as HTMLInputElement).value), setSaved(false))} />
      {!saved && <button class="btn btn--primary" onClick={save}>Зберегти</button>}
    </div>
  );
}

export function Settings() {
  const meta = useMeta();
  const me = useMe();
  const [newName, setNewName] = useState("");
  const [newAliases, setNewAliases] = useState("");

  if (me && !me.is_admin) return <ErrorBox error={new Error("Лише для адміна")} />;
  if (!meta) return <Loader />;

  const add = async () => {
    if (!newName.trim()) return;
    try {
      await api.addMember(newName.trim(), parseAliases(newAliases));
      haptic.success();
      setNewName("");
      setNewAliases("");
      invalidateMeta();
    } catch {
      alertMsg("Не вдалося додати");
    }
  };

  return (
    <main class="page">
      <h1>⚙️ Сім'я</h1>
      <p class="muted small">
        Синоніми допомагають боту зрозуміти «вміє мама» чи «бабусин рецепт». Членів сім'ї з Telegram
        запрошуй командою /invite у боті.
      </p>
      {meta.members.map((m) => <MemberRow key={m.id} member={m} />)}
      <h2>Додати без Telegram</h2>
      <div class="card">
        <input class="input" placeholder="Ім'я (наприклад, Бабуся)" value={newName}
          onInput={(e) => setNewName((e.target as HTMLInputElement).value)} />
        <input class="input" placeholder="синоніми через кому" value={newAliases}
          onInput={(e) => setNewAliases((e.target as HTMLInputElement).value)} />
        <button class="btn btn--primary" onClick={add} disabled={!newName.trim()}>➕ Додати</button>
      </div>
    </main>
  );
}

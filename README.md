# Tarilka 🍽

Сімейний каталог страв: Telegram-бот (`@tarilkaa_bot`) для швидкого додавання і Mini App для вибору «Що приготувати?». План MVP — `docs/plan-mvp.md`.

Стек: Python 3.12, FastAPI, aiogram 3 (webhook), SQLAlchemy 2 + Alembic, PostgreSQL 16 (`pg_trgm`), `uv`.

## Локальний запуск і тести

```bash
python3 -m venv .venv && .venv/bin/pip install uv && .venv/bin/uv sync
cp .env.example .env   # заповнити; для API без Telegram: ENV=dev, DEV_AUTH_MEMBER_ID=1
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Тести потребують окремої бази (її схема скидається на старті):

```bash
TEST_DATABASE_URL=postgresql+asyncpg://tarilka:***@localhost:5432/tarilka_test .venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Деплой (VPS)

Контейнер `app` працює в Docker-мережі `infra` поруч із `postgres`; Caddy (`/opt/n8n/Caddyfile`) проксує `tarilka.romis.cloud` → `tarilka-app:8000` і сам випускає TLS. Міграції виконуються при старті контейнера, вебхук і команди бота встановлюються там само (`ENV=prod`).

```bash
git pull && docker compose up -d --build
docker compose logs -f app
```

Члени сім'ї: `/invite [Ім'я]` у боті (адмін) або

```bash
docker compose exec app python -m app.cli add-member --name Ім'я --username tg_username [--admin] [--alias мама]
docker compose exec app python -m app.cli list-members
```

Username прив'язується до Telegram ID при першому повідомленні; далі доступ лише за ID.

## Бекапи

`ops/backup.sh` (cron о 04:00): `pg_dump -Fc` у `/var/backups/tarilka/` (14 копій), у неділю — tar тому `tarilka_media` (4 копії).

Відновлення:

```bash
ops/restore.sh /var/backups/tarilka/tarilka-YYYYMMDD-HHMM.dump              # перевірка в tarilka_restore
docker compose stop app && ops/restore.sh <dump> tarilka && docker compose up -d   # прод
```

## yt-dlp (етап 3)

Оновлюється при старті worker; вручну: `docker compose exec worker uv pip install -U yt-dlp`.

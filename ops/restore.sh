#!/usr/bin/env bash
# Відновлення дампу в базу (за замовчуванням — у тестову tarilka_restore для перевірки).
#   ops/restore.sh /var/backups/tarilka/tarilka-YYYYMMDD-HHMM.dump [target_db]
# Відновлення в прод: зупинити app (docker compose stop app), target_db=tarilka, потім up -d.
set -euo pipefail

DUMP=${1:?шлях до .dump}
TARGET=${2:-tarilka_restore}
PG_CONTAINER=${PG_CONTAINER:-postgres}
PG_SUPERUSER=${PG_SUPERUSER:-postgres}

psql() { docker exec -i "$PG_CONTAINER" psql -U "$PG_SUPERUSER" -v ON_ERROR_STOP=1 "$@"; }

psql -d postgres -c "DROP DATABASE IF EXISTS $TARGET WITH (FORCE)"
psql -d postgres -c "CREATE DATABASE $TARGET OWNER tarilka"
docker exec -i "$PG_CONTAINER" pg_restore -U "$PG_SUPERUSER" -d "$TARGET" --no-owner --role=tarilka < "$DUMP"
psql -d "$TARGET" -tAc "SELECT 'tags=' || count(*) FROM tags"
psql -d "$TARGET" -tAc "SELECT 'members=' || count(*) FROM members"

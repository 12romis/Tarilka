#!/usr/bin/env bash
# Бекап Tarilka: щодня pg_dump (14 копій), щонеділі — tar тому з фото (4 копії).
# Cron: 0 4 * * * /root/projects/tarilka/ops/backup.sh >> /var/log/tarilka-backup.log 2>&1
set -euo pipefail

DIR=/var/backups/tarilka
PG_CONTAINER=${PG_CONTAINER:-postgres}
PG_SUPERUSER=${PG_SUPERUSER:-postgres}
MEDIA_VOLUME=${MEDIA_VOLUME:-tarilka_media}
TS=$(date +%Y%m%d-%H%M)

mkdir -p "$DIR"
chmod 700 "$DIR"

# Дамп від імені суперкористувача Postgres: роль tarilka для бекапу не потрібна.
docker exec "$PG_CONTAINER" pg_dump -U "$PG_SUPERUSER" -Fc tarilka > "$DIR/tarilka-$TS.dump.tmp"
mv "$DIR/tarilka-$TS.dump.tmp" "$DIR/tarilka-$TS.dump"
ls -1t "$DIR"/tarilka-*.dump | tail -n +15 | xargs -r rm --

if [[ $(date +%u) == 7 ]] && docker volume inspect "$MEDIA_VOLUME" >/dev/null 2>&1; then
  docker run --rm -v "$MEDIA_VOLUME":/media:ro -v "$DIR":/out alpine \
    tar czf "/out/media-$TS.tar.gz" -C /media .
  ls -1t "$DIR"/media-*.tar.gz | tail -n +5 | xargs -r rm --
fi

echo "$(date -Is) ok $DIR/tarilka-$TS.dump"

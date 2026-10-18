#!/bin/bash
# Daredevil PostgreSQL backup — Daredevil's own database plus Sonarr's,
# Radarr's and Prowlarr's (they keep their settings and libraries in the same
# Postgres). Their log databases aren't backed up.
#
# Dumps each database, compresses it to the Backups share, and prunes anything
# older than KEEP_DAYS. Deliberately targets a share separate from appdata
# (Cache) so a Cache pool failure can't take out the live DB and its backups
# in one shot.
#
# Run manually:  bash scripts/backup-db.sh
# On Unraid:     schedule via the User Scripts plugin (see README → Backups).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${DAREDEVIL_BACKUP_DIR:-/mnt/user/Backups/daredevil}"
KEEP_DAYS="${DAREDEVIL_BACKUP_KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
cd "$APP_DIR"

timestamp="$(date +%Y%m%d_%H%M)"
failed=0

dump() {
    local database="$1" out_file="$2"
    if ! docker compose exec -T db psql -U daredevil -d postgres -tAc \
            "SELECT 1 FROM pg_database WHERE datname = '$database'" | grep -q 1; then
        echo "Skipped $database (not created yet)"
        return
    fi
    docker compose exec -T db pg_dump -U daredevil "$database" | gzip > "$out_file"
    if [ -s "$out_file" ] && [ "$(gzip -dc "$out_file" | head -c 1 | wc -c)" -gt 0 ]; then
        echo "Backup written: $out_file ($(du -h "$out_file" | cut -f1))"
    else
        echo "Backup of $database FAILED — empty output file, removing" >&2
        rm -f "$out_file"
        failed=1
    fi
}

# Daredevil keeps its original file name, so existing restore notes still apply
dump daredevil "$BACKUP_DIR/db_${timestamp}.sql.gz"
for app in sonarr radarr prowlarr; do
    dump "$app-main" "$BACKUP_DIR/${app}_${timestamp}.sql.gz"
done

# Prune backups older than KEEP_DAYS
find "$BACKUP_DIR" -maxdepth 1 \( -name 'db_*.sql.gz' -o -name 'sonarr_*.sql.gz' -o -name 'radarr_*.sql.gz' \
    -o -name 'prowlarr_*.sql.gz' \) -mtime +"$KEEP_DAYS" -print -delete

exit "$failed"

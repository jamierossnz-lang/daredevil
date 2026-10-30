#!/bin/bash
# Keeps the third-party containers (Plex, Sonarr, Radarr, Prowlarr,
# qBittorrent, Postgres, …) up to date. `docker compose up -d` never re-pulls
# an image it already has, so a :latest tag otherwise stays on whatever
# version was first pulled, months behind on security fixes.
#
# Backs up the databases first (a new Sonarr, Radarr or Prowlarr migrates its
# database when it starts), pulls every image that isn't built here, recreates
# the containers whose image changed, and deletes the images they leave
# behind. A container that has been stopped by hand stays stopped.
#
# Run manually:  bash scripts/update-images.sh         (every service)
#                bash scripts/update-images.sh plex    (only those named)
# On Unraid:     schedule weekly via the User Scripts plugin (see README →
#                Keeping the containers up to date).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
. scripts/notify.sh

# A failed run only shows in the User Scripts log, so the containers would
# quietly fall behind again. Say so in Daredevil instead.
step='backing up the databases'
notify_failure() {
    notify 'Container update failed' "Stopped while $step on $(hostname). See the User Scripts log." high warning
}
trap notify_failure ERR

# Only Postgres and the apps that keep their data in it need a backup first,
# so a Plex-only run can't be held up by a failing one
needs_backup=$(( $# == 0 ))
for service in "$@"; do
    case "$service" in db|sonarr|radarr|prowlarr) needs_backup=1 ;; esac
done
if [ "$needs_backup" = 1 ]; then
    bash scripts/backup-db.sh
fi

step='pulling the new images'
# A failed pull stops here with nothing recreated; the next run tries again
docker compose pull --ignore-buildable "$@"

step='recreating the containers'
running="$(docker compose ps --services --status running "$@")"
if [ -n "$running" ]; then
    # Service names have no spaces, so word splitting gives one per argument
    # shellcheck disable=SC2086
    docker compose up -d $running
fi

step='deleting the old images'
docker image prune -f

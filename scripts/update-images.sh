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
# Run manually:  bash scripts/update-images.sh
# On Unraid:     schedule weekly via the User Scripts plugin (see README →
#                Keeping the containers up to date).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

bash scripts/backup-db.sh

# A failed pull stops here with nothing recreated; the next run tries again
docker compose pull --ignore-buildable

running="$(docker compose ps --services --status running)"
if [ -n "$running" ]; then
    # Service names have no spaces, so word splitting gives one per argument
    # shellcheck disable=SC2086
    docker compose up -d $running
fi

docker image prune -f

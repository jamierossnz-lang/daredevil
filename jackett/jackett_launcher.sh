#!/bin/sh

# Wraps linuxserver/jackett's own jackett_launcher.sh (bind-mounted over it in
# docker-compose.yml) to force --ListenPublic. Without it, Jackett only accepts
# requests whose Host header is localhost/127.0.0.1 — ServerConfig.json's
# AllowExternal/LocalBindAddress alone don't relax that, so Daredevil/qBittorrent
# talking to it over the Docker network as "jackett:9117" get a flat 400.
# See: https://github.com/Jackett/Jackett/issues/5208#issuecomment-547565515

# Get full Jackett root path
JACKETT_DIR="$(dirname "$(readlink -f "$0")")"

# Launch Jackett (with CLI parameters)
"${JACKETT_DIR}/jackett" --NoRestart --ListenPublic "$@"
ec=$?

# Get user running the service
JACKETT_USER=$(whoami)

# Wait until the updater ends
while pgrep -u "${JACKETT_USER}" JackettUpdater > /dev/null; do
    sleep 1
done

exit $ec

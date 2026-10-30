#!/bin/bash
# Raises the alarm if the Plex container shows signs of a break-in. In October
# 2026 a crypto miner ran in it for days and the only sign was fan noise.
# Plex is the service most likely to be open to the internet, so this looks
# for what that attack left behind:
#
#   - files added to the container outside the few places the linuxserver
#     image changes at start (the miner, its launcher and a crontab went into
#     /tmp, /var/tmp and /var/spool/cron)
#   - a program running from a temp folder, from Plex's /config, or from a
#     file that has since been deleted
#   - any other program that isn't Plex's own and is still running at the
#     next check (one-off commands come and go)
#   - programs in /dev/shm, or a crontab saved to /config, which the image
#     would re-install on every start
#   - how that attack got in: it set Plex's hidden TranscoderH264Options to
#     make the transcoder write a library into Plex's Codecs folder, which
#     Plex then loaded. So a value in that setting, or a file in Codecs
#     that isn't one of Plex's codecs, raises the alarm too.
#
# Everything is read from the host, so nothing inside the container has to be
# trusted. Sends one urgent notification per new set of findings, through
# Daredevil (and ntfy, if set up).
#
# Run manually:  bash scripts/check-plex.sh   (prints what it finds)
# On Unraid:     schedule every 15 minutes via User Scripts (see README →
#                Plex break-in alarm).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
. scripts/notify.sh

# In RAM on Unraid, so a reboot re-arms an alarm that's already been sent
STATE_DIR=/tmp/daredevil-check-plex
mkdir -p "$STATE_DIR"

cid="$(docker compose ps -q plex)"
if [ -z "$cid" ]; then
    echo "Plex isn't running"
    exit 0
fi
init_pid="$(docker inspect -f '{{.State.Pid}}' "$cid")"
config_dir="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' "$cid")"
findings=()

# Files: what the image changes at start, plus what the NVIDIA runtime mounts
# in (only root can write to /usr and /etc in there; Plex runs as abc)
expected='^(/run|/app|/data|/defaults|/etc/(passwd|group)-?|/etc/s6-overlay)(/|$)'
nvidia='^/(usr|etc)/.*(nvidia|libcuda|libnvcuvid|libnvoptix)[^/]*$|^/etc/ld\.so\.cache$|^/usr/lib/firmware/nvidia(/|$)'
diff_lines="$(docker diff "$cid")"
while IFS= read -r line; do
    kind="${line:0:1}" path="${line:2}"
    [[ $path =~ $expected || $path =~ $nvidia ]] && continue
    # Folders show up whenever anything inside them changes, even after it's
    # gone again; the files themselves are listed separately
    full="/proc/$init_pid/root$path"
    [ -d "$full" ] && [ ! -L "$full" ] && continue
    case "$kind" in A) what=added ;; D) what=deleted ;; *) what=changed ;; esac
    findings+=("$what $path")
done <<<"$diff_lines"

# Programs
eae='/config/Library/Application Support/Plex Media Server/Codecs/EasyAudioEncoder-*/EasyAudioEncoder/EasyAudioEncoder'
: > "$STATE_DIR/unknown.new"
for pid in $(docker top "$cid" -eo pid | tail -n +2); do
    exe="$(readlink "/proc/$pid/exe" 2>/dev/null)" || continue  # already gone
    cmd="$(tr '\0' ' ' 2>/dev/null < "/proc/$pid/cmdline" | sed 's/ *$//' | cut -c1-80)" || true
    path="${exe% (deleted)}"
    # Plex, the s6 supervisor, Plex's downloaded audio encoder, and the cron
    # service idling because there are no crontabs
    case "$path" in
        /usr/lib/plexmediaserver/*|/package/admin/*) continue ;;
    esac
    # shellcheck disable=SC2053  # $eae is a pattern
    [[ $path == $eae ]] && continue
    case "$path|$cmd" in
        '/usr/bin/bash|bash ./run svc-'*|*'/sleep|sleep infinity') continue ;;
    esac
    case "$exe" in
        /tmp/*|/var/tmp/*|/dev/shm/*|/config/*|*' (deleted)')
            findings+=("program $exe running as pid $pid ($cmd)") ;;
        *)
            if grep -qxF "$pid $exe" "$STATE_DIR/unknown" 2>/dev/null; then
                findings+=("unexpected program $exe still running as pid $pid ($cmd)")
            fi
            echo "$pid $exe" >> "$STATE_DIR/unknown.new" ;;
    esac
done
mv -f "$STATE_DIR/unknown.new" "$STATE_DIR/unknown"

# Programs or hidden files in shared memory (Plex keeps small data files there)
while IFS= read -r file; do
    findings+=("file in /dev/shm: ${file#/proc/"$init_pid"/root}")
done < <(find "/proc/$init_pid/root/dev/shm" -mindepth 1 \( -type f -perm /111 -o -name '.*' \) 2>/dev/null)

# A crontab here is copied back into the container on every start
if [ -n "$config_dir" ] && [ -n "$(ls -A "$config_dir/crontabs" 2>/dev/null)" ]; then
    findings+=("crontab saved in $config_dir/crontabs")
fi

pms_dir="$config_dir/Library/Application Support/Plex Media Server"
# Raw transcoder options: nothing in Plex's settings screens sets these
while IFS= read -r pref; do
    findings+=("Plex setting $pref")
done < <(grep -oE 'TranscoderH264Options[A-Za-z]*="[^"]+"' "$pms_dir/Preferences.xml" 2>/dev/null)

# Codecs holds what Plex downloads: lib*_decoder.so / lib*_encoder.so in a
# versioned folder, and the EasyAudioEncoder
while IFS= read -r rel; do
    case "$rel" in
        .device-id|EasyAudioEncoder-*-linux-*.zip) ;;
        EasyAudioEncoder-*-linux-*/EasyAudioEncoder/EasyAudioEncoder) ;;
        EasyAudioEncoder-*-linux-*/EasyAudioEncoder/eae-license.txt) ;;
        *-linux-*/lib*_decoder.so|*-linux-*/lib*_encoder.so) ;;
        *) findings+=("unexpected file in Plex's Codecs folder: $rel") ;;
    esac
done < <(cd "$pms_dir/Codecs" 2>/dev/null && find . -mindepth 1 ! -type d | sed 's|^\./||')

if [ "${#findings[@]}" -eq 0 ]; then
    echo "Plex looks clean"
    rm -f "$STATE_DIR/alerted"
    exit 0
fi

report="$(printf '%s\n' "${findings[@]}" | sort)"
echo "$report"
# One alert per new set of findings, not one every 15 minutes
if [ "$report" != "$(cat "$STATE_DIR/alerted" 2>/dev/null)" ]; then
    count="${#findings[@]}"
    shown="$(head -5 <<<"$report")"
    [ "$count" -gt 5 ] && shown="$shown
…and $((count - 5)) more"
    notify 'Plex may be compromised' "On $(hostname):
$shown

If you don't recognise this, stop Plex: docker stop $(docker inspect -f '{{.Name}}' "$cid" | tr -d /)" urgent rotating_light
    printf '%s' "$report" > "$STATE_DIR/alerted"
fi
exit 1

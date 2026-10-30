# Sourced by the host-side scripts to post to Daredevil's notifications (and
# ntfy, if set up). Sent from the web container because Docker gives it
# working DNS even when the host's own lookups fail — on Unraid that's the
# usual reason something else in these scripts failed. Never fails the caller.
#
# Usage, from the project folder:  notify <title> <message> [priority] [tag]
notify() {
    docker compose exec -T -e TITLE="$1" -e MSG="$2" -e PRIORITY="${3:-default}" -e TAG="${4:-}" web \
        python manage.py shell -c "import os; from apps.notifications.notify import send; send(os.environ['TITLE'], os.environ['MSG'], priority=os.environ['PRIORITY'], tags=[t for t in [os.environ['TAG']] if t])" \
        </dev/null || true
}

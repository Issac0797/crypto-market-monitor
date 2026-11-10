#!/bin/bash
# Run only AFTER the human approves this production cutover.
set -Eeuo pipefail
umask 077
test "${1:-}" = --approved-cutover
new="${2:?absolute staged release required}"
root=/opt/crypto-market-monitor
state=/var/lib/crypto-market-monitor
old=$(readlink -f "$root/current")
case "$new" in "$root"/releases/bot-*) ;; *) exit 2;; esac
test "$(readlink -f "$new")" = "$new"
test -x "$new/venv/bin/python"
test -f "$new/config/bot-phase2-3.toml"
test "$new" != "$old"
backup="/var/backups/crypto-market-monitor/pre-$(basename "$new")-$(date -u +%Y%m%dT%H%M%SZ)"
test ! -e "$backup"
install -d -m 700 /var/backups/crypto-market-monitor
backed_up=0
rollback() {
    trap - ERR
    set +e
    systemctl stop crypto-market-monitor
    if [ "$backed_up" = 1 ]; then
        # Keep the failed new version's state/logs before restoring the snapshot.
        cp "$state/data/bot-b.sqlite3" "$backup/failed-new-raw.sqlite3"
        for suffix in -journal -wal -shm; do
            if [ -f "$state/data/bot-b.sqlite3$suffix" ]; then
                cp "$state/data/bot-b.sqlite3$suffix" "$backup/failed-new-raw.sqlite3$suffix"
            fi
        done
        "$new/venv/bin/python" -c 'from pathlib import Path; from crypto_market_monitor.operations import backup; import sys; backup(Path(sys.argv[1]),Path(sys.argv[2]))' "$state" "$backup/failed-new-state"
        cp -a "$state/logs" "$backup/failed-new-logs"
        cp "$backup/bot-b.sqlite3" "$state/data/bot-b.sqlite3"
        chown cmm:cmm "$state/data/bot-b.sqlite3"
        chmod 600 "$state/data/bot-b.sqlite3"
        cp "$backup/config.toml" /etc/crypto-market-monitor/config.toml
        cp "$backup/service.unit" /etc/systemd/system/crypto-market-monitor.service
    fi
    ln -s "$old" "$root/rollback-next"
    mv -Tf "$root/rollback-next" "$root/current"
    systemctl daemon-reload
    systemctl start crypto-market-monitor
    echo "CUTOVER_FAILED_ROLLED_BACK backup=$backup" >&2
    exit 1
}
trap rollback ERR
systemctl stop crypto-market-monitor
"$new/venv/bin/python" -c 'from pathlib import Path; from crypto_market_monitor.operations import backup; import sys; backup(Path(sys.argv[1]),Path(sys.argv[2]))' "$state" "$backup"
cp /etc/crypto-market-monitor/config.toml "$backup/config.toml"
cp /etc/systemd/system/crypto-market-monitor.service "$backup/service.unit"
printf '%s\n' "$old" > "$backup/old-release.txt"
backed_up=1
"$new/venv/bin/python" -m crypto_market_monitor.bot_migrate --state-dir "$state" --from-config /etc/crypto-market-monitor/config.toml --to-config "$new/config/bot-phase2-3.toml"
install -m 644 "$new/config/bot-phase2-3.toml" /etc/crypto-market-monitor/config.toml
install -m 644 "$new/deploy/systemd/crypto-market-monitor.service" /etc/systemd/system/crypto-market-monitor.service
chown cmm:cmm "$state/data/bot-b.sqlite3"
chmod 600 "$state/data/bot-b.sqlite3"
ln -s "$new" "$root/upgrade-next"
mv -Tf "$root/upgrade-next" "$root/current"
systemd-analyze verify /etc/systemd/system/crypto-market-monitor.service
systemctl daemon-reload
systemctl enable --now crypto-market-monitor
systemctl is-active --quiet crypto-market-monitor
"$new/venv/bin/python" - <<'PY'
import hashlib,json,time
from pathlib import Path
sha=hashlib.sha256(Path('/etc/crypto-market-monitor/config.toml').read_bytes()).hexdigest()
for _ in range(18):
    h=json.loads(Path('/var/lib/crypto-market-monitor/health.json').read_text())
    fresh=time.time_ns()//1000000-h.get('updated_ms',0)<30000
    books=all(v=='fresh' for v in h.get('stablecoin_feed',{}).values()) and all(v=='fresh' for v in h.get('basis_feed',{}).values())
    funding=all(v['status'] in {'fresh','waiting_for_fresh_publication'} for v in h.get('funding_feed',{}).values())
    if fresh and h.get('config_sha256')==sha and books and funding and h.get('usde_feed',{}).get('status')=='fresh' and h.get('command_polling'):
        print('CUTOVER_HEALTH_VERIFIED');break
    time.sleep(5)
else:
    raise SystemExit('New service health/polling not verified; rollback required')
PY
trap - ERR
printf 'CUTOVER_COMMITTED release=%s backup=%s\n' "$new" "$backup"

#!/bin/bash
# Explicit human-approved rollback; preserves the failed/current DB and logs.
set -Eeuo pipefail
umask 077
test "${1:-}" = --approved-rollback
backup="${2:?absolute snapshot path required}"
case "$backup" in /var/backups/crypto-market-monitor/pre-bot-*) ;; *) exit 2;; esac
test "$(readlink -f "$backup")" = "$backup"
old=$(cat "$backup/old-release.txt")
case "$old" in /opt/crypto-market-monitor/releases/bot-*) ;; *) exit 2;; esac
test -x "$old/venv/bin/python"
current=$(readlink -f /opt/crypto-market-monitor/current)
state=/var/lib/crypto-market-monitor
failed="$backup/rollback-preserved-$(date -u +%Y%m%dT%H%M%SZ)"
systemctl stop crypto-market-monitor
cp "$state/data/bot-b.sqlite3" "$backup/rollback-raw-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
"$current/venv/bin/python" -c 'from pathlib import Path; from crypto_market_monitor.operations import backup; import sys; backup(Path(sys.argv[1]),Path(sys.argv[2]))' "$state" "$failed"
cp -a "$state/logs" "$failed/logs"
cp "$backup/bot-b.sqlite3" "$state/data/bot-b.sqlite3"
chown cmm:cmm "$state/data/bot-b.sqlite3"
chmod 600 "$state/data/bot-b.sqlite3"
cp "$backup/config.toml" /etc/crypto-market-monitor/config.toml
cp "$backup/service.unit" /etc/systemd/system/crypto-market-monitor.service
ln -s "$old" /opt/crypto-market-monitor/rollback-next
mv -Tf /opt/crypto-market-monitor/rollback-next /opt/crypto-market-monitor/current
systemctl daemon-reload
systemctl start crypto-market-monitor
printf 'ROLLBACK_COMMITTED release=%s preserved=%s\n' "$old" "$failed"

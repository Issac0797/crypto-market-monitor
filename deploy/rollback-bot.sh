#!/bin/bash
# Explicit human-approved rollback; preserves the failed/current DB and logs.
set -Eeuo pipefail
umask 077
test "${1:-}" = --approved-rollback
backup="${2:?absolute snapshot path required}"
case "$backup" in /var/backups/cql-bot/pre-bot-*) ;; *) exit 2;; esac
test "$(readlink -f "$backup")" = "$backup"
old=$(cat "$backup/old-release.txt")
case "$old" in /opt/cql-bot/releases/bot-*) ;; *) exit 2;; esac
test -x "$old/venv/bin/python"
current=$(readlink -f /opt/cql-bot/current)
state=/var/lib/cql-bot
failed="$backup/rollback-preserved-$(date -u +%Y%m%dT%H%M%SZ)"
systemctl stop cql-bot
cp "$state/data/bot-b.sqlite3" "$backup/rollback-raw-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
"$current/venv/bin/python" -c 'from pathlib import Path; from crypto_market_monitor.operations import backup; import sys; backup(Path(sys.argv[1]),Path(sys.argv[2]))' "$state" "$failed"
cp -a "$state/logs" "$failed/logs"
cp "$backup/bot-b.sqlite3" "$state/data/bot-b.sqlite3"
chown cqlbot:cqlbot "$state/data/bot-b.sqlite3"
chmod 600 "$state/data/bot-b.sqlite3"
cp "$backup/config.toml" /etc/cql-bot/config.toml
cp "$backup/service.unit" /etc/systemd/system/cql-bot.service
ln -s "$old" /opt/cql-bot/rollback-next
mv -Tf /opt/cql-bot/rollback-next /opt/cql-bot/current
systemctl daemon-reload
systemctl start cql-bot
printf 'ROLLBACK_COMMITTED release=%s preserved=%s\n' "$old" "$failed"

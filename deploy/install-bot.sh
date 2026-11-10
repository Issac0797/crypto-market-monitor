#!/bin/sh
# Run as root with a verified, source-only release archive. Does not start services.
set -eu
release_id="$1"
archive="$2"
mode="${3:-stage}"
case "$mode" in stage|activate) ;; *) echo 'Invalid install mode' >&2; exit 1;; esac
case "$release_id" in *[!a-z0-9-]*|'') echo 'Invalid release id' >&2; exit 1;; esac
root=/opt/cql-bot
release="$root/releases/$release_id"
test ! -e "$release"
id cqlbot >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/cql-bot --shell /usr/sbin/nologin cqlbot
install -d -m 755 "$root/releases" /etc/cql-bot
install -d -m 700 -o cqlbot -g cqlbot /var/lib/cql-bot
install -d -m 755 "$release"
python3 - "$archive" "$release" <<'PY'
import hashlib, json, pathlib, sys, tarfile
archive, target = sys.argv[1], pathlib.Path(sys.argv[2])
with tarfile.open(archive) as source:
    source.extractall(target, filter='data')
manifest = json.loads((target / 'bot-release.json').read_text())
for name, digest in manifest['files'].items():
    path = (target / name).resolve()
    if not path.is_relative_to(target) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit('Release checksum mismatch')
print('Verified source release', manifest['source_sha256'])
PY
if ! python3 -m venv "$release/venv"; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-venv
    python3 -m venv "$release/venv"
fi
constraints="$release/dependency-constraints.txt"
if [ -x "$root/current/venv/bin/python" ]; then
    "$root/current/venv/bin/python" -m pip freeze | sed -e '/^[Cc]rypto-[Qq]uant-[Ll]ab[ =@]/d' -e '/^[Cc]rypto-[Mm]arket-[Mm]onitor[ =@]/d' > "$constraints"
else
    : > "$constraints"
fi
"$release/venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir --no-compile --constraint "$constraints" "$release"
"$release/venv/bin/python" -m pip check
"$release/venv/bin/python" -m pip freeze > "$release/installed-requirements.txt"
if [ "$mode" = stage ]; then
    echo 'Staged verified release; existing service and configuration unchanged'
    exit 0
fi
install -m 644 "$release/config/bot.toml" /etc/cql-bot/config.toml
install -m 644 "$release/deploy/systemd/cql-bot.service" /etc/systemd/system/cql-bot.service
ln -s "$release" "$root/next"
mv -Tf "$root/next" "$root/current"
systemctl daemon-reload
echo 'Installed Bot release; not started yet'

#!/usr/bin/env bash
# GoTani one-shot provisioning for Ubuntu 24.04 LTS on an Azure VM (D33, D39).
# Idempotent: safe to re-run for updates (git pull, migrate, collectstatic, restart).
#
# Usage (on the VM):
#   sudo GOTANI_DOMAIN=gotani.duckdns.org \
#        DUCKDNS_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
#        REPO_URL=https://github.com/<you>/gotani.git \
#        bash deploy/setup_vm.sh
#
# Optional: BRANCH=main  SEED_DEMO=1 (load demo data once)  DEMO_PASSWORD=...
#
# Before running: reserve a STATIC public IP for the VM, point the DuckDNS
# subdomain at it, and allow inbound TCP 22, 80, 443 in the Azure NSG.

set -euo pipefail

: "${GOTANI_DOMAIN:?Set GOTANI_DOMAIN, e.g. gotani.duckdns.org}"
: "${DUCKDNS_TOKEN:?Set DUCKDNS_TOKEN (from duckdns.org)}"
: "${REPO_URL:?Set REPO_URL (git clone URL)}"
BRANCH="${BRANCH:-main}"
SEED_DEMO="${SEED_DEMO:-0}"

APP_USER=gotani
APP_DIR=/srv/gotani
VENV="$APP_DIR/.venv"
DB_NAME=gotani
DB_USER=gotani
BACKUP_DIR=/var/backups/gotani
SECRETS_DIR=/etc/gotani

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

log() { printf '\n==> %s\n' "$*"; }
as_app() { sudo -u "$APP_USER" -H "$@"; }

log "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-dev git curl ufw cron \
  postgresql-16 postgresql-client-16 caddy
timedatectl set-timezone Asia/Jakarta || true

log "Firewall (SSH first so the session is never cut off)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

log "App user and code"
id -u "$APP_USER" >/dev/null 2>&1 || adduser --system --group --home "$APP_DIR" --shell /bin/bash "$APP_USER"
mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"
if [[ ! -d "$APP_DIR/.git" ]]; then
  as_app git -C "$APP_DIR" init -q
  as_app git -C "$APP_DIR" remote add origin "$REPO_URL"
fi
as_app git -C "$APP_DIR" fetch --prune origin "$BRANCH"
# Tracked files follow origin; untracked .env, media/, staticfiles/ are kept.
as_app git -C "$APP_DIR" checkout -q -B "$BRANCH" "origin/$BRANCH"
as_app git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
install -d -o "$APP_USER" -g "$APP_USER" -m 2750 "$APP_DIR/media"

log "Python virtualenv"
[[ -d "$VENV" ]] || as_app python3 -m venv "$VENV"
as_app "$VENV/bin/pip" install --upgrade pip
as_app "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"

log "PostgreSQL 16 (same VM)"
systemctl enable --now postgresql
install -d -m 700 "$SECRETS_DIR"
if [[ ! -f "$SECRETS_DIR/db_password" ]]; then
  python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$SECRETS_DIR/db_password"
  chmod 600 "$SECRETS_DIR/db_password"
fi
DB_PASSWORD="$(cat "$SECRETS_DIR/db_password")"
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  sudo -u postgres psql -qc "ALTER ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASSWORD';"
else
  sudo -u postgres psql -qc "CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASSWORD';"
fi
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
  || sudo -u postgres createdb -O "$DB_USER" -E UTF8 "$DB_NAME"

log "Production .env"
ENV_FILE="$APP_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
  cat > "$ENV_FILE" <<EOF
DEBUG=False
SECRET_KEY=$SECRET_KEY
ALLOWED_HOSTS=$GOTANI_DOMAIN
CSRF_TRUSTED_ORIGINS=https://$GOTANI_DOMAIN
DATABASE_URL=postgres://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_NAME
SECURE_HSTS_SECONDS=3600
DEMO_PASSWORD=${DEMO_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')}
EOF
  echo "Wrote $ENV_FILE (demo user password is DEMO_PASSWORD inside it)."
fi
chown "$APP_USER:$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

log "Migrate, collectstatic, checks"
cd "$APP_DIR"
as_app "$VENV/bin/python" manage.py migrate --noinput
as_app "$VENV/bin/python" manage.py collectstatic --noinput
as_app "$VENV/bin/python" manage.py check --deploy
if [[ "$SEED_DEMO" == "1" ]]; then
  as_app "$VENV/bin/python" manage.py seed_demo
  as_app "$VENV/bin/python" manage.py check_mass_balance
fi

log "Gunicorn systemd service"
install -m 644 "$APP_DIR/deploy/gotani.service" /etc/systemd/system/gotani.service
systemctl daemon-reload
systemctl enable gotani
systemctl restart gotani

log "Caddy (automatic HTTPS for $GOTANI_DOMAIN)"
usermod -aG "$APP_USER" caddy   # read access to /srv/gotani/media
install -d -o caddy -g caddy /var/log/caddy
sed "s/GOTANI_DOMAIN/$GOTANI_DOMAIN/" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl enable caddy
systemctl restart caddy

log "DuckDNS update cron (every 5 minutes, safety net for the static IP)"
SUBDOMAIN="${GOTANI_DOMAIN%.duckdns.org}"
printf '%s\n' "$DUCKDNS_TOKEN" > "$SECRETS_DIR/duckdns_token"
chmod 600 "$SECRETS_DIR/duckdns_token"
cat > /usr/local/bin/gotani-duckdns <<EOF
#!/usr/bin/env bash
set -eu
TOKEN="\$(cat $SECRETS_DIR/duckdns_token)"
curl -fsS --max-time 20 "https://www.duckdns.org/update?domains=$SUBDOMAIN&token=\$TOKEN&ip=" \
  -o /var/log/gotani-duckdns.log
EOF
chmod 700 /usr/local/bin/gotani-duckdns
echo "*/5 * * * * root /usr/local/bin/gotani-duckdns" > /etc/cron.d/gotani-duckdns
chmod 644 /etc/cron.d/gotani-duckdns
/usr/local/bin/gotani-duckdns && echo "DuckDNS: $(cat /var/log/gotani-duckdns.log)"

log "Nightly pg_dump with 7-day retention"
install -d -o postgres -g postgres -m 700 "$BACKUP_DIR"
cat > /usr/local/bin/gotani-backup <<EOF
#!/usr/bin/env bash
set -euo pipefail
pg_dump -Fc $DB_NAME > "$BACKUP_DIR/$DB_NAME-\$(date +%F).dump"
find "$BACKUP_DIR" -name '$DB_NAME-*.dump' -mtime +6 -delete
EOF
chmod 755 /usr/local/bin/gotani-backup
echo "30 2 * * * postgres /usr/local/bin/gotani-backup" > /etc/cron.d/gotani-backup
chmod 644 /etc/cron.d/gotani-backup
systemctl enable --now cron

log "Done"
systemctl --no-pager --lines=0 status gotani caddy || true
echo "Open https://$GOTANI_DOMAIN  (first certificate can take ~30 s)."
echo "Create an admin: sudo -u $APP_USER $VENV/bin/python $APP_DIR/manage.py createsuperuser"

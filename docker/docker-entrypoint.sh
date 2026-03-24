#!/bin/sh
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
export PUID PGID

case "$PUID" in
    *[!0-9]*) echo "ERROR: PUID must be a number, got '$PUID'."; exit 1 ;;
esac
case "$PGID" in
    *[!0-9]*) echo "ERROR: PGID must be a number, got '$PGID'."; exit 1 ;;
esac

if [ -n "$TIMEZONE" ] && [ -z "$TZ" ]; then
    export TZ="$TIMEZONE"
fi

mkdir -p /etc/rspamd/local.d
mkdir -p /app/data/redis

if ! touch /app/data/.preflight 2>/dev/null; then
    echo "ERROR: /app/data is not writable. Check that the data volume is mounted with correct permissions."
    exit 1
fi
rm -f /app/data/.preflight

if ! getent group "$PGID" > /dev/null 2>&1; then
    groupadd -g "$PGID" boxwatchr
fi

if ! getent passwd "$PUID" > /dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin boxwatchr 2>/dev/null
fi

chown -R "$PUID:$PGID" /app/data /app/config

if [ -z "$RSPAMD_PASSWORD" ]; then
    RSPAMD_PASSWORD=$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)
    export RSPAMD_PASSWORD
    echo "INFO: RSPAMD_PASSWORD not set. A random password has been generated for this session."
    echo "INFO: rspamd web interface password: $RSPAMD_PASSWORD"
fi

RSPAMD_HASH=$(rspamadm pw -q -p "$RSPAMD_PASSWORD")

cat > /etc/rspamd/local.d/worker-controller.inc << EOF
# This file is generated automatically on container startup.
# Do not edit it manually. Set RSPAMD_PASSWORD in your .env file instead.
password = "$RSPAMD_HASH";
bind_socket = "*:11334";
EOF

cat > /etc/rspamd/local.d/greylist.conf << 'EOF'
enabled = false;
EOF

if [ -d "/app/config/rspamd/local.d" ]; then
    cp /app/config/rspamd/local.d/* /etc/rspamd/local.d/ 2>/dev/null || true
fi

cat > /etc/rspamd/local.d/classifier-bayes.conf << 'EOF'
backend = "redis";
new_schema = true;
EOF

cat > /etc/rspamd/local.d/redis.conf << 'EOF'
servers = "127.0.0.1";
EOF

cat > /etc/rspamd/local.d/options.inc << 'EOF'
dns {
    nameserver = ["127.0.0.1:5335"];
}
EOF

SUPERVISOR_PASSWORD=$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)
export SUPERVISOR_PASSWORD

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
#!/usr/bin/env bash
# Finish HTTPS for api.open-home.online — ONLY after DNS is correct.
# Run on the VPS as root. Does not mutate DNS.
set -euo pipefail

DOMAIN="api.open-home.online"
EXPECTED_IP="72.60.108.152"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root on the VPS"
  exit 1
fi

echo "==> DNS check (must be ${EXPECTED_IP})"
RESOLVED="$(dig +short "$DOMAIN" A @8.8.8.8 | head -1 | tr -d '[:space:]')"
AUTH="$(dig +short "$DOMAIN" A @ignacio.ns.cloudflare.com | head -1 | tr -d '[:space:]')"
echo "resolver@8.8.8.8: ${RESOLVED:-EMPTY}"
echo "authoritative@cloudflare: ${AUTH:-EMPTY}"

if [[ "$RESOLVED" != "$EXPECTED_IP" ]]; then
  echo "STOP: DNS not ready (expected $EXPECTED_IP). No certbot run."
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get install -y certbot python3-certbot-nginx >/dev/null
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect
nginx -t
systemctl reload nginx
# GET only — the API health handler does not implement HEAD.
# Avoid piping curl into head: pipefail would treat SIGPIPE as failure.
curl -fsS -D /tmp/openhome-health.hdr -o /tmp/openhome-health.body "https://${DOMAIN}/health"
head -n 12 /tmp/openhome-health.hdr
echo
cat /tmp/openhome-health.body
rm -f /tmp/openhome-health.hdr /tmp/openhome-health.body
echo
certbot renew --dry-run
echo "HTTPS_READY"

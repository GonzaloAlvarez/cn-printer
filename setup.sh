#!/usr/bin/env bash
# cn-printer — idempotent bring-up on printer.lan.
#
# Prerequisites (host): amun core, `amun docker`, `amun printer` (ipp-usb on
# :60000, avahi, /srv/scans, ufw holes) and the Kauket-managed .env:
#   kauket get printer.cn_printer_env
#   ./setup.sh
# Renders sane/airscan.conf and traefik-lan/dynamic.yml, fetches the step-ca
# root, checks the host contracts, builds the local images, brings the stack
# up and reports the queue. Re-run after `git pull`.
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-}" in
  -h|--help) sed -n '/^#!/d; /^[^#]/q; s/^# \{0,1\}//p' "$0"; exit 0 ;;
  "") ;;
  *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
esac

log()  { printf '\n\033[1;36m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }
env_get() { grep -E "^${1}=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

[[ -f .env ]] || die ".env not found. Run: kauket get printer.cn_printer_env"

ESCL_URL="$(env_get PRINTER_ESCL_URL)";   [[ -n "$ESCL_URL" ]]   || die "PRINTER_ESCL_URL empty in .env"
DEVICE_URI="$(env_get PRINTER_DEVICE_URI)"; [[ -n "$DEVICE_URI" ]] || die "PRINTER_DEVICE_URI empty in .env"
SCANS_DIR="$(env_get SCANS_DIR)";         SCANS_DIR="${SCANS_DIR:-/srv/scans}"
SCANS_UID="$(env_get SCANS_UID)";         SCANS_UID="${SCANS_UID:-1000}"
CUPS_LOG_DIR="$(env_get CUPS_LOG_DIR)";   CUPS_LOG_DIR="${CUPS_LOG_DIR:-./cups/log}"
# The LAN-edge keys have the same defaults in docker-compose.yml, so an older
# Kauket .env still works; refresh it (kauket get) to make them explicit.
SCAN_LAN_HOST="$(env_get SCAN_LAN_HOST)"; SCAN_LAN_HOST="${SCAN_LAN_HOST:-scan.print.lan}"
PKI_HOST="$(env_get PKI_HOST)";           PKI_HOST="${PKI_HOST:-pki.lan}"
for k in SCAN_LAN_HOST PKI_HOST PKI_IP SCAN_OCR_LANG GDRIVE_REMOTE_PATH; do
  [[ -n "$(env_get "$k")" ]] || warn "$k missing in .env — using the compose default (refresh: kauket get printer.cn_printer_env)"
done
[[ -n "$(env_get ADMIN_EMAIL)" ]]         || warn "ADMIN_EMAIL is empty — step-ca ACME registration will carry no contact"
[[ -n "$(env_get PRINTER_AUTHKEY)" ]]     || warn "PRINTER_AUTHKEY is empty — ts-printer will not join the tailnet"
[[ -n "$(env_get CUPS_ADMIN_PASSWORD)" ]] || warn "CUPS_ADMIN_PASSWORD is empty — the :631 admin pages will have no login"

# ── host contracts (amun printer) ───────────────────────────────────────────
for d in "$SCANS_DIR" "$SCANS_DIR/swis" "$SCANS_DIR/inbox" "$SCANS_DIR/processed"; do
  [[ -d "$d" ]] || die "$d missing — run: amun printer"
done
[[ "$(stat -c %u "$SCANS_DIR")" == "$SCANS_UID" ]] || warn "$SCANS_DIR is owned by uid $(stat -c %u "$SCANS_DIR"), .env says SCANS_UID=$SCANS_UID"
systemctl is-active --quiet avahi-daemon || warn "avahi-daemon is not active on the host — AirPrint discovery will be missing"
[[ -d /etc/avahi/services ]] || die "/etc/avahi/services missing — the cups entrypoint writes the AirPrint record there (amun printer installs avahi)"
if dpkg -s cups >/dev/null 2>&1; then die "a host CUPS is installed — cupsd must only run in this stack (amun printer removes it)"; fi
mkdir -p "$CUPS_LOG_DIR"

# ipp-usb reachability (the printer may legitimately be off: warn, don't die)
ippusb_hostport="$(printf '%s' "$DEVICE_URI" | sed -E 's#^[a-z]+://([^/]+)/.*#\1#')"
if curl -sf -m 5 -o /dev/null "http://${ippusb_hostport}/"; then
  log "ipp-usb answers at http://${ippusb_hostport}/ (printer attached)"
else
  warn "nothing answers at http://${ippusb_hostport}/ — printer off/unplugged? cups will keep retrying the queue"
fi

# ── step-ca root (traefik-lan trusts the ACME server with it) ────────────────
mkdir -p certs
if [[ ! -f certs/root_ca.crt ]]; then
  log "fetching the step-ca root from http://${PKI_HOST}/cert/ca.crt"
  curl -sSf -m 10 "http://${PKI_HOST}/cert/ca.crt" -o certs/root_ca.crt || die "could not fetch the step-ca root"
fi

# ── render the static sane-airscan device entry + the traefik dynamic config ─
sed "s#@PRINTER_ESCL_URL@#${ESCL_URL}#" sane/airscan.conf.tmpl > sane/airscan.conf
log "rendered sane/airscan.conf → ${ESCL_URL}"
# `>` rewrites the same inode, so a running traefik-lan picks the change up via its file watcher
sed "s#@SCAN_LAN_HOST@#${SCAN_LAN_HOST}#g" traefik-lan/dynamic.yml.tmpl > traefik-lan/dynamic.yml
log "rendered traefik-lan/dynamic.yml → https://${SCAN_LAN_HOST}"

# ── Google Drive remote (optional; Kauket-managed) ───────────────────────────
mkdir -p rclone
if [[ -f rclone/rclone.conf ]]; then
  [[ "$(stat -c %u rclone/rclone.conf)" == "$SCANS_UID" ]] || warn "rclone/rclone.conf is owned by uid $(stat -c %u rclone/rclone.conf); scanservjs (uid $SCANS_UID) must be able to read and refresh it"
else
  warn "rclone/rclone.conf missing — Google Drive uploads stay disabled until: kauket get printer.cn_printer_rclone_conf (mint it with scripts/gdrive-auth.sh on a machine with a browser)"
fi

# ── bring the stack up ───────────────────────────────────────────────────────
log "docker compose up -d --build --remove-orphans"
docker compose up -d --build --remove-orphans

log "waiting for cupsd (up to 2m)"
for i in $(seq 1 24); do
  if curl -sf -m 5 -o /dev/null http://127.0.0.1:631/; then log "cupsd reachable after ${i}x5s"; break; fi
  sleep 5
  [[ $i -eq 24 ]] && die "cupsd never answered on :631 — check 'docker compose logs cups'"
done

log "waiting for traefik-lan (up to 1m)"
for i in $(seq 1 12); do
  code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1/" || true)"
  if [[ "$code" =~ ^30[0-9]$ ]]; then log "traefik-lan redirects :80 → https (HTTP $code)"; break; fi
  sleep 5
  [[ $i -eq 12 ]] && warn "traefik-lan does not answer on :80 (got '$code') — check 'docker compose logs traefik-lan' and the ufw holes (amun printer)"
done

log "listeners"
ss -ltn 2>/dev/null | grep -E ':(80|443|631|8080|5520|9100|9329) ' || true

log "queue"
docker compose exec -T cups lpstat -v 2>/dev/null || warn "no queue yet (printer off?) — the cups container retries every 60s"

cat <<EOF2


===============================================================
cn-printer is up.

  Print (CUPS)   :  http://printer.lan:631      (AirPrint/IPP Everywhere on the LAN)
  Scan           :  https://${SCAN_LAN_HOST}     (scanservjs via traefik-lan, step-ca; also http://printer.lan:8080)
  Scan + upload  :  http://printer.lan:5520     (swis)
  Google Drive   :  $( [[ -f rclone/rclone.conf ]] && echo "enabled → $(env_get GDRIVE_REMOTE_PATH)" || echo "disabled (no rclone/rclone.conf)" )
  Tailnet        :  same ports on printer.ts.gn.al
  Metrics        :  :9100 node-exporter, :9329 cups-exporter (kaiser scrapes)

  Queue helper   :  docker compose exec cups printer-queue-setup --show ${DEVICE_URI}
  Then           :  amun docker        # installs the boot unit + reconcile cron
===============================================================
EOF2

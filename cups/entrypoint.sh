#!/bin/bash
# cn-printer/cups entrypoint: render cupsd.conf from env, seed /etc/cups on an
# empty volume, create the web-UI admin, start cupsd, then create the
# raster-only IPP queue (retrying in the background while the printer is off).
set -euo pipefail

: "${PRINTER_DEVICE_URI:=ipp://172.30.6.1:60000/ipp/print}"
: "${PRINTER_QUEUE:=Brother_MFC-L2750DW}"
: "${PRINTER_DESCRIPTION:=Brother MFC-L2750DW (USB via ipp-usb, raster-only)}"
: "${PRINTER_LOCATION:=printer.lan}"
: "${PRINTER_KEEP_FORMATS:=image/pwg-raster,image/urf}"
: "${PRINTER_WAIT_SECONDS:=60}"
: "${PRINTER_RETRY_SECONDS:=60}"
: "${CUPS_ALLOW:=10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10}"
: "${CUPS_BROWSING:=On}"
: "${CUPS_LOG_LEVEL:=warn}"
: "${CUPS_ADMIN_USER:=print}"
: "${CUPS_ADMIN_PASSWORD:=}"

log() { echo "[cn-printer/cups] $*"; }

# ── /etc/cups volume: seed from the image's pristine copy on first run ──────
if [ ! -f /etc/cups/cups-files.conf ]; then
  log "seeding empty /etc/cups from the image"
  cp -a /usr/local/share/cn-printer-etc-cups/. /etc/cups/
fi
mkdir -p /etc/cups/ppd /etc/cups/ssl /etc/cups/cn-printer /var/log/cups /var/spool/cups/tmp /run/cups /var/cache/cups
chown root:lp /etc/cups/ppd /etc/cups/ssl
chmod 0755 /etc/cups/ppd
chown root:lp /var/log/cups /var/spool/cups /var/spool/cups/tmp /var/cache/cups
chmod 0755 /var/log/cups

# ── cupsd.conf (config-as-code; cupsctl must never be run here) ─────────────
allow_lines=""
for cidr in $CUPS_ALLOW; do allow_lines+="  Allow ${cidr}"$'\n'; done
cat > /etc/cups/cupsd.conf <<CONF
# Rendered by cn-printer/cups/entrypoint.sh — edit docker-compose.yml/.env, not this file.
LogLevel ${CUPS_LOG_LEVEL}
PageLogFormat %p %u %j %T %P %C %{job-billing} %{job-originating-host-name} %{job-name} %{media} %{sides}
MaxLogSize 0
ErrorPolicy retry-job
# Listen on every interface of this netns: the tailscale sidecar publishes
# :631 on the LAN and owns the tailnet address.
Port 631
Listen /run/cups/cups.sock
ServerAlias *
# Share the queue over DNS-SD through the HOST avahi (D-Bus socket mounted)
# so phones/laptops see an AirPrint/IPP Everywhere printer.
Browsing ${CUPS_BROWSING}
BrowseLocalProtocols dnssd
# No _cups subtype: with it macOS enumerates the queue as dnssd://…/cups and
# treats it as a remote CUPS queue (Use: Generic PostScript, no AirPrint
# offered). _print + _universal make it an AirPrint / IPP Everywhere printer.
DNSSDSubTypes _print,_universal
DefaultAuthType Basic
WebInterface Yes
IdleExitTimeout 60
<Location />
  Order allow,deny
  Allow @LOCAL
${allow_lines}</Location>
<Location /admin>
  Order allow,deny
  Allow @LOCAL
${allow_lines}</Location>
<Location /admin/conf>
  AuthType Default
  Require user @SYSTEM
  Order allow,deny
  Allow @LOCAL
${allow_lines}</Location>
<Location /admin/log>
  AuthType Default
  Require user @SYSTEM
  Order allow,deny
  Allow @LOCAL
${allow_lines}</Location>
<Policy default>
  JobPrivateAccess default
  JobPrivateValues default
  SubscriptionPrivateAccess default
  SubscriptionPrivateValues default
  <Limit Create-Job Print-Job Print-URI Validate-Job>
    Order deny,allow
  </Limit>
  <Limit Send-Document Send-URI Hold-Job Release-Job Restart-Job Purge-Jobs Set-Job-Attributes Create-Job-Subscription Renew-Subscription Cancel-Subscription Get-Notifications Reprocess-Job Cancel-Current-Job Suspend-Current-Job Resume-Job Cancel-My-Jobs Close-Job CUPS-Move-Job>
    Require user @OWNER @SYSTEM
    Order deny,allow
  </Limit>
  <Limit CUPS-Get-Document>
    AuthType Default
    Require user @OWNER @SYSTEM
    Order deny,allow
  </Limit>
  <Limit CUPS-Add-Modify-Printer CUPS-Delete-Printer CUPS-Add-Modify-Class CUPS-Delete-Class CUPS-Set-Default CUPS-Get-Devices>
    AuthType Default
    Require user @SYSTEM
    Order deny,allow
  </Limit>
  <Limit Pause-Printer Resume-Printer Enable-Printer Disable-Printer Pause-Printer-After-Current-Job Hold-New-Jobs Release-Held-New-Jobs Deactivate-Printer Activate-Printer Restart-Printer Shutdown-Printer Startup-Printer Promote-Job Schedule-Job-After Cancel-Jobs CUPS-Accept-Jobs CUPS-Reject-Jobs>
    AuthType Default
    Require user @SYSTEM
    Order deny,allow
  </Limit>
  <Limit Cancel-Job CUPS-Authenticate-Job>
    Require user @OWNER @SYSTEM
    Order deny,allow
  </Limit>
  <Limit All>
    Order deny,allow
  </Limit>
</Policy>
<Policy authenticated>
  JobPrivateAccess default
  JobPrivateValues default
  SubscriptionPrivateAccess default
  SubscriptionPrivateValues default
  <Limit Create-Job Print-Job Print-URI Validate-Job>
    AuthType Default
    Order deny,allow
  </Limit>
  <Limit Send-Document Send-URI Hold-Job Release-Job Restart-Job Purge-Jobs Set-Job-Attributes Create-Job-Subscription Renew-Subscription Cancel-Subscription Get-Notifications Reprocess-Job Cancel-Current-Job Suspend-Current-Job Resume-Job Cancel-My-Jobs Close-Job CUPS-Move-Job CUPS-Get-Document>
    AuthType Default
    Require user @OWNER @SYSTEM
    Order deny,allow
  </Limit>
  <Limit CUPS-Add-Modify-Printer CUPS-Delete-Printer CUPS-Add-Modify-Class CUPS-Delete-Class CUPS-Set-Default>
    AuthType Default
    Require user @SYSTEM
    Order deny,allow
  </Limit>
  <Limit Pause-Printer Resume-Printer Enable-Printer Disable-Printer Pause-Printer-After-Current-Job Hold-New-Jobs Release-Held-New-Jobs Deactivate-Printer Activate-Printer Restart-Printer Shutdown-Printer Startup-Printer Promote-Job Schedule-Job-After Cancel-Jobs CUPS-Accept-Jobs CUPS-Reject-Jobs>
    AuthType Default
    Require user @SYSTEM
    Order deny,allow
  </Limit>
  <Limit Cancel-Job CUPS-Authenticate-Job>
    AuthType Default
    Require user @OWNER @SYSTEM
    Order deny,allow
  </Limit>
  <Limit All>
    Order deny,allow
  </Limit>
</Policy>
CONF
chown root:lp /etc/cups/cupsd.conf && chmod 0640 /etc/cups/cupsd.conf

# ── web-UI admin (lpadmin == SystemGroup on Debian) ─────────────────────────
if [ -n "$CUPS_ADMIN_PASSWORD" ]; then
  if ! id -u "$CUPS_ADMIN_USER" >/dev/null 2>&1; then
    useradd -r -M -s /usr/sbin/nologin -G lpadmin "$CUPS_ADMIN_USER"
  fi
  usermod -aG lpadmin "$CUPS_ADMIN_USER"
  echo "${CUPS_ADMIN_USER}:${CUPS_ADMIN_PASSWORD}" | chpasswd
fi

# ── cupsd ───────────────────────────────────────────────────────────────────
cupsd -f &
CUPSD_PID=$!
trap 'kill -TERM "$CUPSD_PID" 2>/dev/null || true; wait "$CUPSD_PID" 2>/dev/null || true' TERM INT
for _ in $(seq 1 30); do lpstat -r >/dev/null 2>&1 && break; sleep 1; done
lpstat -r >/dev/null 2>&1 || { log "cupsd did not come up"; exit 1; }
log "cupsd up (Browsing ${CUPS_BROWSING}; allow: ${CUPS_ALLOW})"

# ── raster-only queue; keep retrying in the background if the printer is off ─
queue_args=(--name "$PRINTER_QUEUE" --description "$PRINTER_DESCRIPTION" --location "$PRINTER_LOCATION"
            --keep-formats "$PRINTER_KEEP_FORMATS" --default --shared --state-dir /etc/cups/cn-printer)
set +e
out="$(printer-queue-setup --wait "$PRINTER_WAIT_SECONDS" "${queue_args[@]}" "$PRINTER_DEVICE_URI" 2>&1)"
rc=$?
set -e
case "$rc" in
  0) log "queue ${PRINTER_QUEUE}: ${out}" ;;
  3) log "WARNING: ${out}"
     log "WARNING: no queue yet — retrying every ${PRINTER_RETRY_SECONDS}s; or run: docker compose exec cups printer-queue-setup --name ${PRINTER_QUEUE} ${PRINTER_DEVICE_URI}"
     ( while sleep "$PRINTER_RETRY_SECONDS"; do
         if o="$(printer-queue-setup --wait 0 "${queue_args[@]}" "$PRINTER_DEVICE_URI" 2>&1)"; then log "queue ${PRINTER_QUEUE}: ${o}"; break; fi
       done ) &
     ;;
  *) log "ERROR: printer-queue-setup failed (rc=${rc}): ${out}" ;;
esac

wait "$CUPSD_PID"

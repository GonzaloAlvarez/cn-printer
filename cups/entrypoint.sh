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
: "${CUPS_BROWSING:=Off}"
: "${PRINTER_ADVERTISE:=yes}"
: "${PRINTER_ADVERTISE_NAME:=Brother MFC-L2750DW}"
: "${PRINTER_ADVERTISE_HOST:=printer.lan}"
: "${AVAHI_SERVICES_DIR:=/etc/avahi/services}"
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
# cupsd's own DNS-SD stays off: CUPS 2.4 hard-codes the _cups subtype, which
# makes macOS treat the queue as a remote CUPS queue (Generic PostScript, no
# AirPrint). The AirPrint record is published instead through the HOST avahi
# from a static service file this entrypoint writes (see advertise_printer).
Browsing ${CUPS_BROWSING}
BrowseLocalProtocols dnssd
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
trap 'remove_advert; kill -TERM "$CUPSD_PID" 2>/dev/null || true; wait "$CUPSD_PID" 2>/dev/null || true' TERM INT
for _ in $(seq 1 30); do lpstat -r >/dev/null 2>&1 && break; sleep 1; done
lpstat -r >/dev/null 2>&1 || { log "cupsd did not come up"; exit 1; }
log "cupsd up (Browsing ${CUPS_BROWSING}; allow: ${CUPS_ALLOW})"

# ── AirPrint record via the host avahi (static service file) ────────────────
# Written from live attributes: the queue's printer-uuid (CUPS) and the
# device's urf-supported / color / sides (through ipp-usb). _universal is the
# AirPrint subtype; no _cups, so macOS/iOS see a plain AirPrint printer and
# offer "Secure AirPrint" (TLS=1.2: cupsd answers IPPS on the same port).
attr() { # uri attribute → value (first match)
  ipptool -tv -T 8 "$1" /usr/share/cups/ipptool/get-printer-attributes.test 2>/dev/null \
    | sed -n "s/^ *$2 ([^)]*) = //p" | head -1
}
advertise_printer() {
  [ "$PRINTER_ADVERTISE" = "yes" ] || return 0
  [ -d "$AVAHI_SERVICES_DIR" ] || { log "no $AVAHI_SERVICES_DIR mounted; skipping the AirPrint record"; return 0; }
  local q_uri="ipp://localhost:631/printers/${PRINTER_QUEUE}" uuid urf color sides duplex=F colour=F
  uuid="$(attr "$q_uri" printer-uuid | sed 's/^urn:uuid://')"
  urf="$(attr "$PRINTER_DEVICE_URI" urf-supported)"
  color="$(attr "$PRINTER_DEVICE_URI" color-supported)"
  sides="$(attr "$PRINTER_DEVICE_URI" sides-supported)"
  [ -z "$urf" ] && urf="$(attr "$q_uri" urf-supported)"
  [ -z "$urf" ] && urf="V1.4,CP1,W8,RS600,DM1"
  case "$sides" in *two-sided*) duplex=T ;; esac
  [ "$color" = "true" ] && colour=T
  local f="$AVAHI_SERVICES_DIR/cn-printer-${PRINTER_QUEUE}.service" tmp="$AVAHI_SERVICES_DIR/.cn-printer-${PRINTER_QUEUE}.service.tmp"
  {
    echo '<?xml version="1.0" standalone="no"?>'
    echo '<!DOCTYPE service-group SYSTEM "avahi-service.dtd">'
    echo '<!-- written by cn-printer/cups/entrypoint.sh — AirPrint record for the CUPS queue -->'
    echo '<service-group>'
    echo "  <name replace-wildcards=\"yes\">${PRINTER_ADVERTISE_NAME} @ %h</name>"
    for svc in ipp ipps; do
      echo "  <service>"
      echo "    <type>_${svc}._tcp</type>"
      echo "    <subtype>_universal._sub._${svc}._tcp</subtype>"
      echo "    <port>631</port>"
      echo "    <txt-record>txtvers=1</txt-record>"
      echo "    <txt-record>qtotal=1</txt-record>"
      echo "    <txt-record>rp=printers/${PRINTER_QUEUE}</txt-record>"
      echo "    <txt-record>ty=${PRINTER_ADVERTISE_NAME}</txt-record>"
      echo "    <txt-record>product=(${PRINTER_ADVERTISE_NAME})</txt-record>"
      echo "    <txt-record>note=${PRINTER_LOCATION}</txt-record>"
      echo "    <txt-record>adminurl=http://${PRINTER_ADVERTISE_HOST}:631/printers/${PRINTER_QUEUE}</txt-record>"
      echo "    <txt-record>pdl=application/pdf,image/urf,image/pwg-raster,image/jpeg</txt-record>"
      echo "    <txt-record>URF=${urf}</txt-record>"
      echo "    <txt-record>TLS=1.2</txt-record>"
      echo "    <txt-record>Color=${colour}</txt-record>"
      echo "    <txt-record>Duplex=${duplex}</txt-record>"
      echo "    <txt-record>air=none</txt-record>"
      [ -n "$uuid" ] && echo "    <txt-record>UUID=${uuid}</txt-record>"
      echo "    <txt-record>priority=50</txt-record>"
      echo "  </service>"
    done
    echo '</service-group>'
  } > "$tmp" && mv -f "$tmp" "$f"
  log "AirPrint record written: $f (URF=${urf}; uuid=${uuid:-none})"
}
remove_advert() { rm -f "$AVAHI_SERVICES_DIR/cn-printer-${PRINTER_QUEUE}.service" 2>/dev/null || true; }

# ── raster-only queue; keep retrying in the background if the printer is off ─
queue_args=(--name "$PRINTER_QUEUE" --description "$PRINTER_DESCRIPTION" --location "$PRINTER_LOCATION"
            --keep-formats "$PRINTER_KEEP_FORMATS" --default --shared --state-dir /etc/cups/cn-printer)
set +e
out="$(printer-queue-setup --wait "$PRINTER_WAIT_SECONDS" "${queue_args[@]}" "$PRINTER_DEVICE_URI" 2>&1)"
rc=$?
set -e
case "$rc" in
  0) log "queue ${PRINTER_QUEUE}: ${out}"; advertise_printer ;;
  3) log "WARNING: ${out}"
     log "WARNING: no queue yet — retrying every ${PRINTER_RETRY_SECONDS}s; or run: docker compose exec cups printer-queue-setup --name ${PRINTER_QUEUE} ${PRINTER_DEVICE_URI}"
     remove_advert
     ( while sleep "$PRINTER_RETRY_SECONDS"; do
         if o="$(printer-queue-setup --wait 0 "${queue_args[@]}" "$PRINTER_DEVICE_URI" 2>&1)"; then log "queue ${PRINTER_QUEUE}: ${o}"; advertise_printer; break; fi
       done ) &
     ;;
  *) log "ERROR: printer-queue-setup failed (rc=${rc}): ${out}" ;;
esac

wait "$CUPSD_PID"

# cn-printer

USB print + scan server for the **Brother MFC-L2750DW** on `printer.lan` (Raspberry Pi 4, WiFi, OS on a USB SSD). A Docker Compose stack in the homelab "sidecar" shape: a Tailscale sidecar owns the network namespace, CUPS and the two scan UIs join it, a host-network Traefik serves the scan UI as **`https://scan.print.lan`** with a step-ca certificate, and host-network exporters report to kaiser. Scans default to **PDF** (searchable-PDF variants via tesseract) and can go **straight to Google Drive**.

Companion host plugin: [amun-printer](https://github.com/GonzaloAlvarez/amun-printer) (ipp-usb on the host, usblp blacklist, persistent journal, watchdog, memory cgroup, `/srv/scans`, ufw holes incl. 80/443).

```
printer.lan (host)   ipp-usb :60000 on all interfaces (IPP + eSCL)   avahi-daemon   ufw: 22 · 80/443 · 9100/9329←kaiser · 60000←bridges
├─ traefik-lan (host net) :80 → :443   https://scan.print.lan → 127.0.0.1:8080   step-ca ACME (HTTP-01 via pfSense)
└─ cn-printer (bridge 172.30.6.0/24, gateway 172.30.6.1)
   ts-printer   tailscale sidecar `printer` (tag:svc); publishes 631 8080 5520 on the LAN
   ├─ cups      own trixie image · queue Brother_MFC-L2750DW → ipp://172.30.6.1:60000/ipp/print · raster-only PPD
   ├─ scanservjs  own image (sbs20/scanservjs:v3.3.0 + rclone) · eSCL via sane-airscan → http://172.30.6.1:60000/eSCL
   │             PDF-first pipelines · OCR (tesseract) · "→ Google Drive" pipelines + Files-tab action · output /srv/scans
   └─ swis      own image · scan + upload-to-print (lp → CUPS default queue) · output /srv/scans/swis
   node-exporter-lan :9100 · cups-exporter :9329 · promtail-lan → kaiser Loki · watchtower · backup (offen)
```

## Why it is built this way

- **Everything goes through ipp-usb.** The Brother exposes IPP (print) and eSCL (scan) over one USB interface. ipp-usb multiplexes both; a classic CUPS `usb://` queue would claim the interface and starve the scanner (that is exactly how plane.lan lost scanning in July 2026). There is **no `usb://` anywhere** in this stack.
- **CUPS rasterizes every job.** The queue's IPP Everywhere PPD is generated at start by `cups/printer-queue-setup`, which keeps only the `*cupsFilter2` conversions to `image/pwg-raster` and `image/urf` and strips the `application/pdf`, `image/jpeg` and PostScript passthroughs. The printer's own PDF/PostScript interpreter — the reason "most PDFs fail" — never sees a document again.
- **AirPrint from a container.** cupsd's own DNS-SD stays off: CUPS 2.4 hard-codes the `_cups` subtype on shared queues, and macOS then enumerates the printer as `dnssd://…/cups?uuid=`, treats it as a remote CUPS queue and offers only "Generic PostScript Printer" (`DNSSDSubTypes` is a 2.5 directive, rejected by 2.4). Instead the cups entrypoint writes a static avahi service file into the host's `/etc/avahi/services/` (bind-mounted) from live attributes: the queue's `printer-uuid`, the Brother's real `urf-supported`, colour and duplex, `_universal` subtype on `_ipp` and `_ipps`, `TLS=1.2`, `air=none`. The host avahi announces it; phones and laptops on the WiFi VLAN see a plain AirPrint printer (macOS: "Secure AirPrint") and talk to the host's `:631`, which the sidecar publishes. The record is removed when the container stops or the printer goes away.
- **The LAN edge is a host-network Traefik** (`traefik-lan`, the cn-nvr pattern): it owns the Pi's 80/443, redirects HTTP to HTTPS and routes `Host: scan.print.lan` to the port the sidecar publishes. Certificates come from step-ca over ACME HTTP-01, which pki validates **through pfSense** — so the name needs a Technitium record *and* a pfSense Host Override. It runs on the host network (not in the sidecar's namespace) so it can `extra_hosts` pki and survive sidecar restarts; ufw must admit 80/443 (amun-printer does).
- **Loopback is per-netns.** The containers reach ipp-usb on the compose bridge gateway `172.30.6.1`, so the host plugin runs ipp-usb with `interface = all` and ufw admits `:60000` only from `172.16.0.0/12`.
- **scanservjs is our own image on the upstream tag.** `scanservjs/Dockerfile` adds a pinned rclone (GitHub release, checksum-verified; trixie's apt rclone is the 2022 1.60 line) to `sbs20/scanservjs:v3.3.0`, which already ships tesseract 5.5 with `eng`/`spa`/… data. `scanservjs/config.local.js` makes "PDF (JPG | high-quality)" the default pipeline, keeps the OCR pipelines, adds two "→ Google Drive" pipelines (automatic upload when the scan finishes) and a Files-tab action "Upload to Google Drive". The Drive remote is an OAuth client with the **`drive.file`** scope only: a leaked token could touch files rclone created, nothing else.
- **swis on Python 3.12 with uvicorn 0.29.** swis 0.6.1 pins pydantic 1.x and `websockets~=9.1` but lets uvicorn float to 0.54, whose websockets integration needs websockets ≥ 13 and crashes at start; the image pins uvicorn 0.29.0 and sidesteps trixie's Python 3.13. swis has no device/queue selection: it scans the SANE **default** device (the one static airscan entry) and prints to the CUPS **default** queue (the helper sets it).
- **One Watchtower per host**, local builds opted out; the scanservjs upstream tag and rclone are pinned in `scanservjs/Dockerfile` on purpose (its `config.local.js` API changes between majors).

## Install (printer.lan)

```bash
bash <(curl -fsSL https://go.gn.al/amun)        # amun core
amun docker                                     # engine + resiliency
amun printer                                    # host prerequisites (one reboot: memory cgroup); ufw 80/443, 9100/9329, 60000
git clone https://github.com/GonzaloAlvarez/cn-printer ~/cn-printer && cd ~/cn-printer
kauket request printer                          # first time; `kauket approve` on the Mac
kauket get printer.cn_printer_env               # → .env (0600)
kauket get printer.cn_printer_rclone_conf       # → rclone/rclone.conf (0600) — optional until Google Drive is wanted
./setup.sh                                      # step-ca root, sane/airscan.conf, traefik-lan/dynamic.yml, build, up -d
amun docker                                     # boot unit docker-compose@cn-printer + reconcile cron
```

Names (once): `scan.print.lan → 10.2.0.30` in `cn-dnsdhcpd/dns/records.tsv` (`./seed-dns.sh` on pki) **and** in `cn-home`'s `LAN_HOSTS` (`./deploy` programs the pfSense Host Override). Without the pfSense side step-ca's HTTP-01 fails and Traefik serves its default self-signed certificate.

`.env` keys are documented in [`.env.example`](.env.example): the Headscale pre-auth key (`headscale preauthkeys create --user 2 --tags tag:svc --expiration 1h` on the VPS), the CUPS admin password, the LAN edge (`SCAN_LAN_HOST`, `PKI_HOST`/`PKI_IP`, `ADMIN_EMAIL`), scan defaults (`SCAN_OCR_LANG`, `GDRIVE_REMOTE_PATH`), the shared backup/SMTP credentials, and the device URIs (keep them in sync with the IPAM block in `docker-compose.yml`).

### URLs

| What | LAN | Tailnet |
|---|---|---|
| Scan (scanservjs) | **`https://scan.print.lan`** (step-ca) · `http://printer.lan:8080` | `http://printer.ts.gn.al:8080` |
| Print (CUPS web UI, IPP) | `http://printer.lan:631` · `ipp://printer.lan:631/printers/Brother_MFC-L2750DW` | `http://printer.ts.gn.al:631` |
| Scan + upload-to-print (swis) | `http://printer.lan:5520` | same port (its front-end calls `printer.lan`, see gotchas) |
| Metrics (kaiser scrapes) | `10.2.0.30:9100`, `10.2.0.30:9329` | — |

Printing needs no login. The `:631` admin pages ask for `CUPS_ADMIN_USER` / `CUPS_ADMIN_PASSWORD`. AirPrint/Mopria discovery works on the WiFi VLAN only (mDNS does not cross VLANs or the tailnet); elsewhere add the printer by URL. `scan.print.lan` resolves on the LAN only.

## Scanning: PDF, OCR, Google Drive

- **Default = PDF.** The pipeline dropdown opens on `PDF (JPG | high quality)`; the other upstream outputs (JPG/PNG/TIF, OCR text) are still there. `OCR | PDF (JPG | high quality)` produces a **searchable** PDF (tesseract, languages from `SCAN_OCR_LANG`, default `eng+spa`); on the Pi 4 count ~10–20 s per page at 300 dpi.
- **Multi-page documents:** source *ADF* + batch *auto* feeds the whole stack into one PDF; on the flatbed use batch *manual* and press *Next* per page.
- **To Google Drive:** pick `PDF → Google Drive` or `PDF (OCR) → Google Drive` — the file is written to `/srv/scans` as usual and then uploaded to `GDRIVE_REMOTE_PATH` (`gdrive:Scans`, i.e. a `Scans` folder at the top of My Drive) in the background. Anything already scanned: *Files* tab → select → *Upload to Google Drive*. A failed background upload leaves `<scan>.gdrive-FAILED.txt` next to the scan (visible in *Files*) with the rclone error; the Files-tab action reports errors in the UI directly.
- **Drive credentials** (`rclone/rclone.conf`, Kauket `printer.cn_printer_rclone_conf`) are minted on a machine with a browser by [`scripts/gdrive-auth.sh`](scripts/gdrive-auth.sh) — read its header: it needs your own Google Cloud OAuth client (Desktop app, scope `drive.file`, consent screen **published**; an app left "in testing" gets tokens that expire after 7 days), opens the login, verifies the token against Drive, and hands the file to Kauket (`--kauket`). On the Pi: `kauket get printer.cn_printer_rclone_conf` — no container restart needed. To revoke: delete the OAuth client in Google Cloud (or the app under myaccount.google.com → Security → Third-party access) and `kauket remove` the secret.
- With `drive.file`, rclone only sees files it created: `rclone lsd gdrive:` shows just `Scans`, and an existing folder of yours with the same name is *not* reused (Drive allows duplicates). Rename or merge in Drive if that matters.

## Operations

```bash
docker compose ps; docker compose logs --tail=50 cups
docker compose exec cups lpstat -v -p -d                     # queue, state, default
docker compose exec cups printer-queue-setup --show ipp://172.30.6.1:60000/ipp/print   # the raster-only PPD
docker compose exec cups printer-queue-setup --name Brother_MFC-L2750DW ipp://172.30.6.1:60000/ipp/print   # re-create after a printer swap
docker compose exec cups ipptool -tv ipp://172.30.6.1:60000/ipp/print get-printer-attributes.test | grep -E 'document-format-supported|marker|state-reasons'
curl -s http://printer.lan:8080/api/v1/context | python3 -m json.tool | head      # scanservjs devices + pipelines
docker compose logs --tail=20 traefik-lan                    # ACME / routing
openssl s_client -connect scan.print.lan:443 -servername scan.print.lan </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates   # step-ca leaf?
docker compose up -d --force-recreate --no-deps traefik-lan  # after a git pull that changed traefik-lan/dynamic.yml.tmpl (single-file mount)
docker compose exec scanservjs rclone --config /etc/rclone/rclone.conf lsd gdrive:   # Drive token still valid?
sudo systemctl restart docker-compose@cn-printer             # whole-stack restart through the boot unit
```

State: `cups_etc` volume (printers.conf, PPD, the helper's state copy), `traefik_lan_acme` (the step-ca leaf — disposable, re-issued on loss) and `/srv/scans` on the host. Scans and the CUPS config are backed up weekly by offen (Sun 01:30) to `s3://cloudnet-lab-storage/printer/` and the raidnas WebDAV share `/printer/`; the queue itself is reproducible from `.env` (`setup.sh` re-creates it). Restore = `kauket get` (both secrets) + `./setup.sh`, then untar scans into `/srv/scans`.

Trigger a backup by hand: `docker compose exec backup /usr/bin/backup`.

## Observability (kaiser, cn-observability)

- Prometheus jobs `printer-node` (`:9100`, `host=printer`) and `printer-cups` (`:9329`, `stack=cn-printer`); blackbox `blackbox-https` probes `https://scan.print.lan/api/v1/system`, `blackbox-http` probes `:631/` and `:5520/app/`.
- Dashboards `printer-overview` (host: CPU/mem/disk/net + SoC temperature + under-voltage/throttle from `rpi.prom`) and `cups` (queue state, jobs, state reasons, toner, probes, pages per day/user from `page_log` in Loki). Generated by `grafana/build-dashboards.py` — edit the generator, not the JSON.
- Alerts: `PiUndervoltage`, `PiThrottled`, `CupsExporterDown`, `PrinterStopped`, `PrinterNeedsAttention`, `TonerLow`, `PrintQueueStuck`, plus the generic host group.
- promtail ships container logs (incl. `[gdrive] uploaded/FAILED` lines from scanservjs and Traefik's ACME log), the journal and CUPS `page_log`/`error_log` (`CUPS_LOG_DIR` bind) to `10.1.1.140:3100`; kaiser's ufw must admit `10.2.0.30` on 3100.

Deliberate omissions: no Consul/traefik-lab route (plain HTTP on the tailnet by port; `scan.print.lan` is LAN-only), no Authentik gate (printing is a native-client protocol), no cAdvisor.

## Testing

`./test` builds the four local images and brings the stack up with **virtual devices** — `ippeveprinter` from `cups/Dockerfile.ippeve` (a fake IPP Everywhere printer that advertises PDF *and* raster, so the test proves CUPS still sends raster; it carries its own D-Bus + avahi because CUPS 2.4's ippeveprinter initialises DNS-SD even with `-r off`), the SANE `test` backend (a synthetic colour flatbed scanservjs lists as `test:0`), an rclone `local` remote standing in for Google Drive (`.test/drive-out`), and traefik-lan inside the netns on plain HTTP — on loopback ports `16310/18080/15520/19329/18081` (a Mac's own cupsd keeps `:631`). It then proves:

- queue created on the virtual printer, default, shared; `printer-queue-setup` re-run prints `UNCHANGED`; the PPD keeps `image/pwg-raster|urf` and has **no** `application/pdf`/`image/jpeg` passthrough;
- `lp` of a two-page PDF → the spooled document is PWG/URF raster (`EVIDENCE: print → …`);
- scanservjs REST scan to PNG, PDF and **OCR PDF**, downloaded and type-checked; **PDF is the default pipeline**;
- `PDF → Google Drive` lands the file on the stand-in remote with no `.gdrive-FAILED` marker; the Files-tab action uploads an existing scan; the action without `rclone.conf` fails with HTTP 500 and the server stays up;
- traefik-lan routes `Host: scan.print.lan` to scanservjs and 404s an unknown host;
- swis up, upload (stored under swis's timestamped name) + `/print/execute` lands a second raster job; file ownership uid 1000 (Linux only);
- cups-exporter reports `cups_up 1`, printer idle, device state.

```bash
./test            # on the Mac (Docker Desktop) or any Linux Docker host
./test --keep     # leave the stack up: http://127.0.0.1:18080 (scan UI), :16310 (CUPS), :15520 (swis), :18081 (edge, send Host: scan.print.lan)
```

On macOS Docker Desktop a long scan request is occasionally dropped by the port forwarder; the scan step retries once (a real failure still fails both attempts). Linux runs (kora VM, rpid11, the Pi) have never shown it.

Inside a kora VM the same script is run by `amun-printer/test` (`amun/test --post tests/vm-e2e.sh`), after `amun`, `amun docker` and `amun printer` have converged. Static checks: `docker compose -f docker-compose.yml -f docker-compose.test.yml --env-file .env.test config --quiet`, `shellcheck cups/* swis/entrypoint.sh setup.sh test scripts/*.sh`, `node --check scanservjs/config.local.js`, `python3 -m py_compile cups-exporter/exporter.py`.

## Gotchas

- **Never run `cupsctl` in the cups container** — it rewrites `cupsd.conf`, which the entrypoint renders from env on every start.
- **cups-browsed must stay out** (not installed in the image, purged on the host): on trixie it would auto-create a second queue for the ipp-usb printer with PDF passthrough.
- **`PRINTER_DEVICE_URI`/`PRINTER_ESCL_URL` hardcode the bridge gateway** `172.30.6.1`; if the IPAM block changes, change them too and re-run `./setup.sh`.
- **`traefik-lan/dynamic.yml` is a single-file bind mount**: `setup.sh` re-renders it in place (same inode, Traefik's watcher sees it), but a `git pull` that changes the template needs `docker compose up -d --force-recreate --no-deps traefik-lan`. A `TRAEFIK DEFAULT CERT` in the browser means HTTP-01 failed: check the pfSense Host Override (cn-home `LAN_HOSTS`), the Technitium record and `ufw status` (80/443 from Anywhere).
- **The host-network Traefik gets an empty `resolv.conf`** on this Docker version — the only name it dials, `PKI_HOST`, is pinned through `extra_hosts` with `PKI_IP`.
- **scanservjs runs the pipeline `afterAction` without awaiting it**: a rejecting action would be an unhandled promise rejection and kill the server. That is why the pipelines use the "(background)" action, which catches and writes the `.gdrive-FAILED.txt` marker, while the Files-tab action throws so the UI can show the error.
- **`rclone/rclone.conf` is mounted read-write on purpose** and must be readable by uid 1000 (`kauket get` as gonzalo gives exactly that): rclone rewrites the `token` line when it refreshes the access token. The refresh token itself does not change, so the Kauket copy stays valid; re-mint only if Google revokes it (7-day expiry = the OAuth app was left "in testing").
- **`rclone authorize` wants its JSON blob in URL-safe, unpadded base64** — standard base64 fails with `decode simple map: illegal base64 data`. `scripts/gdrive-auth.sh` does this right; the blob is what lets us request `drive.file` instead of full `drive`.
- **swis front-end host** is baked into its `config.json` as `SWIS_PUBLIC_HOST` (`printer.lan`). From the tailnet the page loads but its API calls go to `printer.lan`; use scanservjs there, or map `printer.lan` locally.
- **scanservjs and swis run as uid 1000** so scans and uploads land as `gonzalo` for the future Samba share and Stirling-PDF (`/srv/scans/inbox` as a watched folder). `scanservjs-init` (a one-shot, named to match amun-docker's reconcile skip list) hands the data volume to that uid; swis writes its front-end config as root and drops privileges with `setpriv`. On Docker Desktop (macOS) bind-mount ownership is virtualized, so `./test` only asserts ownership on Linux.
- **The printer may be off at boot**: cups starts anyway (healthcheck is the web UI, not the queue) and retries the queue every 60 s; `lpstat -v` empty until then.
- **AirPrint record**: `/etc/avahi/services/cn-printer-Brother_MFC-L2750DW.service` on the host, written by the cups entrypoint once the queue exists (`PRINTER_ADVERTISE_NAME`/`_HOST` in `.env`). If `dns-sd -B _ipp._tcp,_universal local.` on a Mac shows nothing, check `avahi-daemon` on the host and that `/etc/avahi/services` is bind-mounted; a Mac that still sees `dnssd://…/cups` is showing a cached record (`sudo killall -HUP mDNSResponder`).
- **Pi 4 power**: the SSD and the printer share the Pi's 1.2 A USB budget; under-voltage shows up as `rpi_undervoltage_*` on kaiser. Use the official 5.1 V / 3 A supply and a powered hub for the SSD.
- Promtail is EOL upstream (2026-03); kept here for fleet consistency until the fleet moves to Alloy.

## Next on this box (compatible by design)

Stirling-PDF (`stirlingtools/stirling-pdf:3.1.0`, `JAVA_TOOL_OPTIONS=-Xmx1g`, `mem_limit` works because amun-printer enables the memory cgroup) watching `/srv/scans/inbox` → `/srv/scans/processed`, published through `traefik-lan` as `pdf.print.lan` (one more router + the two DNS records); a Samba share of `/srv/scans` (`ghcr.io/servercontainers/samba`, host network, `NETBIOS_DISABLE=1`, wsdd2, host avahi `smb.xml`). Either joins this project or a sibling stack with `boot.after: [cn-printer]`.

## License

GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007 — Copyright (c) 2026 Gonzalo Alvarez

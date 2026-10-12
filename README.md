# cn-printer

USB print + scan server for the **Brother MFC-L2750DW** on `printer.lan` (Raspberry Pi 4, WiFi, OS on a USB SSD). A Docker Compose stack in the homelab "sidecar" shape: a Tailscale sidecar owns the network namespace, CUPS and the two scan UIs join it, host-network exporters report to kaiser.

Companion host plugin: [amun-printer](https://github.com/GonzaloAlvarez/amun-printer) (ipp-usb on the host, usblp blacklist, persistent journal, watchdog, memory cgroup, `/srv/scans`, ufw holes).

```
printer.lan (host)   ipp-usb :60000 on all interfaces (IPP + eSCL)   avahi-daemon (D-Bus)
└─ cn-printer (bridge 172.30.6.0/24, gateway 172.30.6.1)
   ts-printer   tailscale sidecar `printer` (tag:svc); publishes 631 8080 5520 on the LAN
   ├─ cups      own trixie image · queue Brother_MFC-L2750DW → ipp://172.30.6.1:60000/ipp/print · raster-only PPD
   ├─ scanservjs  sbs20/scanservjs:v3.3.0 · eSCL via sane-airscan → http://172.30.6.1:60000/eSCL · output /srv/scans
   └─ swis      own image · scan + upload-to-print (lp → CUPS default queue) · output /srv/scans/swis
   node-exporter-lan :9100 · cups-exporter :9329 · promtail-lan → kaiser Loki · watchtower · backup (offen)
```

## Why it is built this way

- **Everything goes through ipp-usb.** The Brother exposes IPP (print) and eSCL (scan) over one USB interface. ipp-usb multiplexes both; a classic CUPS `usb://` queue would claim the interface and starve the scanner (that is exactly how plane.lan lost scanning in July 2026). There is **no `usb://` anywhere** in this stack.
- **CUPS rasterizes every job.** The queue's IPP Everywhere PPD is generated at start by `cups/printer-queue-setup`, which keeps only the `*cupsFilter2` conversions to `image/pwg-raster` and `image/urf` and strips the `application/pdf`, `image/jpeg` and PostScript passthroughs. The printer's own PDF/PostScript interpreter — the reason "most PDFs fail" — never sees a document again.
- **AirPrint from a container.** cupsd's own DNS-SD stays off: CUPS 2.4 hard-codes the `_cups` subtype on shared queues, and macOS then enumerates the printer as `dnssd://…/cups?uuid=`, treats it as a remote CUPS queue and offers only "Generic PostScript Printer" (`DNSSDSubTypes` is a 2.5 directive, rejected by 2.4). Instead the cups entrypoint writes a static avahi service file into the host's `/etc/avahi/services/` (bind-mounted) from live attributes: the queue's `printer-uuid`, the Brother's real `urf-supported`, colour and duplex, `_universal` subtype on `_ipp` and `_ipps`, `TLS=1.2`, `air=none`. The host avahi announces it; phones and laptops on the WiFi VLAN see a plain AirPrint printer (macOS: "Secure AirPrint") and talk to the host's `:631`, which the sidecar publishes. The record is removed when the container stops or the printer goes away.
- **Loopback is per-netns.** The containers reach ipp-usb on the compose bridge gateway `172.30.6.1`, so the host plugin runs ipp-usb with `interface = all` and ufw admits `:60000` only from `172.16.0.0/12`.
- **swis on Python 3.12 with uvicorn 0.29.** swis 0.6.1 pins pydantic 1.x and `websockets~=9.1` but lets uvicorn float to 0.54, whose websockets integration needs websockets ≥ 13 and crashes at start; the image pins uvicorn 0.29.0 and sidesteps trixie's Python 3.13. swis has no device/queue selection: it scans the SANE **default** device (the one static airscan entry) and prints to the CUPS **default** queue (the helper sets it).
- **One Watchtower per host**, local builds opted out; scanservjs pinned to a tag on purpose (its `config.local.js` API changes between majors).

## Install (printer.lan)

```bash
bash <(curl -fsSL https://go.gn.al/amun)        # amun core
amun docker                                     # engine + resiliency
amun printer                                    # host prerequisites (one reboot: memory cgroup)
git clone https://github.com/GonzaloAlvarez/cn-printer ~/cn-printer && cd ~/cn-printer
kauket request printer                          # first time; `kauket approve` on the Mac
kauket get printer.cn_printer_env               # → .env (0600)
./setup.sh                                      # renders sane/airscan.conf, builds, up -d, reports the queue
amun docker                                     # boot unit docker-compose@cn-printer + reconcile cron
```

`.env` keys are documented in [`.env.example`](.env.example): the Headscale pre-auth key (`headscale preauthkeys create --user 2 --tags tag:svc --expiration 1h` on the VPS), the CUPS admin password, the shared backup/SMTP credentials, and the device URIs (keep them in sync with the IPAM block in `docker-compose.yml`).

### URLs

| What | LAN | Tailnet |
|---|---|---|
| Print (CUPS web UI, IPP) | `http://printer.lan:631` · `ipp://printer.lan:631/printers/Brother_MFC-L2750DW` | `http://printer.ts.gn.al:631` |
| Scan (scanservjs) | `http://printer.lan:8080` | `http://printer.ts.gn.al:8080` |
| Scan + upload-to-print (swis) | `http://printer.lan:5520` | same port (its front-end calls `printer.lan`, see gotchas) |
| Metrics (kaiser scrapes) | `10.2.0.30:9100`, `10.2.0.30:9329` | — |

Printing needs no login. The `:631` admin pages ask for `CUPS_ADMIN_USER` / `CUPS_ADMIN_PASSWORD`. AirPrint/Mopria discovery works on the WiFi VLAN only (mDNS does not cross VLANs or the tailnet); elsewhere add the printer by URL.

## Operations

```bash
docker compose ps; docker compose logs --tail=50 cups
docker compose exec cups lpstat -v -p -d                     # queue, state, default
docker compose exec cups printer-queue-setup --show ipp://172.30.6.1:60000/ipp/print   # the raster-only PPD
docker compose exec cups printer-queue-setup --name Brother_MFC-L2750DW ipp://172.30.6.1:60000/ipp/print   # re-create after a printer swap
docker compose exec cups ipptool -tv ipp://172.30.6.1:60000/ipp/print get-printer-attributes.test | grep -E 'document-format-supported|marker|state-reasons'
curl -s http://printer.lan:8080/api/v1/context | python3 -m json.tool | head      # scanservjs devices
sudo systemctl restart docker-compose@cn-printer             # whole-stack restart through the boot unit
```

State: `cups_etc` volume (printers.conf, PPD, the helper's state copy) and `/srv/scans` on the host. Both are backed up weekly by offen (Sun 01:30) to `s3://cloudnet-lab-storage/printer/` and the raidnas WebDAV share `/printer/`; the queue itself is reproducible from `.env` (`setup.sh` re-creates it). Restore = `kauket get` + `./setup.sh`, then untar scans into `/srv/scans`.

Trigger a backup by hand: `docker compose exec backup /usr/bin/backup`.

## Observability (kaiser, cn-observability)

- Prometheus jobs `printer-node` (`:9100`, `host=printer`) and `printer-cups` (`:9329`, `stack=cn-printer`); blackbox `blackbox-http` probes of `:631`, `:8080/api/v1/system`, `:5520`.
- Dashboards `printer-overview` (host: CPU/mem/disk/net + SoC temperature + under-voltage/throttle from `rpi.prom`) and `cups` (queue state, jobs, state reasons, toner, pages per day/user from `page_log` in Loki).
- Alerts: `PiUndervoltage`, `PiThrottled`, `CupsExporterDown`, `PrinterStopped`, `PrinterNeedsAttention`, `TonerLow`, `PrintQueueStuck`, plus the generic host group.
- promtail ships container logs, the journal and CUPS `page_log`/`error_log` (`CUPS_LOG_DIR` bind) to `10.1.1.140:3100`; kaiser's ufw must admit `10.2.0.30` on 3100.

Deliberate omissions: no Consul/traefik-lab route (plain HTTP on the tailnet by port), no Authentik gate (printing is a native-client protocol), no cAdvisor.

## Testing

`./test` builds the three local images and brings the stack up with **virtual devices** — `ippeveprinter` from `cups/Dockerfile.ippeve` (a fake IPP Everywhere printer that advertises PDF *and* raster, so the test proves CUPS still sends raster; it carries its own D-Bus + avahi because CUPS 2.4's ippeveprinter initialises DNS-SD even with `-r off`) and the SANE `test` backend (a synthetic colour flatbed scanservjs lists as `test:0`) — on loopback ports `16310/18080/15520/19329` (a Mac's own cupsd keeps `:631`). It then proves:

- queue created on the virtual printer, default, shared; `printer-queue-setup` re-run prints `UNCHANGED`; the PPD keeps `image/pwg-raster|urf` and has **no** `application/pdf`/`image/jpeg` passthrough;
- `lp` of a two-page PDF → the spooled document is PWG/URF raster (`EVIDENCE: print → …`);
- scanservjs REST scan to PNG and to PDF, downloaded and type-checked (`EVIDENCE: scan (…) → …`);
- swis up, upload (stored under swis's timestamped name) + `/print/execute` lands a second raster job; file ownership uid 1000 (Linux only);
- cups-exporter reports `cups_up 1`, printer idle, device state.

```bash
./test            # on the Mac (Docker Desktop) or any Linux Docker host
./test --keep     # leave the stack up: http://127.0.0.1:18080 (scan UI), :16310 (CUPS), :15520 (swis)
```

On macOS Docker Desktop a long scan request is occasionally dropped by the port forwarder; the scan step retries once (a real failure still fails both attempts). Linux runs (kora VM, rpid11, the Pi) have never shown it.

Inside a kora VM the same script is run by `amun-printer/test` (`amun/test --post tests/vm-e2e.sh`), after `amun`, `amun docker` and `amun printer` have converged. Static checks: `docker compose -f docker-compose.yml -f docker-compose.test.yml --env-file .env.test config --quiet`, `shellcheck cups/* swis/entrypoint.sh setup.sh test scripts/*.sh`, `python3 -m py_compile cups-exporter/exporter.py`.

## Gotchas

- **Never run `cupsctl` in the cups container** — it rewrites `cupsd.conf`, which the entrypoint renders from env on every start.
- **cups-browsed must stay out** (not installed in the image, purged on the host): on trixie it would auto-create a second queue for the ipp-usb printer with PDF passthrough.
- **`PRINTER_DEVICE_URI`/`PRINTER_ESCL_URL` hardcode the bridge gateway** `172.30.6.1`; if the IPAM block changes, change them too and re-run `./setup.sh`.
- **swis front-end host** is baked into its `config.json` as `SWIS_PUBLIC_HOST` (`printer.lan`). From the tailnet the page loads but its API calls go to `printer.lan`; use scanservjs there, or map `printer.lan` locally.
- **scanservjs and swis run as uid 1000** so scans and uploads land as `gonzalo` for the future Samba share and Stirling-PDF (`/srv/scans/inbox` as a watched folder). `scanservjs-init` (a one-shot, named to match amun-docker's reconcile skip list) hands the data volume to that uid; swis writes its front-end config as root and drops privileges with `setpriv`. On Docker Desktop (macOS) bind-mount ownership is virtualized, so `./test` only asserts ownership on Linux.
- **The printer may be off at boot**: cups starts anyway (healthcheck is the web UI, not the queue) and retries the queue every 60 s; `lpstat -v` empty until then.
- **AirPrint record**: `/etc/avahi/services/cn-printer-Brother_MFC-L2750DW.service` on the host, written by the cups entrypoint once the queue exists (`PRINTER_ADVERTISE_NAME`/`_HOST` in `.env`). If `dns-sd -B _ipp._tcp,_universal local.` on a Mac shows nothing, check `avahi-daemon` on the host and that `/etc/avahi/services` is bind-mounted; a Mac that still sees `dnssd://…/cups` is showing a cached record (`sudo killall -HUP mDNSResponder`).
- **Pi 4 power**: the SSD and the printer share the Pi's 1.2 A USB budget; under-voltage shows up as `rpi_undervoltage_*` on kaiser. Use the official 5.1 V / 3 A supply and a powered hub for the SSD.
- Promtail is EOL upstream (2026-03); kept here for fleet consistency until the fleet moves to Alloy.

## Next on this box (compatible by design)

Stirling-PDF (`stirlingtools/stirling-pdf:3.1.0`, `JAVA_TOOL_OPTIONS=-Xmx1g`, `mem_limit` works because amun-printer enables the memory cgroup) watching `/srv/scans/inbox` → `/srv/scans/processed`; a Samba share of `/srv/scans` (`ghcr.io/servercontainers/samba`, host network, `NETBIOS_DISABLE=1`, wsdd2, host avahi `smb.xml`). Either joins this project or a sibling stack with `boot.after: [cn-printer]`.

## License

GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007 — Copyright (c) 2026 Gonzalo Alvarez

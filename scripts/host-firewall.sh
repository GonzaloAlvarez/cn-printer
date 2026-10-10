#!/usr/bin/env bash
# cn-printer/scripts/host-firewall.sh — the UFW holes this stack needs on the
# host (idempotent, sudo). `amun printer` owns these; this script re-asserts
# them for a host where the plugin has not run yet.
#   9100,9329/tcp from kaiser (10.1.1.140): node-exporter-lan, cups-exporter
#   60000/tcp from the docker bridge range: ipp-usb for the containers
# Everything else (631, 8080, 5520) is Docker-published and bypasses ufw.
set -euo pipefail
[[ "$(id -u)" -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
KAISER_IP="${KAISER_IP:-10.1.1.140}"
DOCKER_CIDR="${DOCKER_CIDR:-172.16.0.0/12}"
ufw allow proto tcp from "$KAISER_IP" to any port 9100,9329 comment 'amun-printer: exporter scraped by kaiser prometheus'
ufw allow proto tcp from "$DOCKER_CIDR" to any port 60000 comment 'amun-printer: ipp-usb for cn-printer containers'
echo
ufw status numbered | grep -E 'amun-printer' || true

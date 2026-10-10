#!/bin/bash
# swis binds 0.0.0.0 regardless of --ip; --ip only feeds the FRONT-END config
# (api_url), so it must be the name clients use. swis writes that config into
# its own package dir at start, which needs root — write it here as root with
# --nocfg, then drop to the scans uid so every scan/upload lands as uid 1000.
set -euo pipefail
: "${SWIS_UID:=1000}" "${SWIS_GID:=1000}"
pkg="$(python -c 'import swis, os; print(os.path.dirname(swis.__file__))')"
printf '{"COMMENT": "!!!DO NOT EDIT MANUALLY!!!", "api_url": "%s", "api_port": "%s", "scans_url": "/files"}' \
  "$SWIS_PUBLIC_HOST" "$SWIS_PORT" > "$pkg/app/config.json"
mkdir -p "$SWIS_DESTINATION"
chown "$SWIS_UID:$SWIS_GID" "$SWIS_DESTINATION" 2>/dev/null || true
exec setpriv --reuid="$SWIS_UID" --regid="$SWIS_GID" --clear-groups \
  swis --ip "$SWIS_PUBLIC_HOST" --port "$SWIS_PORT" -d "$SWIS_DESTINATION" --nocfg

#!/usr/bin/env bash
# cn-printer/scripts/gdrive-auth.sh — mint rclone.conf for the Google Drive
# uploads. Runs on a machine with a browser and rclone (the Pi is headless).
#
# Google side, once (console.cloud.google.com, any project of yours):
#   1. APIs & Services → Library → enable "Google Drive API".
#   2. Google Auth Platform → Branding: External, app name e.g. "cn-printer",
#      your e-mail as support and developer contact.
#      Data access: add ONLY the scope ".../auth/drive.file" (non-sensitive —
#      no Google verification needed).
#      Audience: PUBLISH the app ("In production"). An app left "In testing"
#      gets refresh tokens that stop working after 7 days.
#   3. Clients → Create client → application type "Desktop app" → copy the
#      client ID and the client secret.
#
# Usage:
#   scripts/gdrive-auth.sh <client_id> <client_secret> [--remote-path gdrive:Scans] [--kauket]
#
# Opens the browser for the Google login (scope drive.file: rclone can only
# see files it created itself), writes a 0600 rclone.conf under $TMPDIR,
# checks it against Drive (creates the target folder), prints the Kauket
# command that installs it on the Pi and, with --kauket, runs it and shreds
# the temp file. Nothing containing the token is ever printed.
set -euo pipefail

SECRET_ID=printer.cn_printer_rclone_conf
DEST=/home/gonzalo/cn-printer/rclone/rclone.conf
REMOTE_PATH=gdrive:Scans
DO_KAUKET=0
CID=""; CSEC=""
die() { printf '\033[1;31m[gdrive-auth] %s\033[0m\n' "$*" >&2; exit 1; }
log() { printf '\033[1;36m[gdrive-auth]\033[0m %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --remote-path) REMOTE_PATH="$2"; shift ;;
    --kauket) DO_KAUKET=1 ;;
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    -*) die "unknown flag $1" ;;
    *) if [ -z "$CID" ]; then CID="$1"; elif [ -z "$CSEC" ]; then CSEC="$1"; else die "unexpected argument $1"; fi ;;
  esac
  shift
done
[ -n "$CID" ] && [ -n "$CSEC" ] || { sed -n '16,18p' "$0"; exit 2; }
[[ "$REMOTE_PATH" == gdrive:* ]] || die "--remote-path must start with 'gdrive:' (the remote name config.local.js and this file agree on)"
command -v rclone >/dev/null 2>&1 || die "rclone is not installed here (brew install rclone)"
command -v python3 >/dev/null 2>&1 || die "python3 is required to parse the authorize output"

umask 077
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cn-printer-gdrive.XXXXXX")"
CONF="$WORK/rclone.conf"
cleanup() { if [ "$DO_KAUKET" -eq 1 ]; then rm -rf "$WORK"; fi; }
trap cleanup EXIT

# rclone authorize takes the backend options as a URL-safe, unpadded base64
# JSON blob (the std alphabet is rejected: "decode simple map: illegal base64").
blob="$(printf '{"client_id":"%s","client_secret":"%s","scope":"drive.file"}' "$CID" "$CSEC" | base64 | tr -d '\n=' | tr '+/' '-_')"

log "a browser window opens: sign in with the Google account that owns the Drive and allow access"
log "(requested scope: drive.file only)"
rclone authorize drive "$blob" > "$WORK/authorize.out" || die "rclone authorize failed"

TOKEN_JSON="$(python3 - "$WORK/authorize.out" <<'PY'
import base64, json, sys
lines = open(sys.argv[1]).read().splitlines()
try:
    start = next(i for i, l in enumerate(lines) if l.strip().endswith('--->'))
    end = next(i for i, l in enumerate(lines) if l.strip().startswith('<---End paste'))
except StopIteration:
    sys.exit("could not find the '--->' … '<---End paste' block in the rclone authorize output")
payload = ''.join(l.strip() for l in lines[start + 1:end])
if payload.startswith('{'):
    obj = json.loads(payload)
else:
    pad = payload + '=' * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(pad)
    except Exception:
        raw = base64.b64decode(pad)
    obj = json.loads(raw)
if 'token' in obj:
    tok = obj['token']
    tok = json.loads(tok) if isinstance(tok, str) else tok
elif 'access_token' in obj:
    tok = obj
else:
    sys.exit("authorize output has neither 'token' nor 'access_token'")
for k in ('access_token', 'refresh_token'):
    if not tok.get(k):
        sys.exit(f"token is missing {k} — re-run; make sure the app is published and you approved the consent screen")
print(json.dumps(tok, separators=(',', ':')))
PY
)" || die "could not parse the token"

cat > "$CONF" <<EOT
# cn-printer Google Drive remote — minted by scripts/gdrive-auth.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Installed on the Pi by Kauket ($SECRET_ID → $DEST, mode 0600, owner uid 1000).
# rclone rewrites the token line when it refreshes the access token.
[gdrive]
type = drive
client_id = $CID
client_secret = $CSEC
scope = drive.file
token = $TOKEN_JSON
EOT
chmod 0600 "$CONF"

log "checking the remote and creating ${REMOTE_PATH} if needed"
rclone --config "$CONF" mkdir "$REMOTE_PATH" || die "rclone could not create ${REMOTE_PATH} — token or scope problem"
rclone --config "$CONF" lsd "${REMOTE_PATH%%:*}:" | sed 's/^/    /' || true
log "OK — Drive answers; ${REMOTE_PATH} exists (with scope drive.file, rclone lists only what it created)"

KAUKET_CMD=(kauket add "$SECRET_ID" "$CONF" --dest "$DEST" --mode 0600)
if kauket list 2>/dev/null | grep -qx "$SECRET_ID"; then KAUKET_CMD+=(--force); fi
if [ "$DO_KAUKET" -eq 1 ]; then
  log "running: ${KAUKET_CMD[*]}"
  "${KAUKET_CMD[@]}"
  log "stored. On the Pi:  ssh printer.lan 'cd ~/cn-printer && kauket get $SECRET_ID'   (no restart needed — rclone reads the file per upload)"
else
  cat <<EOT

rclone.conf written to $CONF (0600). Next:
  ${KAUKET_CMD[*]}
  ssh printer.lan 'cd ~/cn-printer && kauket get $SECRET_ID'
then delete the temp file:  rm -rf "$WORK"
(or re-run with --kauket to do the first step and the cleanup here)
EOT
fi

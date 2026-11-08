# cn-printer paperless-forwarder — watches /srv/scans/inbox and uploads every
# finished scan to Paperless-ngx through its REST API (forwarder.py, stdlib
# only). Runs as root on purpose: it writes the node-exporter textfile dir
# (root 0755 on the host); moved files keep their uid 1000, markers are
# chowned to SCANS_UID:SCANS_GID. Local build → Watchtower label excludes it.
FROM python:3.12-alpine
RUN apk add --no-cache ca-certificates tzdata
WORKDIR /app
COPY forwarder.py /app/forwarder.py
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/app/forwarder.py"]

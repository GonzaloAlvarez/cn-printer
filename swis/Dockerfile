# cn-printer/swis — swis 0.6.1 ("Simple Web-based Interface for Scanner"):
# scan + upload-to-print web UI on :5520. Python 3.12 on purpose: swis pins
# pydantic~=1.9 / uvicorn~=0.17, which do not build on trixie's 3.13.
# scanimage uses the SANE default device (the static airscan entry mounted at
# /etc/sane.d/airscan.conf); lp prints to the CUPS default queue over
# CUPS_SERVER (localhost:631 inside the ts-printer netns).
FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends sane-utils sane-airscan imagemagick cups-client curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && for f in /etc/ImageMagick-*/policy.xml; do \
      sed -i -E 's#<policy domain="coder" rights="none" pattern="PDF" />#<policy domain="coder" rights="read | write" pattern="PDF" />#' "$f"; \
    done \
 # swis declares uvicorn~=0.17 (any 0.x) + websockets~=9.1; pip picks uvicorn 0.54,
 # whose websockets integration needs websockets>=13 -> ImportError at start.
 # 0.29 is the last uvicorn whose websockets path still works with 9.x.
 && pip install swis==0.6.1 'uvicorn==0.29.0' \
 && groupadd -g 1000 scans && useradd -u 1000 -g 1000 -M -s /usr/sbin/nologin scans \
 && mkdir -p /srv/scans/swis && chown 1000:1000 /srv/scans/swis
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh
ENV CUPS_SERVER=localhost:631 SWIS_PUBLIC_HOST=printer.lan SWIS_PORT=5520 SWIS_DESTINATION=/srv/scans/swis SWIS_UID=1000 SWIS_GID=1000
EXPOSE 5520
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fs -o /dev/null http://localhost:5520/ || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

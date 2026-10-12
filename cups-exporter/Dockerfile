# cn-printer/cups-exporter — Prometheus metrics for the CUPS queue and the
# Brother itself (no maintained upstream exporter exposes state reasons,
# toner or impressions): IPP via pycups plus the Brother's public
# maintenance web page (brother_ews.py) for lifetime counters.
# Debian packages only: python3-cups + the client.
FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-cups python3-prometheus-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY exporter.py /usr/local/bin/cups-exporter
COPY brother_ews.py /usr/local/lib/cups-exporter/brother_ews.py
RUN chmod 0755 /usr/local/bin/cups-exporter
ENV PYTHONPATH=/usr/local/lib/cups-exporter
ENV CUPS_SERVER=127.0.0.1:631 EXPORTER_PORT=9329
EXPOSE 9329
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/metrics' % os.environ.get('EXPORTER_PORT','9329'), timeout=4)" || exit 1
ENTRYPOINT ["/usr/local/bin/cups-exporter"]

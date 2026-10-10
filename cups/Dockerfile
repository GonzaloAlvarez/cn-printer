# cn-printer/cups — cupsd for the printer.lan Pi, Debian trixie (same release
# as the host). No printer drivers: the queue is driverless IPP over ipp-usb
# with a raster-only PPD (see printer-queue-setup). No cups-browsed: on trixie
# it would auto-create a second, PDF-passthrough queue for the ipp-usb printer.
FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      cups cups-filters cups-ipp-utils ghostscript curl ca-certificates file procps \
 && apt-get purge -y cups-browsed 2>/dev/null || true \
 && rm -rf /var/lib/apt/lists/* \
 && cp -a /etc/cups /usr/local/share/cn-printer-etc-cups
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY printer-queue-setup /usr/local/sbin/printer-queue-setup
RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/sbin/printer-queue-setup
VOLUME ["/etc/cups"]
EXPOSE 631
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
  CMD curl -fs -o /dev/null http://localhost:631/ || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# cn-printer scanservjs image: upstream v3.3.0 (Debian trixie, tesseract 5.5
# with eng/spa/deu/fra/… traineddata) plus a pinned rclone for the Google
# Drive actions in config.local.js. rclone comes from the GitHub release and
# is verified against the release's SHA256SUMS (trixie's apt rclone is the
# 2022 1.60 line). Watchtower does not track this image (local build): bump
# the two versions by hand after reading the release notes.
#
# The ARG is RCLONE_REL, not RCLONE_VERSION: rclone maps every RCLONE_* env
# var to a flag, so an ARG named RCLONE_VERSION breaks `rclone version`
# ("Invalid value when setting --version from environment variable").
ARG SCANSERVJS_VERSION=v3.3.0
ARG RCLONE_REL=1.75.2

FROM debian:trixie-slim AS rclone
ARG RCLONE_REL
ARG TARGETARCH
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    base="https://github.com/rclone/rclone/releases/download/v${RCLONE_REL}"; \
    deb="rclone-v${RCLONE_REL}-linux-${TARGETARCH}.deb"; \
    cd /tmp; \
    curl -fsSL -o "${deb}" "${base}/${deb}"; \
    curl -fsSL -o SHA256SUMS "${base}/SHA256SUMS"; \
    grep " ${deb}\$" SHA256SUMS | sha256sum -c -; \
    dpkg-deb -x "${deb}" /out; \
    /out/usr/bin/rclone version

FROM sbs20/scanservjs:${SCANSERVJS_VERSION}
COPY --from=rclone /out/usr/bin/rclone /usr/local/bin/rclone

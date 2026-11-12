#!/bin/sh
set -eu
umask 077
AUTH_FILE=$(mktemp)
trap 'rm -f "$AUTH_FILE"' EXIT
printf 'machine %s login %s password %s\n' "$NEXUS_HOST" "$NEXUS_USER" "$NEXUS_PASSWORD" > "$AUTH_FILE"
BASE_URL="http://${NEXUS_HOST}:8081/repository/syspulse"
# Hosted APT accepts the raw Debian package through POST at the repository root.
HTTP_STATUS=$(curl --fail-with-body --silent --show-error --max-time 120 \
  --netrc-file "$AUTH_FILE" -H 'Content-Type: application/vnd.debian.binary-package' \
  --data-binary "@dist/syspulse_${PKG_VERSION}_amd64.deb" \
  --output /tmp/apt-upload-response --write-out '%{http_code}' "${BASE_URL}/") || {
    cat /tmp/apt-upload-response >&2
    exit 1
  }
printf 'UPLOAD_HTTP_STATUS=%s\n' "$HTTP_STATUS"
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 15 \
      "${BASE_URL}/dists/${APT_DIST}/InRelease" -o package-artifacts/InRelease; then
    break
  fi
  sleep 2
done
curl --fail --silent --show-error --max-time 30 "${BASE_URL}/dists/${APT_DIST}/Release" -o package-artifacts/Release
curl --fail --silent --show-error --max-time 30 \
  "${BASE_URL}/dists/${APT_DIST}/${APT_COMPONENT}/binary-amd64/Packages" -o package-artifacts/Packages
printf 'Package uploaded; signed repository metadata downloaded.\n'

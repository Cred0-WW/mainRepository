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
EXPECTED_DEB_SHA256=$(sha256sum "dist/syspulse_${PKG_VERSION}_amd64.deb" | awk '{print $1}')
METADATA_READY=false
for attempt in $(seq 1 30); do
  if curl --fail --silent --max-time 15 \
      "${BASE_URL}/dists/${APT_DIST}/${APT_COMPONENT}/binary-amd64/Packages" -o package-artifacts/Packages \
      && grep -q "^SHA256: ${EXPECTED_DEB_SHA256}$" package-artifacts/Packages \
      && curl --fail --silent --max-time 15 "${BASE_URL}/dists/${APT_DIST}/Release" -o package-artifacts/Release \
      && curl --fail --silent --max-time 15 "${BASE_URL}/dists/${APT_DIST}/InRelease" -o package-artifacts/InRelease; then
    PACKAGES_SHA256=$(sha256sum package-artifacts/Packages | awk '{print $1}')
    if grep -Eq "^[[:space:]]*${PACKAGES_SHA256}[[:space:]]+[0-9]+[[:space:]]+${APT_COMPONENT}/binary-amd64/Packages$" package-artifacts/Release \
        && grep -q "$PACKAGES_SHA256" package-artifacts/InRelease; then
      METADATA_READY=true
      break
    fi
  fi
  printf 'Waiting for matching signed APT metadata: attempt %s\n' "$attempt"
  sleep 2
done
if [ "$METADATA_READY" != true ]; then
  echo 'Timed out waiting for signed metadata for the newly uploaded package' >&2
  exit 1
fi
printf 'Package uploaded; signed repository metadata downloaded.\n'

#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if dpkg-query -W syspulse >/dev/null 2>&1; then
  echo 'The verification container was not clean' >&2
  exit 1
fi
echo 'CLEAN_CONTAINER=true; syspulse was not installed before the test.'
install -m 0644 /input/syspulse-archive-keyring.gpg /usr/share/keyrings/syspulse-archive-keyring.gpg
# Update only our source, avoiding unrelated internet repositories in this test.
printf 'deb [arch=amd64 signed-by=/usr/share/keyrings/syspulse-archive-keyring.gpg] http://%s:8081/repository/syspulse/ %s %s\n' \
  "$NEXUS_HOST" "$APT_DIST" "$APT_COMPONENT" > /etc/apt/sources.list.d/syspulse.list
apt-get -o Dir::Etc::sourcelist=sources.list.d/syspulse.list \
  -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0 update
apt-get install -y --no-install-recommends "syspulse=${PKG_VERSION}"
echo 'PACKAGE_INFO_BEGIN'
dpkg-query -W -f='Package: ${Package}\nVersion: ${Version}\nArchitecture: ${Architecture}\nStatus: ${Status}\n' syspulse
echo 'PACKAGE_INFO_END'
test "$(dpkg-query -W -f='${Version}' syspulse)" = "$PKG_VERSION"
test "$(dpkg-query -W -f='${Architecture}' syspulse)" = amd64
test "$(syspulse --version)" = "syspulse ${PKG_VERSION}"
echo "${EXPECTED_BINARY_SHA256}  /usr/bin/syspulse" | sha256sum --check --status
echo 'INSTALLED_BINARY_SHA256_MATCH=true'
echo 'VERSION_OUTPUT_BEGIN'
syspulse --version
echo 'VERSION_OUTPUT_END'
echo 'SNAPSHOT_BEGIN'
syspulse
echo 'SNAPSHOT_END'
echo 'APT_INSTALL_AND_EXECUTION_VERIFIED=true'

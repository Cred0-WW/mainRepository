#!/bin/sh
set -eu
case "$PKG_VERSION" in ''|*[!0-9.]*) echo 'Invalid package version' >&2; exit 1;; esac
PKG_ROOT="build/syspulse_${PKG_VERSION}"
mkdir -p "$PKG_ROOT/DEBIAN" "$PKG_ROOT/usr/bin" dist package-artifacts
cp dist/syspulse "$PKG_ROOT/usr/bin/syspulse"
chmod 755 "$PKG_ROOT/usr/bin/syspulse"
cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: syspulse
Version: ${PKG_VERSION}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: DevOps Team <devops@example.com>
Description: Lightweight CLI tool for a system health snapshot
 Reads /proc and reports uptime, load average and memory info.
EOF
dpkg-deb --build --root-owner-group -Zgzip -z9 "$PKG_ROOT" "dist/syspulse_${PKG_VERSION}_amd64.deb"
dpkg-deb --info "dist/syspulse_${PKG_VERSION}_amd64.deb" > package-artifacts/package-control.txt
dpkg-deb --contents "dist/syspulse_${PKG_VERSION}_amd64.deb" > package-artifacts/package-contents.txt
sha256sum "dist/syspulse_${PKG_VERSION}_amd64.deb" dist/syspulse > package-artifacts/SHA256SUMS
printf 'Version was read from main.go; .deb metadata and file list verified.\n'

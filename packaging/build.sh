#!/bin/sh
set -eu
mkdir -p dist package-artifacts
export GOOS=linux GOARCH=amd64 CGO_ENABLED=0 GOTOOLCHAIN=local
go build -trimpath -buildvcs=false -ldflags='-s -w' -o dist/syspulse .
go version -m dist/syspulse > package-artifacts/go-build.txt
printf 'Built static linux/amd64 binary with CGO_ENABLED=0.\n'

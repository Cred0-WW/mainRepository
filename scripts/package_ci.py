#!/usr/bin/env python3
"""Build, package, publish and verify syspulse using isolated Linux containers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'package-artifacts'
ENV_FILE = ROOT / '.env.syspulse'
OWNER = ROOT / '.env.syspulse.owner'
TOOLS = 'syspulse-practice5-tools:1.27.4'
DOCKER = '/Applications/Docker.app/Contents/Resources/bin/docker' if sys.platform == 'darwin' else 'docker'
NAMES = ['NEXUS_HOST', 'NEXUS_USER', 'NEXUS_PASSWORD']


def version():
    matches = re.findall(r'^const Version = "([0-9]+\.[0-9]+\.[0-9]+)"$', (ROOT / 'main.go').read_text(), re.M)
    if len(matches) != 1:
        raise ValueError('main.go must contain one semantic Version constant')
    return matches[0]


def clean(text):
    for key in NAMES:
        if os.environ.get(key):
            text = text.replace(os.environ[key], '***')
    return text


def run(args, *, stdin=None, show=True, timeout=900):
    result = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=ROOT, timeout=timeout)
    safe = clean(result.stdout.decode(errors='replace'))
    if show or result.returncode:
        print(safe, flush=True)
    if result.returncode:
        raise ValueError(f'Command failed with exit code {result.returncode}: {args[0]}')
    return safe


def docker(*args, **kwargs):
    return run([DOCKER, *args], **kwargs)


def write_json(name, value):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(clean(json.dumps(value, ensure_ascii=False, indent=2)) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_epoch():
    result = subprocess.run(['git', 'log', '-1', '--format=%ct', '--', 'main.go'], cwd=ROOT, capture_output=True, text=True)
    value = result.stdout.strip()
    return value if value.isdigit() else '0'


def tools():
    docker('build', '-f', 'packaging/Dockerfile.tools', '-t', TOOLS, '.')
    docker('run', '--rm', '--network', 'none', TOOLS, 'sh', '-ec', 'go version; dpkg-deb --version | head -n 1')


def configure():
    host = os.environ.get('NEXUS_HOST', '')
    user = os.environ.get('NEXUS_USER', '')
    password = os.environ.get('NEXUS_PASSWORD', '')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', host) or not re.fullmatch(r'[A-Za-z0-9_.-]+', user):
        raise ValueError('Set valid NEXUS_HOST and NEXUS_USER Secrets')
    if not re.fullmatch(r'[A-Za-z0-9_!.-]{16,128}', password):
        raise ValueError('NEXUS_PASSWORD must contain 16-128 supported non-whitespace characters')
    if ENV_FILE.exists():
        raise ValueError('An existing .env.syspulse was left unchanged')
    for path, content in [(ENV_FILE, ''.join(f'{k}={os.environ[k]}\n' for k in NAMES))]:
        with open(path, 'w') as stream:
            os.chmod(path, 0o600)
            stream.write(content)
    OWNER.write_text(sha(ENV_FILE))
    OWNER.chmod(0o600)
    print('Private container environment created; credential values not printed.')


def build():
    OUT.mkdir(exist_ok=True)
    docker('run', '--rm', '--network', 'none', '-v', f'{ROOT}:/workspace', TOOLS, 'sh', 'packaging/build.sh')
    data = (ROOT / 'dist/syspulse').read_bytes()
    if data[:5] != b'\x7fELF\x02' or struct.unpack_from('<H', data, 18)[0] != 62:
        raise ValueError('The output is not an ELF64 amd64 executable')
    offset = struct.unpack_from('<Q', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 54)
    if any(struct.unpack_from('<I', data, offset + i * size)[0] == 3 for i in range(count)):
        raise ValueError('The executable contains PT_INTERP and is not fully static')
    write_json('build-info.json', {'version': version(), 'platform': 'linux/amd64', 'elf64': True,
                                 'static': True, 'CGO_ENABLED': '0', 'binarySHA256': sha(ROOT / 'dist/syspulse')})


def package():
    ver = version()
    docker('run', '--rm', '--network', 'none', '-e', f'PKG_VERSION={ver}',
           '-e', f'SOURCE_DATE_EPOCH={source_epoch()}', '-v', f'{ROOT}:/workspace', TOOLS, 'sh', 'packaging/package.sh')
    target = ROOT / f'dist/syspulse_{ver}_amd64.deb'
    write_json('package-info.json', {'package': 'syspulse', 'version': ver, 'architecture': 'amd64',
                                   'bytes': target.stat().st_size, 'SHA256': sha(target),
                                   'sourceDateEpoch': source_epoch()})
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f'pkg_ver={ver}\ndeb_path=dist/syspulse_{ver}_amd64.deb\n')


def publish():
    output = docker('run', '--rm', '--network', 'syspulsepr5_network', '--env-file', str(ENV_FILE),
                    '-e', f'PKG_VERSION={version()}', '-e', 'APT_DIST=stable', '-e', 'APT_COMPONENT=main',
                    '-v', f'{ROOT}:/workspace', TOOLS, 'sh', 'packaging/publish.sh')
    match = re.search(r'UPLOAD_HTTP_STATUS=(200|201|204)', output)
    if not match:
        raise ValueError('The repository did not confirm the upload')
    package_info = json.loads((OUT / 'package-info.json').read_text())
    index = (OUT / 'Packages').read_text()
    entries = [block for block in index.split('\n\n') if 'Package: syspulse\n' in block + '\n' and f'Version: {version()}\n' in block + '\n']
    if len(entries) != 1 or f'SHA256: {package_info["SHA256"]}' not in entries[0]:
        raise ValueError('APT Packages metadata does not match the built .deb')
    if 'BEGIN PGP SIGNED MESSAGE' not in (OUT / 'InRelease').read_text():
        raise ValueError('InRelease was not signed')
    write_json('publish.json', {'repository': 'syspulse', 'distribution': 'stable', 'component': 'main',
                               'HTTP': int(match[1]), 'packageIndexSHA256Matches': True,
                               'signedInRelease': True, 'matchingPackageEntries': len(entries)})


def verify():
    ver = version()
    log = docker('run', '--rm', '-i', '--platform', 'linux/amd64', '--network', 'syspulsepr5_network',
                 '-e', f'NEXUS_HOST={os.environ["NEXUS_HOST"]}', '-e', f'PKG_VERSION={ver}',
                 '-e', 'APT_DIST=stable', '-e', 'APT_COMPONENT=main',
                 '-e', f'EXPECTED_BINARY_SHA256={sha(ROOT / "dist/syspulse")}',
                 '-v', f'{ROOT}/packaging/syspulse-archive-keyring.gpg:/input/syspulse-archive-keyring.gpg:ro',
                 'ubuntu:22.04', 'bash', '-se', stdin=(ROOT / 'packaging/verify.sh').read_bytes())
    (OUT / 'apt-install.log').write_text(log)
    if 'APT_INSTALL_AND_EXECUTION_VERIFIED=true' not in log:
        raise ValueError('APT installation or execution verification was incomplete')
    snapshot = log.split('SNAPSHOT_BEGIN\n', 1)[1].split('SNAPSHOT_END', 1)[0]
    (OUT / 'syspulse-output.txt').write_text(snapshot)
    write_json('verification.json', {'verified': True, 'package': 'syspulse', 'version': ver,
                                    'architecture': 'amd64', 'cleanUbuntu': '22.04',
                                    'installedThroughAPT': True, 'signedByKeyring': True,
                                    'installedBinarySHA256Matches': True, 'versionOutput': f'syspulse {ver}',
                                    'executionMode': 'GitHub Actions' if os.environ.get('GITHUB_ACTIONS') == 'true' else 'local',
                                    'runId': os.environ.get('GITHUB_RUN_ID', 'local')})


def collect():
    OUT.mkdir(exist_ok=True)
    for path in OUT.rglob('*'):
        if path.is_symlink():
            raise ValueError('Artifact symlinks are not allowed')
        if path.is_file():
            path.write_text(clean(path.read_text(errors='replace')))
            if any(os.environ.get(k) and os.environ[k].encode() in path.read_bytes() for k in NAMES):
                raise ValueError('A credential value was found in artifacts')
    for path in (ROOT / 'dist').glob('*'):
        if path.is_file() and any(os.environ.get(k) and os.environ[k].encode() in path.read_bytes() for k in NAMES):
            raise ValueError('A credential value was found in distribution files')
    print('Build, package, publication and APT evidence collected; credential-value audit passed.')


def cleanup():
    if ENV_FILE.exists():
        if OWNER.is_file() and OWNER.read_text() == sha(ENV_FILE):
            ENV_FILE.unlink(); OWNER.unlink()
        else:
            print('A pre-existing environment file was left unchanged.')
    else:
        OWNER.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['tools', 'configure', 'build', 'package', 'publish', 'verify', 'collect', 'cleanup'])
    args = parser.parse_args()
    try:
        globals()[args.command]()
    except Exception as error:
        print(clean(str(error)), file=sys.stderr)
        raise SystemExit(1)

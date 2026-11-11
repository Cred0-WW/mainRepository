#!/usr/bin/env python3
"""Repeatable migration for the isolated Mattermost practice deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get('MATTERMOST_STATE_DIR', str(Path.home() / 'Documents/Codex/mattermost-migration-lesson/state')))
MARKER = STATE / '.migration_completed'
OUT = ROOT / 'migration-artifacts'
REQUIRED = ['DB_MYSQL_ROOT_PASSWORD', 'DB_MYSQL_PASSWORD', 'DB_POSTGRES_PASSWORD', 'MM_ADMIN_PASSWORD']
ENV = ROOT / '.env.migration'
ENV_OWNER = ROOT / '.env.migration.owner'
DOCKER = '/Applications/Docker.app/Contents/Resources/bin/docker' if sys.platform == 'darwin' else 'docker'
BASE_URL = 'http://127.0.0.1:8065'


def redact(text):
    for key in REQUIRED:
        value = os.environ.get(key, '')
        if value:
            text = text.replace(value, '***')
    return text


def command(args, *, stdin=None, timeout=600, show=True):
    result = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=timeout, cwd=ROOT)
    if show:
        print(redact(result.stdout.decode(errors='replace')), flush=True)
    if result.returncode:
        if not show:
            print(redact(result.stdout.decode(errors='replace')), flush=True)
        raise RuntimeError(f'Command failed with exit code {result.returncode}: {args[0]}')
    return result.stdout


def docker(*args, **kwargs):
    return command([DOCKER, *args], **kwargs)


def compose(*args):
    return docker('compose', '--env-file', str(ENV), '-f', str(ROOT / 'docker-compose.prod.yml'), *args)


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as stream:
        os.chmod(path, 0o600)
        stream.write(data.encode() if isinstance(data, str) else data)


def write_json(name, value):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(redact(json.dumps(value, ensure_ascii=False, indent=2)) + '\n')


def configure():
    for key in REQUIRED:
        value = os.environ.get(key, '')
        if not re.fullmatch(r'[A-Za-z0-9_!.-]{16,128}', value):
            raise ValueError(f'{key}: use 16-128 characters from A-Z a-z 0-9 _ ! . -')
    if ENV.exists():
        raise ValueError('An existing .env.migration was left unchanged')
    private_write(ENV, ''.join(f'{key}={json.dumps(os.environ[key])}\n' for key in REQUIRED))
    private_write(ENV_OWNER, hashlib.sha256(ENV.read_bytes()).hexdigest())
    print('Created private .env.migration; no secret values printed.')


def status():
    migrated = MARKER.is_file()
    if migrated:
        saved = json.loads(MARKER.read_text())
        if saved.get('targetVersion') != '12.0.1' or not saved.get('verified'):
            raise ValueError('Unexpected marker contents; refusing automatic data replacement')
    value = str(migrated).lower()
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f'already_migrated={value}\n')
    print(f'already_migrated={value}; marker={MARKER}')


def mysql(query):
    return docker('exec', '-i', 'mattermost-mysql', 'sh', '-c',
                  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --default-character-set=utf8mb4 --batch --raw --skip-column-names mattermost',
                  stdin=query.encode(), show=False)


def psql(query):
    return docker('exec', '-i', 'mattermost-postgres', 'psql', '-U', 'mmuser', '-d', 'mattermost',
                  '-v', 'ON_ERROR_STOP=1', '-At', stdin=query.encode(), show=False)


def backup():
    if MARKER.exists():
        raise ValueError('Migration already complete; refusing a new legacy backup')
    OUT.mkdir(exist_ok=True)
    docker('stop', 'mattermost')
    # A failed deployment may already have removed the old container. Its
    # preserved MySQL volume allows a retry without recreating source data.
    docker('compose', '--env-file', str(ENV), '-f', str(ROOT / 'legacy/docker-compose.yml'),
           'up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'mattermost-mysql')
    # The application is stopped before taking a consistent database and file snapshot.
    run = os.environ.get('GITHUB_RUN_ID', str(int(time.time())))
    backup_dir = STATE / 'backups' / run
    dump = docker('exec', 'mattermost-mysql', 'sh', '-c',
                  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --default-character-set=utf8mb4 --single-transaction --routines --triggers --no-tablespaces --set-gtid-purged=OFF mattermost', show=False)
    if len(dump) < 1000 or b'CREATE TABLE' not in dump:
        raise ValueError('MySQL backup is empty or incomplete')
    private_write(backup_dir / 'mattermost.sql', dump)
    source = json.loads(mysql("SELECT JSON_ARRAYAGG(JSON_OBJECT('id', Id, 'message', Message)) FROM Posts WHERE Type='';").decode())
    write_json('mysql-before-transfer.json', source)
    archive = docker('run', '--rm', '--network', 'none', '-v', 'mattermostpr4_data:/data:ro',
                     '--entrypoint', 'tar', 'postgres:15-alpine', 'czf', '-', '-C', '/data', '.', show=False)
    private_write(backup_dir / 'mattermost-data.tar.gz', archive)
    info = {'databaseBytes': len(dump), 'databaseSHA256': hashlib.sha256(dump).hexdigest(),
            'filesBytes': len(archive), 'filesSHA256': hashlib.sha256(archive).hexdigest(),
            'runId': run, 'storedOutsideRepository': True}
    private_write(backup_dir / 'manifest.json', json.dumps(info, indent=2))
    write_json('backup-manifest.json', info)
    print('Consistent MySQL and shared-volume backups saved privately outside the checkout.')


def migrate():
    if MARKER.exists():
        raise ValueError('Refusing to overwrite an already migrated PostgreSQL database')
    compose('up', '-d', '--wait', '--wait-timeout', '180', 'db-postgres')
    previous_tables = psql("SELECT tablename FROM pg_tables WHERE schemaname IN ('public','mattermost') ORDER BY tablename;").decode().splitlines()
    write_json('target-before-reset.json', {'tables': previous_tables, 'markerPresent': False})
    # pgloader data-only mode needs Mattermost's exact PostgreSQL schema. Rebuild
    # only this isolated target on a retry; keep the stopped MySQL source intact.
    psql('DROP SCHEMA IF EXISTS mattermost CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION mmuser;')
    pg_dsn = f"postgres://mmuser:{os.environ['DB_POSTGRES_PASSWORD']}@mattermost-postgres:5432/mattermost?sslmode=disable"
    docker('run', '--rm', '--platform', 'linux/amd64', '--network', 'mattermostpr4_network',
           '-e', 'MM_SQLSETTINGS_DRIVERNAME=postgres', '-e', f'MM_SQLSETTINGS_DATASOURCE={pg_dsn}',
           '--entrypoint', '/mattermost/bin/mattermost',
           'mattermost/mattermost-team-edition:9.5', 'db', 'migrate')
    enum_types = psql("SELECT typname FROM pg_type WHERE typtype='e';").decode().splitlines()
    enum_casts = ''.join(f'column {table}.Type to "{kind}" drop typemod,\n           '
                         for table, kind in [('Channels', 'channel_type'), ('Teams', 'team_type'),
                                             ('UploadSessions', 'upload_session_type')]
                         if kind in enum_types)
    load = f'''LOAD DATABASE
      FROM mysql://root:{os.environ['DB_MYSQL_ROOT_PASSWORD']}@mattermost-mysql:3306/mattermost
      INTO pgsql://mmuser:{os.environ['DB_POSTGRES_PASSWORD']}@mattermost-postgres:5432/mattermost
      WITH include drop, data only, workers = 2, concurrency = 1,
           prefetch rows = 1000, batch rows = 500,
           create no tables, create no indexes, preserve index names
      SET PostgreSQL PARAMETERS maintenance_work_mem to '64MB', work_mem to '8MB'
      CAST {enum_casts}type int when (= precision 11) to integer drop typemod,
           type bigint when (= precision 20) to bigint drop typemod,
           type text to varchar drop typemod using remove-null-characters,
           type tinyint when (<= precision 4) to boolean using tinyint-to-boolean,
           type json to jsonb drop typemod using remove-null-characters
      EXCLUDING TABLE NAMES MATCHING ~<IR_>, ~<focalboard>, 'schema_migrations',
           'db_migrations', 'db_lock', 'Configurations', 'ConfigurationFiles', 'db_config_migrations', 'calls'
      BEFORE LOAD DO
           $$ ALTER SCHEMA public RENAME TO mattermost; $$,
           $$ TRUNCATE TABLE mattermost.systems; $$,
           $$ DROP INDEX IF EXISTS mattermost.idx_posts_message_txt; $$,
           $$ DROP INDEX IF EXISTS mattermost.idx_fileinfo_content_txt; $$
      AFTER LOAD DO
           $$ ALTER SCHEMA mattermost RENAME TO public; $$,
           $$ ALTER USER mmuser SET search_path TO public; $$;
'''
    private_write(STATE / 'migration.load', load)
    try:
        # stdin avoids a host bind mount and secret values in process arguments.
        log = docker('run', '--rm', '-i', '--platform', 'linux/amd64', '--network', 'mattermostpr4_network',
                     '--entrypoint', 'sh', 'mattermost/mattermost-pgloader:latest', '-c',
                     'umask 077; cat > /tmp/migration.load; pgloader --verbose /tmp/migration.load',
                     stdin=load.encode(), show=False)
        safe = redact(log.decode(errors='replace'))
        (OUT / 'pgloader.log').write_text(safe)
        print(safe, flush=True)
        if re.search(r'\b(FATAL|ERROR)\b', safe):
            raise ValueError('pgloader reported an error; marker was not created')
    finally:
        (STATE / 'migration.load').unlink(missing_ok=True)
    psql("CREATE INDEX IF NOT EXISTS idx_posts_message_txt ON public.posts USING gin(to_tsvector('english', message)); CREATE INDEX IF NOT EXISTS idx_fileinfo_content_txt ON public.fileinfo USING gin(to_tsvector('english', content));")
    rows = json.loads(psql("SELECT json_agg(t) FROM (SELECT id, message FROM posts WHERE type='' ORDER BY id) t;").decode())
    write_json('postgres-after-transfer.json', rows)
    print('pgloader completed; exact Mattermost PostgreSQL schema and full-text indexes are ready.')


def deploy():
    compose('up', '-d', '--build', '--remove-orphans', '--wait', '--wait-timeout', '180')
    wait_http()


def api(path, *, token=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(BASE_URL + '/api/v4' + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    return urllib.request.urlopen(req, timeout=20)


def wait_http():
    for attempt in range(120):
        try:
            with api('/system/ping') as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(2)
    raise ValueError('Mattermost did not return HTTP 200 within 240 seconds')


def verify():
    wait_http()
    expected = json.loads((ROOT / 'fixtures/expected-data.json').read_text())
    with api('/users/login', data={'login_id': 'georgiy.titov', 'password': os.environ['MM_ADMIN_PASSWORD']}) as response:
        token = response.headers['Token']
        user = json.load(response)
    with api('/system/ping') as response:
        version = response.headers.get('X-Version-Id', '')
        health = json.load(response)
    if not version.startswith('12.0.1'):
        raise ValueError('Unexpected Mattermost version: ' + version)
    with api('/channels/' + expected['channelId'], token=token) as response:
        channel = json.load(response)
    if channel['type'] != 'P':
        raise ValueError('The private channel did not remain private')
    with api('/channels/' + expected['channelId'] + '/posts?per_page=100', token=token) as response:
        posts = json.load(response)['posts']
    for post in expected['posts']:
        if posts.get(post['id'], {}).get('message') != post['message']:
            raise ValueError('An original message ID or message text was lost')
    with api('/files/' + expected['fileId'], token=token) as response:
        attachment_hash = hashlib.sha256(response.read()).hexdigest()
    with api('/users/' + user['id'] + '/image', token=token) as response:
        avatar_hash = hashlib.sha256(response.read()).hexdigest()
    if attachment_hash != expected['attachmentSHA256'] or avatar_hash != expected['avatarSHA256']:
        raise ValueError('Attachment or avatar checksum changed')
    with urllib.request.urlopen(BASE_URL, timeout=15) as response:
        web_status = response.status
    if web_status != 200:
        raise ValueError('The web interface did not return HTTP 200')
    sql_rows = json.loads(psql("SELECT json_agg(t) FROM (SELECT id,message FROM posts WHERE type='' ORDER BY id) t;").decode())
    by_id = {r['id']: r['message'] for r in sql_rows}
    if any(by_id.get(p['id']) != p['message'] for p in expected['posts']):
        raise ValueError('PostgreSQL rows differ from the source messages')
    if len(sql_rows) != len(expected['posts']):
        raise ValueError('Unexpected number of user messages; possible duplicate records')
    containers = docker('ps', '--filter', 'label=com.docker.compose.project=mattermostpr4', '--format', '{{.Names}}', show=False).decode().splitlines()
    if set(containers) != {'mattermost', 'mattermost-postgres'}:
        raise ValueError('Unexpected containers in this project: ' + ', '.join(containers))
    data_volume = docker('inspect', 'mattermost', '--format', '{{range .Mounts}}{{if eq .Destination "/mattermost/data"}}{{.Name}}{{end}}{{end}}', show=False).decode().strip()
    if data_volume != 'mattermostpr4_data':
        raise ValueError('The shared user-files volume changed')
    retry_probe_absent = psql("SELECT to_regclass('public.retry_probe') IS NULL;").decode().strip() == 't'
    if not retry_probe_absent:
        raise ValueError('The partial-target probe survived the schema reset')
    result = {'verified': True, 'targetVersion': '12.0.1', 'health': health, 'webHTTP': web_status,
              'messagesPreserved': len(expected['posts']), 'originalPostIdsPreserved': True,
              'totalUserMessages': len(sql_rows),
              'privateChannelPreserved': True, 'attachmentSHA256': attachment_hash,
              'avatarSHA256': avatar_hash, 'filesMatch': True, 'database': 'PostgreSQL 15',
              'containers': containers, 'sharedVolume': data_volume,
              'retryProbeAbsent': retry_probe_absent,
              'runId': os.environ.get('GITHUB_RUN_ID', 'local'),
              'alreadyMigratedAtStart': os.environ.get('ALREADY_MIGRATED', 'false')}
    write_json('verification.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def mark():
    result = json.loads((OUT / 'verification.json').read_text())
    if not result.get('verified'):
        raise ValueError('Verification has not passed')
    if not MARKER.exists():
        private_write(MARKER, json.dumps(result, indent=2))
        print('Created persistent .migration_completed only after successful verification.')
    else:
        print('Existing success marker preserved.')


def collect():
    OUT.mkdir(exist_ok=True)
    for args, name in [(['ps', '-a', '--filter', 'label=com.docker.compose.project=mattermostpr4', '--format', '{{.Names}} | {{.Image}} | {{.Status}}'], 'containers.txt'),
                       (['logs', '--tail', '100', 'mattermost'], 'mattermost.log')]:
        result = subprocess.run([DOCKER, *args], capture_output=True, cwd=ROOT)
        (OUT / name).write_text(redact((result.stdout + result.stderr).decode(errors='replace')))
    for path in OUT.rglob('*'):
        if path.is_file():
            for key in REQUIRED:
                if os.environ.get(key) and os.environ[key].encode() in path.read_bytes():
                    raise ValueError('Secret value detected in artifact: ' + path.name)
    print('Collected diagnostic evidence; secret-value audit passed.')


def cleanup():
    if ENV.exists():
        if ENV_OWNER.is_file() and ENV_OWNER.read_text() == hashlib.sha256(ENV.read_bytes()).hexdigest():
            ENV.unlink()
            ENV_OWNER.unlink()
        else:
            print('Existing .env.migration was not created by this run; left unchanged.')
    else:
        ENV_OWNER.unlink(missing_ok=True)
    (STATE / 'migration.load').unlink(missing_ok=True)
    print('Removed temporary configuration files; volumes and private backups retained.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['configure', 'status', 'backup', 'migrate', 'deploy', 'verify', 'mark', 'collect', 'cleanup'])
    args = parser.parse_args()
    try:
        globals()[args.command]()
    except Exception as error:
        print(redact(str(error)), file=sys.stderr)
        raise SystemExit(1)

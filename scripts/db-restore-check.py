import fcntl
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

TARGETS = {
    'portal': (
        '/opt/schutz/employee-portal-p0',
        'employee-portal-p0-db-1',
        '.fastapi-release.lock',
    ),
    'management': (
        '/opt/schutz-monitoring',
        'schutz-monitoring-management-db-1',
        '.management-release.lock',
    ),
}

def run(args, **kwargs):
    result = subprocess.run(
        args, timeout=240, stderr=subprocess.PIPE, **kwargs
    )
    if result.returncode:
        raise RuntimeError('COMMAND_FAILED: ' + args[0])
    return result

def inspect(container):
    result = run(
        ['docker', 'inspect', container], stdout=subprocess.PIPE
    )
    return json.loads(result.stdout)[0]

def psql(container, sql):
    return run([
        'docker', 'exec', '-i', container, 'sh', '-c',
        'exec psql -X -Atq -v ON_ERROR_STOP=1 '
        '-U "$POSTGRES_USER" -d "$POSTGRES_DB"',
    ], input=sql.encode(), stdout=subprocess.PIPE)

def main():
    target = sys.argv[1]
    root, source, lock_name = TARGETS[target]
    root = Path(root)
    os.umask(0o077)

    lock = (root / lock_name).open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    before = inspect(source)
    if before['State'].get('Health', {}).get('Status') != 'healthy':
        raise RuntimeError('SOURCE_DB_NOT_HEALTHY')

    size = int(psql(
        source, 'SELECT pg_database_size(current_database());'
    ).stdout)
    if shutil.disk_usage(root).free < max(1024**3, size * 3):
        raise RuntimeError('INSUFFICIENT_FREE_DISK_SPACE')

    backup_root = root / '.db-backups'
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup_root.chmod(0o700)
    backup = Path(tempfile.mkdtemp(
        prefix=target + '-', dir=backup_root
    ))
    archive = backup / 'database.dump'
    temporary = 'schutz-restore-check-' + uuid.uuid4().hex[:12]

    session = subprocess.Popen([
        'docker', 'exec', '-i', source, 'sh', '-c',
        'exec psql -X -Atq -v ON_ERROR_STOP=1 '
        '-U "$POSTGRES_USER" -d "$POSTGRES_DB"',
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
       stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def query(sql):
        session.stdin.write(sql + '\n')
        session.stdin.flush()
        if not select.select([session.stdout], [], [], 90)[0]:
            raise RuntimeError('SOURCE_QUERY_TIMEOUT')
        value = session.stdout.readline().strip()
        if not value:
            raise RuntimeError('SOURCE_QUERY_FAILED')
        return value

    table_sql = (
        "SELECT coalesce(json_agg(tablename ORDER BY tablename),'[]') "
        "FROM pg_tables WHERE schemaname='public';"
    )

    try:
        snapshot = query(
            "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; "
            "SET LOCAL statement_timeout='60s'; "
            "SELECT pg_export_snapshot();"
        )
        if not re.fullmatch(r'[0-9A-Fa-f-]+', snapshot):
            raise RuntimeError('INVALID_SNAPSHOT')

        tables = json.loads(query(table_sql))
        if not tables:
            raise RuntimeError('NO_PUBLIC_TABLES')

        counts = {}
        for table in tables:
            identifier = '"' + table.replace('"', '""') + '"'
            counts[table] = int(query(
                'SELECT count(*) FROM public.' + identifier + ';'
            ))

        with archive.open('wb') as output:
            run([
                'docker', 'exec', source, 'sh', '-c',
                'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
                '-Fc --no-owner --no-acl --snapshot="$1"',
                'sh', snapshot,
            ], stdout=output)

        session.communicate('ROLLBACK;\n', timeout=20)
        if session.returncode or archive.stat().st_size == 0:
            raise RuntimeError('BACKUP_FAILED')

        (backup / 'table-counts.json').write_text(
            json.dumps(counts, sort_keys=True)
        )
        print('DB_BACKUP_CREATED', flush=True)

        run([
            'docker', 'run', '-d', '--name', temporary,
            '--network', 'none', '--memory', '256m', '--cpus', '0.5',
            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
            '-e', 'POSTGRES_USER=restorecheck',
            '-e', 'POSTGRES_DB=restorecheck',
            before['Image'],
        ], stdout=subprocess.DEVNULL)

        for _ in range(60):
            ready = subprocess.run([
                'docker', 'exec', temporary, 'pg_isready',
                '-h', '127.0.0.1',
                '-U', 'restorecheck', '-d', 'restorecheck',
            ], stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, timeout=10)
            if ready.returncode == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError('RESTORE_DB_NOT_READY')

        with archive.open('rb') as data:
            run([
                'docker', 'exec', '-i', temporary,
                'pg_restore', '--exit-on-error',
                '--no-owner', '--no-acl',
                '-U', 'restorecheck', '-d', 'restorecheck',
            ], stdin=data, stdout=subprocess.DEVNULL)

        restored_tables = json.loads(
            psql(temporary, table_sql).stdout
        )
        if tables != restored_tables:
            raise RuntimeError('RESTORED_TABLE_SET_MISMATCH')

        for table, count in counts.items():
            identifier = '"' + table.replace('"', '""') + '"'
            restored = int(psql(
                temporary,
                'SELECT count(*) FROM public.' + identifier + ';'
            ).stdout)
            if restored != count:
                raise RuntimeError('RESTORED_ROW_COUNT_MISMATCH')

        after = inspect(source)
        if (
            after['Id'] != before['Id']
            or after['RestartCount'] != before['RestartCount']
            or after['State'].get('Health', {}).get('Status') != 'healthy'
        ):
            raise RuntimeError('SOURCE_DB_STATE_CHANGED')

        print('DB_RESTORE_TABLE_COUNTS_MATCH', flush=True)
        print('SOURCE_DB_UNCHANGED', flush=True)
        (backup / 'verified.txt').write_text(
            'DB_BACKUP_RESTORE_SUCCESS\n'
        )
    finally:
        if session.poll() is None:
            try:
                session.communicate('ROLLBACK;\n', timeout=10)
            except Exception:
                session.kill()
                session.wait(timeout=10)

        cleanup = subprocess.run([
            'docker', 'rm', '-fv', temporary,
        ], stdout=subprocess.DEVNULL,
           stderr=subprocess.DEVNULL, timeout=30)
        if cleanup.returncode:
            exists = subprocess.run([
                'docker', 'inspect', temporary,
            ], stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, timeout=10)
            if exists.returncode == 0:
                raise RuntimeError('TEMPORARY_DB_CLEANUP_FAILED')

    print('DB_BACKUP_RESTORE_SUCCESS', flush=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        detail = (
            str(error) if type(error) is RuntimeError
            else type(error).__name__
        )
        print('DB_BACKUP_RESTORE_FAILED: ' + detail, file=sys.stderr)
        raise SystemExit(1)

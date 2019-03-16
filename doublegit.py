import argparse
import itertools
import logging
import os
import re
import sqlite3
import subprocess


logger = logging.getLogger(__name__)


_re_fetch = re.compile(r' ([+t*! -]) +([^ ]+|\[[^\]]+\]) +'
                       r'([^ ]+) +-> +([^ ]+)(?: +(.+))?$')


class Operation(object):
    FAST_FORWARD = ' '
    FORCED = '+'
    PRUNED = '-'
    TAG = 't'
    NEW = '*'
    REJECT = '!'
    NOOP = '='


def fetch(repository):
    cmd = ['git', 'fetch', '--prune', '--all']
    proc = subprocess.Popen(cmd, cwd=repository,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, err = proc.communicate()
    if proc.wait() != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)

    new = []
    changed = []
    removed = []
    tags = []
    for line in err.splitlines():
        line = line.decode('utf-8')
        m = _re_fetch.match(line)
        if m is not None:
            logger.info("> %s", line)
            op, summary, from_, to, reason = m.groups()

            if op == Operation.NEW:
                new.append(to)
            elif op in (Operation.FAST_FORWARD, Operation.FORCED):
                changed.append(to)
            elif op == Operation.PRUNED:
                removed.append(to)
            elif op == Operation.TAG:
                tags.append(to)
            elif op == Operation.REJECT:
                raise ValueError("Error updating ref %s" % to)
            else:
                raise RuntimeError
        else:
            logger.info("! %s", line)
    return new, changed, removed


def get_sha(repository, ref):
    cmd = ['git', 'rev-parse', ref]
    sha = subprocess.check_output(cmd, cwd=repository)
    return sha.decode('utf-8').strip()


def make_branch(repository, name, sha):
    cmd = ['git', 'branch', '-f', name, sha]
    subprocess.check_call(cmd, cwd=repository)


def included_branches(repository, target):
    cmd = ['git', 'branch', '--merged', target]
    out = subprocess.check_output(cmd, cwd=repository)
    refs = []
    for line in out.splitlines():
        refs.append(line.decode('utf-8').strip())
    return refs


def delete_branch(repository, ref):
    cmd = ['git', 'branch', '-D', ref]
    subprocess.check_call(cmd, cwd=repository)


def update(repository):
    # Check Git repository (bare)
    if (not os.path.exists(os.path.join(repository, 'refs')) or
            not os.path.exists(os.path.join(repository, 'objects'))):
        raise ValueError("%s is not a Git repository" % repository)

    # Open database
    db_path = os.path.join(repository, 'gitarchive.sqlite3')
    if not os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            '''
            CREATE TABLE refs(
                remote TEXT NOT NULL,
                name TEXT NOT NULL,
                from_date DATETIME NOT NULL,
                to_date DATETIME NULL,
                sha TEXT NOT NULL
            );
            ''',
        )
    else:
        conn = sqlite3.connect(db_path)

    # Do fetch
    new, changed, removed = fetch(repository)

    # Update database
    for ref in itertools.chain(removed, changed):
        remote, name = ref.split('/', 1)
        conn.execute(
            '''
            UPDATE refs SET to_date=DATETIME()
            WHERE remote=? AND name=?
            ORDER BY from_date DESC
            LIMIT 1;
            ''',
            [remote, name],
        )
    for ref in itertools.chain(changed, new):
        sha = get_sha(repository, ref)
        remote, name = ref.split('/', 1)
        conn.execute(
            '''
            INSERT INTO refs(remote, name, from_date, to_date, sha)
            VALUES(?, ?, DATETIME(), NULL, ?);
            ''',
            [remote, name, sha],
        )

    # Create refs to prevent garbage collection
    for ref in itertools.chain(changed, new):
        sha = get_sha(repository, ref)
        make_branch(repository, 'keep-%s' % sha, sha)

    # Remove superfluous branches
    for ref in itertools.chain(changed, new):
        sha = get_sha(repository, ref)
        keeper = 'keep-%s' % sha
        for br in included_branches(repository, sha):
            if br != keeper:
                delete_branch(repository, br)

    conn.commit()


def main():
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument('repository')

    args = parser.parse_args()
    update(args.repository)


if __name__ == '__main__':
    main()

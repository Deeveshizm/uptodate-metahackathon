"""Persistent server store; preserves the existing repository's SQL contract.

All runtime tables live in a non-exposed schema, independent of Supabase Auth.
Connections are short lived and use no prepared statements (Supavisor compatible).
Schema changes are applied explicitly, never during an API container cold start.
"""
import os
import re
from contextlib import contextmanager

import psycopg

SCHEMA = 'uptodate_runtime'
VERSION = 1
WRITE_LOCK = 740817315


class Row:
    def __init__(self, names, values):
        self.names, self.values = names, tuple(values)

    def __getitem__(self, key):
        return self.values[self.names.index(key)] if isinstance(key, str) else self.values[key]

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)

    def keys(self):
        return self.names


def row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Row(names, values)


def query(sql):
    # Translate only bind markers outside quoted SQL, preserving literal '?' and
    # escaping percent signs for psycopg's parameter protocol. Values stay bound.
    def token(match):
        value = match.group()
        return '%s' if value == '?' else value.replace('%', '%%')
    sql = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\?|%", token, sql)
    if re.match(r'\s*INSERT OR IGNORE\b', sql, re.I):
        sql = re.sub(r'INSERT OR IGNORE', 'INSERT', sql, count=1, flags=re.I)
        sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    return sql


class Connection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        if sql.strip().upper() == 'BEGIN IMMEDIATE':
            # Protect short read/check/write sections, including spending limits,
            # across every API/worker replica rather than a per-process mutex.
            return self.connection.execute('SELECT pg_advisory_xact_lock(%s)', (WRITE_LOCK,))
        return self.connection.execute(query(sql), params)


class PostgresStore:
    def __init__(self, url):
        self.path = url
        with self.connect() as db:
            version = db.execute('SELECT version FROM schema_version WHERE id=1').fetchone()
            if not version or version['version'] != VERSION:
                raise RuntimeError('Apply the UpToDate runtime database migration before starting the server')

    @contextmanager
    def connect(self):
        options = dict(prepare_threshold=None, row_factory=row_factory, connect_timeout=10,
                       application_name='uptodate')
        # Local integration tests use a Unix socket; hosted connections require TLS.
        if os.environ.get('UPTODATE_ENV') == 'cloud':
            options['sslmode'] = 'require'
        with psycopg.connect(self.path, **options) as connection:
            # Transaction-local settings work through transaction poolers and do
            # not leak into the next tenant using the underlying connection.
            connection.execute(f"SET LOCAL search_path TO {SCHEMA},pg_catalog; "
                               "SET LOCAL statement_timeout TO '30s'; "
                               "SET LOCAL lock_timeout TO '10s'; "
                               "SET LOCAL extra_float_digits TO 3")
            yield Connection(connection)

"""Single-instance SaaS prototype storage: hashed client keys and owned jobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

# SQLite classifies WAL teardown races, lock contention and antivirus-style file
# locks as operational errors without a dedicated code, so match on the text.
TRANSIENT_SQLITE_ERRORS=('readonly database','database is locked','database table is locked','unable to open database')


def _transient(exc):
    message=str(exc).lower()
    return any(fragment in message for fragment in TRANSIENT_SQLITE_ERRORS)


class ServiceStore:
    def __init__(self,root,max_active_per_client=3,max_active_total=12):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        import os
        if os.name=='posix':self.root.chmod(0o700)
        self.database=self.root/'service.sqlite3';self.max_active_per_client=max_active_per_client;self.max_active_total=max_active_total
        with self.connection() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS clients(id TEXT PRIMARY KEY,name TEXT NOT NULL,key_hash TEXT UNIQUE NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,client_id TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,
              config TEXT NOT NULL,active_revision INTEGER NOT NULL DEFAULT 0,next_revision INTEGER NOT NULL DEFAULT 1,idempotency_key TEXT,
              error TEXT,result TEXT,UNIQUE(client_id,idempotency_key));
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,client_id TEXT NOT NULL,operation TEXT NOT NULL,status TEXT NOT NULL,
              revision INTEGER NOT NULL,created REAL NOT NULL,result TEXT,error TEXT);
            CREATE INDEX IF NOT EXISTS owned_jobs ON jobs(client_id,created);''')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.database,timeout=10);db.row_factory=sqlite3.Row
        try:
            yield db;db.commit()
        except Exception:
            db.rollback();raise
        finally:db.close()

    def _write(self, body, attempts=4):
        """Run a write transaction, retrying the transient SQLite failures.

        In WAL mode SQLite tears down ``-wal``/``-shm`` when the last connection
        closes. This store opens one connection per operation, and the API and
        its worker run as separate processes, so the two sides can race on that
        teardown and surface ``attempt to write a readonly database`` — reported
        as SQLITE_READONLY, which SQLite never retries on its own.

        ``body`` must be safe to repeat: it receives a fresh connection and is
        rolled back before any retry, so it either commits once or not at all.
        """
        for attempt in range(attempts):
            db=sqlite3.connect(self.database,timeout=10);db.row_factory=sqlite3.Row
            try:
                result=body(db);db.commit();return result
            except sqlite3.OperationalError as exc:
                try:db.rollback()
                except sqlite3.Error:pass
                if attempt==attempts-1 or not _transient(exc):raise
                time.sleep(0.05*(2**attempt))
            except Exception:
                try:db.rollback()
                except sqlite3.Error:pass
                raise
            finally:db.close()

    def create_client(self,name):
        if not isinstance(name,str) or not 1<=len(name)<=80:raise ValueError('client name must have 1–80 characters')
        client=uuid.uuid4().hex;key=secrets.token_urlsafe(32)
        with self.connection() as db:db.execute('INSERT INTO clients(id,name,key_hash) VALUES(?,?,?)',(client,name,hashlib.sha256(key.encode()).hexdigest()))
        return {'id':client,'name':name,'apiKey':key}

    def authenticate(self,key):
        if not isinstance(key,str) or not 20<=len(key)<=128:return None
        digest=hashlib.sha256(key.encode()).hexdigest()
        with self.connection() as db:row=db.execute('SELECT id FROM clients WHERE key_hash=? AND revoked=0',(digest,)).fetchone()
        return row['id'] if row else None

    def revoke_client(self,client):
        with self.connection() as db:db.execute('UPDATE clients SET revoked=1 WHERE id=?',(client,))

    def client_active(self,client):
        with self.connection() as db:return db.execute('SELECT 1 FROM clients WHERE id=? AND revoked=0',(client,)).fetchone() is not None

    def _quota(self,db,client):
        completed=db.execute('SELECT COUNT(*) FROM jobs WHERE client_id=?',(client,)).fetchone()[0]
        if completed>=20:raise ValueError('stored job quota exceeded')
        directory=self.root/'clients'/client
        used=sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()) if directory.exists() else 0
        if used+100*1024*1024>2*1024**3:raise ValueError('client storage quota exceeded')
        statuses="('uploading','queued','running','editing','matching')"
        owned=db.execute(f'SELECT COUNT(*) FROM jobs WHERE client_id=? AND status IN {statuses}',(client,)).fetchone()[0]
        total=db.execute(f'SELECT COUNT(*) FROM jobs WHERE status IN {statuses}').fetchone()[0]
        if owned>=self.max_active_per_client or total>=self.max_active_total:raise ValueError('queue quota exceeded')

    def new_job(self,client,config,idempotency_key=None):
        if idempotency_key is not None and (not isinstance(idempotency_key,str) or not 1<=len(idempotency_key)<=128):raise ValueError('invalid idempotency key')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if idempotency_key:
                row=db.execute('SELECT id,config FROM jobs WHERE client_id=? AND idempotency_key=?',(client,idempotency_key)).fetchone()
                if row:
                    if json.loads(row['config'])!=config:raise ValueError('idempotency key conflicts with different parameters')
                    return row['id'],False
            self._quota(db,client);job=uuid.uuid4().hex;now=time.time()
            db.execute('INSERT INTO jobs(id,client_id,status,created,updated,config,idempotency_key) VALUES(?,?,?,?,?,?,?)',
                       (job,client,'uploading',now,now,json.dumps(config),idempotency_key))
        self.job_root(client,job).mkdir(parents=True,exist_ok=True)
        return job,True

    def job_root(self,client,job):
        import re
        if not re.fullmatch(r'[0-9a-f]{32}',client) or not re.fullmatch(r'[0-9a-f]{32}',job):raise ValueError('invalid job identity')
        return self.root/'clients'/client/'jobs'/job

    def job(self,client,job):
        with self.connection() as db:row=db.execute('SELECT * FROM jobs WHERE id=? AND client_id=?',(job,client)).fetchone()
        if not row:raise KeyError('job not found')
        result=dict(row);result['config']=json.loads(result['config']);result['result']=json.loads(result['result']) if result['result'] else None
        return result

    def jobs(self,client):
        with self.connection() as db:rows=db.execute('SELECT id FROM jobs WHERE client_id=? ORDER BY created DESC LIMIT 100',(client,)).fetchall()
        return [self.job(client,row['id']) for row in rows]

    def fail_upload(self,client,job):
        with self.connection() as db:db.execute("UPDATE jobs SET status='failed',error='upload rejected',updated=? WHERE id=? AND client_id=?",(time.time(),job,client))

    def enqueue(self,client,job,operation):
        self.job(client,job)
        if operation not in {'convert','apply','match'}:raise ValueError('unknown operation')
        def body(db):
            db.execute('BEGIN IMMEDIATE');record=db.execute('SELECT * FROM jobs WHERE id=? AND client_id=?',(job,client)).fetchone()
            if operation=='convert' and record['status']!='uploading':raise ValueError('job is not awaiting conversion')
            if operation!='convert' and record['status']!='succeeded':raise ValueError('job is busy or has no completed result')
            if operation!='convert':self._quota(db,client)
            if operation=='apply' and record['next_revision']>8:raise ValueError('revision quota exceeded')
            revision=0 if operation=='convert' else record['next_revision'] if operation=='apply' else record['active_revision']
            task=uuid.uuid4().hex
            db.execute('INSERT INTO tasks(id,job_id,client_id,operation,status,revision,created) VALUES(?,?,?,?,?,?,?)',(task,job,client,operation,'queued',revision,time.time()))
            status={'convert':'queued','apply':'editing','match':'matching'}[operation]
            db.execute('UPDATE jobs SET status=?,updated=?,next_revision=next_revision+? WHERE id=?',(status,time.time(),int(operation=='apply'),job))
            return task,revision,record['active_revision']
        task,revision,previous=self._write(body)
        return {'id':task,'job':job,'operation':operation,'revision':revision,'previousRevision':previous}

    def start_task(self,task):
        def body(db):
            db.execute("UPDATE tasks SET status='running' WHERE id=?",(task['id'],))
            if task['operation']=='convert':db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(time.time(),task['job']))
        self._write(body)

    def finish_task(self,task,result=None,error=None):
        def body(db):
            db.execute('UPDATE tasks SET status=?,result=?,error=? WHERE id=?',('failed' if error else 'succeeded',json.dumps(result) if result else None,error,task['id']))
            if error:
                status='failed' if task['operation']=='convert' else 'succeeded'
                db.execute('UPDATE jobs SET status=?,error=?,updated=? WHERE id=?',(status,error,time.time(),task['job']))
            elif task['operation'] in {'convert','apply'}:
                db.execute("UPDATE jobs SET status='succeeded',active_revision=?,result=?,error=NULL,updated=? WHERE id=?",(task['revision'],json.dumps(result),time.time(),task['job']))
            else:db.execute("UPDATE jobs SET status='succeeded',updated=? WHERE id=?",(time.time(),task['job']))
        self._write(body)

    def task(self,client,task_id):
        with self.connection() as db:row=db.execute('SELECT * FROM tasks WHERE id=? AND client_id=?',(task_id,client)).fetchone()
        if not row:raise KeyError('task not found')
        result=dict(row);result['result']=json.loads(result['result']) if result['result'] else None
        return result

    def activate_revision(self,client,job,revision):
        self.job(client,job)
        def body(db):
            db.execute('BEGIN IMMEDIATE')
            status=db.execute('SELECT status FROM jobs WHERE id=?',(job,)).fetchone()['status']
            if status!='succeeded':raise ValueError('job is busy')
            row=db.execute("SELECT result FROM tasks WHERE job_id=? AND client_id=? AND revision=? AND operation IN ('convert','apply') AND status='succeeded'",(job,client,revision)).fetchone()
            if not row:raise KeyError('revision not found')
            db.execute('UPDATE jobs SET active_revision=?,result=?,updated=? WHERE id=?',(revision,row['result'],time.time(),job))
        self._write(body)

    def recover_interrupted(self):
        # Call once at single-instance service startup, never per HTTP request.
        with self.connection() as db:
            rows=db.execute("SELECT id,operation,job_id FROM tasks WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                db.execute("UPDATE tasks SET status='failed',error='service restarted; task not published' WHERE id=?",(row['id'],))
                db.execute('UPDATE jobs SET status=?,error=?,updated=? WHERE id=?',('failed' if row['operation']=='convert' else 'succeeded','service restarted; previous revision retained',time.time(),row['job_id']))
            db.execute("UPDATE jobs SET status='failed',error='upload interrupted' WHERE status='uploading'")


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['create-client','revoke-client']);parser.add_argument('--root',type=Path,default=Path('.saas-data'));parser.add_argument('--name');parser.add_argument('--client')
    args=parser.parse_args();store=ServiceStore(args.root)
    if args.command=='create-client':print(json.dumps(store.create_client(args.name or 'Client'),ensure_ascii=False))
    else:store.revoke_client(args.client);print('revoked')


if __name__=='__main__':
    main()

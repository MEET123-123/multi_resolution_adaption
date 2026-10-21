"""Backfill original icons and continuously follow newly collected applications."""
import argparse
import concurrent.futures
import json
import os
import pathlib
import sqlite3
import threading
import time
import traceback
import urllib.error

from crawl import Crawler, dump, now
from icon_assets import download_icon, initialize_icons, database_write_lock


class Worker:
    def __init__(self, args):
        self.args = args
        self.folder = pathlib.Path(args.output).resolve()
        self.db = sqlite3.connect(self.folder / 'crawl.sqlite3', timeout=60)
        with database_write_lock(self.folder, self.db):
            initialize_icons(self.db)
        self.rate_lock = threading.Lock()
        self.last_start = 0
        self.started = now()
        self.processed = 0
        self.last_export = 0

    def discover(self):
        with database_write_lock(self.folder, self.db):
            self.db.execute('''INSERT OR IGNORE INTO icons(app_id,source_url)
                SELECT a.app_id,json_extract(a.record,'$.icon_url') FROM apps a
                LEFT JOIN icons i ON a.app_id=i.app_id WHERE a.status='done' AND i.app_id IS NULL''')
            self.db.execute("UPDATE icons SET status='missing_url',error='No official icon URL' WHERE status='pending' AND (source_url IS NULL OR source_url='')")
            self.db.commit()

    def fetch(self, ident, url):
        with self.rate_lock:
            time.sleep(max(0, self.args.interval - (time.monotonic() - self.last_start)))
            self.last_start = time.monotonic()
        return download_icon(self.folder, ident, url)

    def progress(self, state='running', error=None):
        self.discover()
        counts = dict(self.db.execute('SELECT status,count(*) FROM icons GROUP BY status'))
        if state == 'complete' and any(count for key, count in counts.items() if key != 'done'):
            state = 'complete_with_errors'
        value = {'pid': os.getpid(), 'state': state, 'started_at': self.started, 'at': now(),
                 'icons': counts, 'processed_this_run': self.processed, 'error': error}
        (self.folder / 'icons_progress.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print(dump(value), flush=True)

    def export(self):
        args = argparse.Namespace(output=str(self.folder), interval=0.35,
                    max_apps=0, max_seconds=0, max_pages=1000, export_only=True)
        crawler = Crawler(args)
        try:
            crawler.export('running_checkpoint_icons_updated')
            self.last_export = time.monotonic()
        finally:
            crawler.db.close()

    def run(self):
        self.progress()
        next_export = 1000
        caught_up_exported = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.args.workers) as pool:
            try:
                while True:
                    if (self.folder / 'STOP').exists() or (self.folder / 'STOP_ICONS').exists():
                        self.export()
                        self.progress('stopped')
                        return
                    self.discover()
                    batch = self.db.execute('''SELECT app_id,source_url,attempts FROM icons
                        WHERE status='pending' OR (status='error' AND attempts<3 AND retry_at<=?)
                        ORDER BY rowid LIMIT ?''', (time.time(), self.args.workers * 8)).fetchall()
                    if not batch:
                        if not caught_up_exported and time.monotonic() - self.last_export >= 300:
                            self.export()
                            self.progress('following_new_apps')
                            caught_up_exported = True
                        pending_apps = self.db.execute("SELECT count(*) FROM apps WHERE status='pending'").fetchone()[0]
                        job = self.folder / 'background_job.json'
                        state = json.loads(job.read_text(encoding='utf-8')).get('state') if job.exists() else ''
                        retryable = self.db.execute("SELECT count(*) FROM icons WHERE status='error' AND attempts<3").fetchone()[0]
                        if not retryable and (self.args.once or state in ('stopped', 'error')
                                or (not pending_apps and state in ('complete', 'finishing_icons'))):
                            self.export()
                            self.progress('complete')
                            return
                        time.sleep(5)
                        continue
                    caught_up_exported = False
                    futures = {pool.submit(self.fetch, ident, url): (ident, attempts)
                               for ident, url, attempts in batch}
                    access_error = None
                    for future in concurrent.futures.as_completed(futures):
                        ident, attempts = futures[future]
                        try:
                            result = future.result()
                            with database_write_lock(self.folder, self.db):
                                self.db.execute('''UPDATE icons SET status='done',local_path=?,sha256=?,size_bytes=?,
                                    width=?,height=?,content_type=?,downloaded_at=?,error=NULL,attempts=attempts+1
                                    WHERE app_id=?''', (result['local_path'], result['sha256'], result['size_bytes'],
                                    result['width'], result['height'], result['content_type'], now(), ident))
                                self.db.commit()
                        except Exception as error:
                            code = error.code if isinstance(error, urllib.error.HTTPError) else None
                            status = 'missing' if code in (404, 410) else 'error'
                            with database_write_lock(self.folder, self.db):
                                self.db.execute('''UPDATE icons SET status=?,error=?,attempts=attempts+1,retry_at=?
                                    WHERE app_id=?''', (status, str(error), time.time() + 10 * 2**attempts, ident))
                                self.db.commit()
                            if code in (401, 403, 429):
                                access_error = 'Image CDN access/rate limit: ' + str(code)
                        self.processed += 1
                    if access_error:
                        raise RuntimeError(access_error)
                    if self.processed >= next_export:
                        self.export()
                        next_export += 1000
                    self.progress()
            except Exception as error:
                self.progress('error', str(error))
                traceback.print_exc()
                raise
            finally:
                self.db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/huawei_appgallery/data')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--interval', type=float, default=0.1, help='Minimum global interval between image requests')
    parser.add_argument('--once', action='store_true', help='Backfill current records and exit instead of following new apps')
    args = parser.parse_args()
    if args.workers not in range(1, 5) or args.interval < 0.1:
        parser.error('Use 1-4 workers and an interval >= 0.1 seconds')
    Worker(args).run()

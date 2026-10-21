"""Resumable CN AppGallery public metadata collector (Python standard library)."""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import html
import json
import pathlib
import re
import sqlite3
import sys
import time
import urllib.error

from probe import Client, HOST
from icon_assets import ICON_FIELDS, initialize_icons, icon_metadata, export_lock, database_write_lock

APP_CATEGORIES = {
    '影音娱乐', '实用工具', '社交通讯', '教育', '新闻阅读', '拍摄美化', '美食',
    '出行导航', '旅游住宿', '购物比价', '商务', '儿童', '金融理财', '运动健康', '便捷生活', '汽车'
}
GAME_CATEGORIES = {'游戏', '角色扮演', '休闲益智', '经营策略', '体育竞速', '动作射击', '棋牌桌游'}


def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def app_id(item):
    value = str(item.get('appid') or item.get('appId') or '')
    if re.fullmatch(r'C\d+', value):
        return value
    match = re.match(r'app\|(C\d+)(?:\W|_|$)', str(item.get('detailId', '')))
    return match[1] if match else None


def text_value(value):
    if value is None:
        return ''
    if isinstance(value, str):
        value = re.sub(r'<br\s*/?>', '\n', value, flags=re.I)
        value = re.sub(r'</(?:p|div|li)>', '\n', value, flags=re.I)
        return html.unescape(re.sub(r'<[^>]+>', '', value)).replace('\r\n', '\n').strip()
    return dump(value)


def detail_record(data, expected_id, seed):
    cards = collections.defaultdict(list)
    for card in data.get('layoutData', []):
        cards[card.get('layoutName', '')].extend(card.get('dataList', []))
    def first(name):
        return next(iter(cards[name]), {})
    hidden = first('detailhiddencard')
    if app_id(hidden) != expected_id:
        return None  # Removed applications can return only recommendations, with rtnCode=0.
    head = first('detailheadcard')
    info = first('detailappinfocard')
    about = first('pcappinfocard')
    intro = first('detailappintrocard')
    stats = first('appdetaildatacard')
    category = seed.get('kindName', '')
    if hidden.get('ctype') != 0 or category not in APP_CATEGORIES:
        return None
    raw_intro = intro.get('appIntro', '')
    if not isinstance(raw_intro, str):
        raise ValueError('Unrecognized appIntro schema: ' + expected_id)
    description = text_value(raw_intro)
    update_cards = {k: v for k, v in cards.items()
                    if any(word in k.lower() for word in ('update', 'changelog', 'newfeature'))}
    updates = [text_value(item.get('body') or item.get('text') or item.get('intro'))
               for items in update_cards.values() for item in items]
    record = {
        'app_id': expected_id, 'name': hidden.get('name') or head.get('name'),
        'package_name': hidden.get('package') or info.get('package'),
        'category': category, 'subcategory': seed.get('tagName', ''),
        'description': description, 'description_raw': raw_intro,
        'description_length': len(description),
        'description_sha256': hashlib.sha256(raw_intro.encode()).hexdigest(),
        'short_description': hidden.get('editorDescribe') or seed.get('memo', ''),
        'update_notes': '\n\n'.join(filter(None, updates)),
        'developer': info.get('developer') or about.get('developer'),
        'version': info.get('version') or hidden.get('versionName'),
        'version_code': hidden.get('versionCode'),
        'release_date': info.get('releaseDate') or about.get('releaseDate'),
        'size_bytes': hidden.get('fullSize') or hidden.get('size'),
        'size_display': hidden.get('sizeDesc'), 'rating': stats.get('score') or head.get('stars'),
        'rating_count': stats.get('scoredBy') or head.get('gradeCount'),
        'downloads': stats.get('downloads'), 'download_unit': stats.get('downloadUnit'),
        'downloads_display': seed.get('downCountDesc', ''),
        'minimum_age': hidden.get('minAge'), 'price': hidden.get('price'),
        'tariff': info.get('tariffDesc'), 'icon_url': hidden.get('icon'),
        'screenshot_urls': [i for item in cards['detailscreencard'] for i in item.get('images', [])],
        'privacy_policy_url': (about.get('conceal') or {}).get('text', ''),
        'website': about.get('webSite', ''), 'permissions': about.get('appPermission', {}),
        'platform_packing_type': hidden.get('packingType'), 'ctype': hidden.get('ctype'),
        'country': 'CN', 'locale': 'zh_CN',
        'source_url': 'https://appgallery.huawei.com/app/' + expected_id,
        'source_api': HOST + '/uowap/index', 'source_uri': 'app|' + expected_id,
        'collected_at': now(),
    }
    return record


class Crawler:
    def __init__(self, args):
        self.args = args
        self.out = pathlib.Path(args.output).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / 'raw/details').mkdir(parents=True, exist_ok=True)
        (self.out / 'raw/catalog').mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.out / 'crawl.sqlite3', timeout=60)
        with database_write_lock(self.out, self.db):
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.executescript('''
          CREATE TABLE IF NOT EXISTS jobs (
            uri TEXT, page INTEGER, category TEXT, path TEXT, status TEXT DEFAULT 'pending',
            error TEXT, PRIMARY KEY(uri,page));
          CREATE TABLE IF NOT EXISTS apps (
            app_id TEXT PRIMARY KEY, seed TEXT, source TEXT, status TEXT DEFAULT 'pending',
            record TEXT, error TEXT, attempts INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS exclusions (
            app_id TEXT PRIMARY KEY, name TEXT, category TEXT, ctype TEXT, reason TEXT);
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            ''')
            initialize_icons(self.db)
        self.client = None
        self.started = time.monotonic()
        self.requests = 0

    def save_raw(self, subdir, name, value):
        with gzip.open(self.out / 'raw' / subdir / (name + '.json.gz'), 'wt', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)

    def fetch(self, method, **params):
        for attempt in range(3):
            try:
                self.requests += 1
                data = self.client.get(method, **params)
                if data.get('rtnCode', 0) != 0:
                    raise ValueError('API rtnCode=' + str(data.get('rtnCode')))
                return data
            except urllib.error.HTTPError as error:
                body = error.read(2000).decode('utf-8', 'replace')
                if error.code == 403 and '1002' in body and attempt == 0:
                    # Same anonymous session refresh performed by the official website.
                    self.client = Client(self.args.interval)
                    continue
                if error.code in (401, 403, 429):
                    raise RuntimeError('Access or rate limit; stopping without bypass: ' + str(error.code)) from error
                if error.code < 500 or attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
            except (TimeoutError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))

    def enqueue_job(self, uri, category, path, page=1):
        self.db.execute('INSERT OR IGNORE INTO jobs(uri,page,category,path) VALUES(?,?,?,?)',
                        (uri, page, category, path))

    def discover_apps(self, data, source):
        for item in walk(data.get('layoutData', [])):
            ident = app_id(item)
            if not ident or not item.get('name') or 'ctype' not in item:
                continue
            category = item.get('kindName', '')
            if item.get('ctype') != 0 or category in GAME_CATEGORIES or item.get('webApp') == 1:
                self.db.execute('INSERT OR IGNORE INTO exclusions VALUES(?,?,?,?,?)',
                    (ident, item.get('name'), category, str(item.get('ctype')), 'game_or_non_regular_app'))
                continue
            if category not in APP_CATEGORIES:
                continue  # Positive category evidence is required before inclusion.
            self.db.execute('INSERT OR IGNORE INTO apps(app_id,seed,source) VALUES(?,?,?)',
                            (ident, dump(item), source))

    def initialize(self):
        if self.db.execute("SELECT value FROM meta WHERE key='initialized'").fetchone():
            return
        data = self.fetch('internal.getTemplate')
        self.save_raw('catalog', 'template', data)
        with database_write_lock(self.out, self.db):
            for root in data.get('tabInfo', []):
                if root.get('tabEnName') == 'Apps':
                    for category in root.get('tabInfo', []):
                        name = category['tabName']
                        if name in APP_CATEGORIES:
                            self.enqueue_job(category['tabId'], name, name)
                elif root.get('tabEnName') == 'Top':
                    for child in root.get('tabInfo', []):
                        if child.get('tabName') == '应用排行':
                            self.enqueue_job(child['tabId'], '', '应用排行')
            self.db.execute("INSERT INTO meta VALUES('initialized',?)", (now(),))
            self.db.commit()

    def catalog_step(self, job):
        uri, page, category, path = job
        data = self.fetch('internal.getTabDetail', uri=uri, reqPageNum=page, maxResults=25)
        self.save_raw('catalog', hashlib.sha256((uri + ':' + str(page)).encode()).hexdigest()[:24], data)
        with database_write_lock(self.out, self.db):
            self.discover_apps(data, path)
            for tab in data.get('tabInfo', []):
                tab_id = tab.get('tabId')
                if tab_id and tab_id != uri:
                    self.enqueue_job(tab_id, category, path + '/' + tab.get('tabName', ''))
            if data.get('hasNextPage'):
                if page < self.args.max_pages:
                    self.enqueue_job(uri, category, path, page + 1)
                else:
                    self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ('page_cap:' + uri, str(page)))
            self.db.execute("UPDATE jobs SET status='done',error=NULL WHERE uri=? AND page=?", (uri, page))
            self.db.commit()

    def detail_step(self, item):
        ident, seed_json = item
        seed = json.loads(seed_json)
        data = self.fetch('internal.getTabDetail', uri='app|' + ident, reqPageNum=1, maxResults=25)
        record = detail_record(data, ident, seed)
        # Retain original description/metadata cards; omit user review content and list tracking data.
        raw = {k: v for k, v in data.items() if k != 'layoutData'}
        raw['layoutData'] = [card for card in data.get('layoutData', [])
                             if card.get('layoutName') not in ('pcscorecommentlistcard', 'horizonhomecard')]
        self.save_raw('details', ident, raw)
        status = 'done' if record and record['description'] else ('empty_description' if record else 'unavailable')
        with database_write_lock(self.out, self.db):
            self.discover_apps(data, 'recommendations:' + ident)
            self.db.execute('UPDATE apps SET status=?,record=?,error=NULL,attempts=attempts+1 WHERE app_id=?',
                            (status, dump(record) if record else None, ident))
            self.db.commit()

    def stats(self):
        counts = dict(self.db.execute('SELECT status,count(*) FROM apps GROUP BY status'))
        jobs = dict(self.db.execute('SELECT status,count(*) FROM jobs GROUP BY status'))
        return {'at': now(), 'apps': counts, 'catalog_pages': jobs,
                'excluded': self.db.execute('SELECT count(*) FROM exclusions').fetchone()[0],
                'requests_this_run': self.requests,
                'elapsed_seconds_this_run': round(time.monotonic() - self.started)}

    def progress(self):
        value = self.stats()
        (self.out / 'progress.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print(dump(value), flush=True)

    def export(self, reason):
        with export_lock(self.out):
            self._export_locked(reason)

    def _export_locked(self, reason):
        fields = ['app_id', 'name', 'package_name', 'category', 'subcategory', 'description',
                  'description_length', 'short_description', 'update_notes', 'developer', 'version',
                  'version_code', 'release_date', 'size_bytes', 'size_display', 'rating', 'rating_count',
                  'downloads', 'download_unit', 'downloads_display', 'minimum_age', 'price', 'tariff',
                  'source_url', 'collected_at', 'country', 'locale', 'description_raw',
                  'description_sha256', 'privacy_policy_url', 'website', 'icon_url',
                  'screenshot_urls', 'permissions', 'platform_packing_type', 'ctype', 'source_api', 'source_uri']
        fields.extend(ICON_FIELDS)
        lengths = []
        categories = collections.Counter()
        names = []
        with (self.out / 'apps.jsonl').open('w', encoding='utf-8') as js, \
             (self.out / 'apps.csv').open('w', encoding='utf-8-sig', newline='') as cs:
            writer = csv.DictWriter(cs, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            rows = self.db.execute('''SELECT a.record, i.local_path, COALESCE(i.status,'pending'),
                i.sha256,i.size_bytes,i.width,i.height,i.downloaded_at,i.error
                FROM apps a LEFT JOIN icons i ON a.app_id=i.app_id
                WHERE a.status='done' ORDER BY a.app_id''')
            for record_json, *icon_row in rows:
                record = json.loads(record_json)
                record.update(icon_metadata(icon_row))
                js.write(dump(record) + '\n')
                writer.writerow({k: dump(v) if isinstance(v, (list, dict)) else v for k, v in record.items()})
                lengths.append(record['description_length'])
                categories[record['category']] += 1
                if len(names) < 12:
                    names.append({'app_id': record['app_id'], 'name': record['name'],
                                  'description_length': record['description_length']})
        with (self.out / 'failed_or_unavailable.jsonl').open('w', encoding='utf-8') as stream:
            for ident, status, error in self.db.execute("SELECT app_id,status,error FROM apps WHERE status NOT IN ('done','pending')"):
                stream.write(dump({'app_id': ident, 'status': status, 'error': error}) + '\n')
        manifest = self.stats() | {
            'stop_reason': reason, 'scope': 'CN regular applications only; games and quick apps excluded',
            'exported_app_count': len(lengths), 'categories': dict(categories),
            'icon_counts': dict(self.db.execute('SELECT status,count(*) FROM icons GROUP BY status')),
            'description_length': {'min': min(lengths, default=0), 'max': max(lengths, default=0),
                                   'mean': round(sum(lengths) / len(lengths), 1) if lengths else 0},
            'examples': names,
            'limitations': ['Public web catalogs and recommendation graph are not a complete store index.',
                           'An application may have a short official description; descriptions are never expanded by AI.',
                           'Update notes are empty when not supplied in the public detail response.',
                           'CN locale and regular-app category are enforced; platform_packing_type is preserved.',
                           'Application identity is AppGallery app_id; packages are not merged across listings.'],
        }
        (self.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(dump(manifest), flush=True)

    def run(self):
        if self.args.export_only:
            self.export('export_only')
            return
        self.client = Client(self.args.interval)
        self.initialize()
        reason = 'public_frontier_exhausted'
        step = 0
        try:
            while True:
                if (self.out / 'STOP').exists():
                    reason = 'user_stop_file_checkpoint'
                    break
                if self.args.max_seconds and time.monotonic() - self.started >= self.args.max_seconds:
                    reason = 'time_budget_checkpoint'
                    break
                done = self.db.execute("SELECT count(*) FROM apps WHERE status='done'").fetchone()[0]
                if self.args.max_apps and done >= self.args.max_apps:
                    reason = 'app_count_checkpoint'
                    break
                job = self.db.execute("SELECT uri,page,category,path FROM jobs WHERE status='pending' ORDER BY page,rowid LIMIT 1").fetchone()
                item = self.db.execute("SELECT app_id,seed FROM apps WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
                if job and (step % 4 == 0 or not item):
                    self.catalog_step(job)
                else:
                    if not item:
                        break
                    try:
                        self.detail_step(item)
                    except (ValueError, TimeoutError, urllib.error.URLError) as error:
                        with database_write_lock(self.out, self.db):
                            self.db.execute("UPDATE apps SET status='error',error=?,attempts=attempts+1 WHERE app_id=?",
                                            (str(error), item[0]))
                            self.db.commit()
                step += 1
                if step % 100 == 0:
                    self.progress()
                if step % 1000 == 0:
                    self.export('running_checkpoint')
        except KeyboardInterrupt:
            reason = 'interrupted_checkpoint'
        except Exception as error:
            reason = 'stopped_error: ' + str(error)
            raise
        finally:
            self.export(reason)
            self.progress()
        return reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/huawei_appgallery/data')
    parser.add_argument('--interval', type=float, default=0.35,
                        help='Minimum seconds between requests; sequential, no concurrent requests.')
    parser.add_argument('--max-apps', type=int, default=0, help='0 = crawl until public frontier exhausted')
    parser.add_argument('--max-seconds', type=int, default=0, help='0 = no time checkpoint')
    parser.add_argument('--max-pages', type=int, default=1000, help='Defensive per-catalog pagination cap')
    parser.add_argument('--export-only', action='store_true')
    args = parser.parse_args()
    if args.interval < 0.25 or args.max_apps < 0 or args.max_seconds < 0 or args.max_pages < 1:
        parser.error('interval must be >= 0.25; limits must be nonnegative; max-pages must be positive')
    Crawler(args).run()


if __name__ == '__main__':
    main()

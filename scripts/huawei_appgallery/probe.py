"""Inspect the public anonymous website API, without storing session codes."""
import http.cookiejar
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request
import uuid

HOST = 'https://web-drcn.hispace.dbankcloud.com/edge'


class Client:
    def __init__(self, interval=0.5):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.identity = str(uuid.uuid4())
        self.code = 'null'
        self.interval = interval
        self.last = 0
        self.code = self.request('/webedge/getInterfaceCode',
                                 body={'params': {}, 'zone': 'CN', 'locale': 'zh_CN'})

    def request(self, path, params=None, body=None):
        time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
        url = HOST + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json',
                     'Identity-Id': self.identity,
                     'Interface-Code': self.code + '_' + str(int(time.time()*1000)),
                     'User-Agent': 'AppGalleryMetadataResearch/1.0'})
        self.last = time.monotonic()
        with self.opener.open(req, timeout=40) as response:
            return json.load(response)

    def get(self, method, **params):
        return self.request('/uowap/index', dict(method=method, serviceType=20,
                            zone='CN', locale='zh_CN', **params))


if __name__ == '__main__':
    client = Client()
    out = pathlib.Path('artifacts/huawei_appgallery/research')
    for index, uri in enumerate(sys.argv[1:]):
        data = client.get('internal.getTabDetail', uri=uri, reqPageNum=1, maxResults=25)
        (out / f'probe_{index}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(uri, data.get('rtnCode'), data.get('name'), data.get('hasNextPage'), data.get('totalPages'), flush=True)
        for card in data.get('layoutData', []):
            print('  CARD', card.get('layoutName'), card.get('name'),
                  len(card.get('dataList', [])), flush=True)
            if card.get('dataList'):
                first = card['dataList'][0]
                print('  FIELDS', list(first) if isinstance(first, dict) else str(first)[:200], flush=True)

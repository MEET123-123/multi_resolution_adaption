"""Original AppGallery icon assets and cross-process export coordination."""
from contextlib import contextmanager
import hashlib
import io
import os
import pathlib
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

ICON_FIELDS = ['icon_local_path', 'icon_status', 'icon_sha256', 'icon_size_bytes',
               'icon_width', 'icon_height', 'icon_downloaded_at', 'icon_error']


def initialize_icons(db):
    db.execute('''CREATE TABLE IF NOT EXISTS icons (
        app_id TEXT PRIMARY KEY, source_url TEXT, status TEXT DEFAULT 'pending',
        local_path TEXT, sha256 TEXT, size_bytes INTEGER, width INTEGER, height INTEGER,
        content_type TEXT, downloaded_at TEXT, error TEXT, attempts INTEGER DEFAULT 0,
        retry_at REAL DEFAULT 0)''')
    db.commit()


@contextmanager
def export_lock(folder, filename='export.lock'):
    with (pathlib.Path(folder) / filename).open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                time.sleep(0.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def database_write_lock(folder, connection=None):
    with export_lock(folder, 'database_write.lock'):
        try:
            yield
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise


def icon_metadata(row):
    if not row:
        return dict(zip(ICON_FIELDS, ['', 'pending', '', None, None, None, '', '']))
    return dict(zip(ICON_FIELDS, row))


def allowed_icon_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ''
    return (parsed.scheme == 'https' and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and host.startswith('appimg')
            and (host.endswith('.dbankcdn.com') or host.endswith('.dbankcdn.cn')))


class IconRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        if not allowed_icon_url(new_url):
            raise ValueError('Icon redirected outside public image CDN')
        return super().redirect_request(request, fp, code, message, headers, new_url)


def image_info(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 33 and data[-8:] == b'IEND\xaeB`\x82':
        extension = '.png'
        width, height = struct.unpack('>II', data[16:24])
    elif data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):
        extension, width, height = '.jpg', None, None
    elif data[:6] in (b'GIF87a', b'GIF89a') and len(data) >= 10:
        extension = '.gif'
        width, height = struct.unpack('<HH', data[6:10])
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP' and len(data) >= 30:
        extension, width, height = '.webp', None, None
    else:
        raise ValueError('Not a complete supported image (PNG/JPEG/GIF/WebP)')
    try:
        from PIL import Image
    except ImportError:
        pass
    else:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image.verify()
    if width is not None and (width < 1 or height < 1):
        raise ValueError('Invalid icon dimensions')
    return extension, width, height


def download_icon(folder, ident, url):
    if not re.fullmatch(r'C\d+', ident) or not allowed_icon_url(url):
        raise ValueError('Unexpected app ID or icon source URL')
    request = urllib.request.Request(url, headers={'User-Agent': 'AppGalleryMetadataResearch/1.0'})
    opener = urllib.request.build_opener(IconRedirectHandler())
    with opener.open(request, timeout=30) as response:
        data = response.read(10 * 1024 * 1024 + 1)
        content_type = response.headers.get_content_type()
    if len(data) > 10 * 1024 * 1024:
        raise ValueError('Icon exceeds 10 MiB')
    extension, width, height = image_info(data)
    relative = pathlib.Path('icons') / (ident + extension)
    target = pathlib.Path(folder) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.part')
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return {'local_path': relative.as_posix(), 'sha256': hashlib.sha256(data).hexdigest(),
            'size_bytes': len(data), 'width': width, 'height': height, 'content_type': content_type}

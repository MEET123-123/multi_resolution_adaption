"""Bounded, read-only intake validation for untrusted theme ZIP/HWT archives."""
from __future__ import annotations

import io
import stat
import zipfile
from dataclasses import dataclass,asdict
from pathlib import Path


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes:int=100*1024*1024
    max_total_bytes:int=768*1024*1024
    max_entry_bytes:int=64*1024*1024
    max_entries:int=5000
    max_depth:int=3
    max_ratio:float=250.
    max_image_pixels:int=32_000_000


def safe_member(name):
    if not name or '\\' in name or name.startswith('/') or any(c in name for c in ':<>|?*"\x00') or len(name.encode('utf-8'))>1024:
        raise ValueError('unsafe archive path: '+repr(name))
    parts=name.rstrip('/').split('/')
    reserved={'con','prn','aux','nul',*[f'com{i}' for i in range(1,10)],*[f'lpt{i}' for i in range(1,10)]}
    if any(p in {'','.', '..'} or p.endswith((' ','.')) or p.split('.')[0].casefold() in reserved or any(ord(c)<32 for c in p) for p in parts):
        raise ValueError('unsafe or non-portable archive path: '+repr(name))
    return '/'.join(parts).casefold()


def validate_archive(path,limits=None):
    limits=limits or ArchiveLimits();path=Path(path)
    if path.stat().st_size>limits.max_archive_bytes:raise ValueError('theme archive exceeds upload size limit')
    totals={'entries':0,'uncompressedBytes':0,'nestedArchives':0,'images':0}
    def inspect(archive,depth):
        if depth>limits.max_depth:raise ValueError('nested archive depth exceeded')
        seen=set();files=set()
        for info in archive.infolist():
            normalized=safe_member(info.orig_filename)
            if normalized in seen:raise ValueError('duplicate/case-colliding archive member: '+info.filename)
            parts=normalized.split('/')
            if any('/'.join(parts[:i]) in files for i in range(1,len(parts))) or (not info.is_dir() and any(p.startswith(normalized+'/') for p in seen)):
                raise ValueError('archive file/directory collision: '+info.filename)
            seen.add(normalized)
            if not info.is_dir():files.add(normalized)
            if stat.S_ISLNK(info.external_attr>>16):raise ValueError('archive symlinks are not supported')
            if info.flag_bits&1:raise ValueError('encrypted archives are not supported')
            totals['entries']+=1;totals['uncompressedBytes']+=info.file_size
            if totals['entries']>limits.max_entries or totals['uncompressedBytes']>limits.max_total_bytes:
                raise ValueError('archive expansion budget exceeded')
            if info.file_size>limits.max_entry_bytes:raise ValueError('archive member exceeds size limit: '+info.filename)
            if info.file_size/max(1,info.compress_size)>limits.max_ratio:raise ValueError('archive compression ratio exceeds limit: '+info.filename)
            if info.is_dir():continue
            with archive.open(info) as stream:
                prefix=stream.read(4)
                if prefix in {b'PK\x03\x04',b'PK\x05\x06'}:
                    data=prefix+stream.read(limits.max_entry_bytes)
                    totals['nestedArchives']+=1
                    with zipfile.ZipFile(io.BytesIO(data)) as nested:inspect(nested,depth+1)
                elif Path(info.filename).suffix.lower() in {'.png','.jpg','.jpeg','.webp','.gif','.bmp'}:
                    from PIL import Image
                    stream.seek(0)
                    with Image.open(stream) as bitmap:
                        if bitmap.width*bitmap.height>limits.max_image_pixels:raise ValueError('image pixel budget exceeded: '+info.filename)
                        totals['images']+=1
        invalid=archive.testzip()
        if invalid:raise ValueError('archive CRC failed: '+invalid)
    try:
        with zipfile.ZipFile(path) as archive:inspect(archive,0)
    except (zipfile.BadZipFile,NotImplementedError,RuntimeError,OSError) as exc:
        raise ValueError('invalid or unsupported theme archive: '+str(exc)) from exc
    return {'passed':True,'limits':asdict(limits),**totals}

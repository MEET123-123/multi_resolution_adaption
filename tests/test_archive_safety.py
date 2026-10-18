import io
import stat
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from multires_app.engine.archive_safety import ArchiveLimits,validate_archive,safe_member

ROOT=Path(__file__).resolve().parents[1]


class ArchiveSafetyTests(unittest.TestCase):
    def archive(self,root,members):
        path=root/'theme.hwt'
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,data in members:archive.writestr(name,data)
        return path

    def test_nested_archives_are_counted_and_bounded(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            root=Path(tmp);nested=io.BytesIO()
            with zipfile.ZipFile(nested,'w') as archive:archive.writestr('res/icon.png',b'not an image')
            # Use ordinary data here: invalid images must fail their own validation.
            nested=io.BytesIO()
            with zipfile.ZipFile(nested,'w') as archive:archive.writestr('payload.bin',b'abcd')
            path=self.archive(root,[('unlock/lockscreen/manifest.xml','<Lockscreen/>'),('icons',nested.getvalue())])
            report=validate_archive(path);self.assertEqual(report['nestedArchives'],1);self.assertEqual(report['entries'],3)
            with self.assertRaises(ValueError):validate_archive(path,replace(ArchiveLimits(),max_entries=2))
            with self.assertRaises(ValueError):validate_archive(path,replace(ArchiveLimits(),max_depth=0))

    def test_path_traversal_windows_aliases_case_collisions_and_symlinks(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            root=Path(tmp)
            for name in ['../escape','C:/escape','a\\b','a/CON.txt','a/end.','a//b','a/name:stream']:
                with self.assertRaises(ValueError,msg=name):safe_member(name)
                if '\\' in name:continue  # ZipFile writers normalize Windows separators.
                path=self.archive(root,[(name,b'x')])
                with self.assertRaises(ValueError,msg=name):validate_archive(path)
            for members in [[('A.bin',b'a'),('a.bin',b'b')],[('a',b'x'),('a/b',b'y')]]:
                with self.assertRaises(ValueError):validate_archive(self.archive(root,members))
            path=root/'link.hwt';info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
            with zipfile.ZipFile(path,'w') as archive:archive.writestr(info,'../outside')
            with self.assertRaises(ValueError):validate_archive(path)

    def test_expansion_and_upload_limits(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            path=self.archive(Path(tmp),[('huge.bin',b'0'*100000)])
            for limits in [replace(ArchiveLimits(),max_archive_bytes=10),replace(ArchiveLimits(),max_entry_bytes=100),
                           replace(ArchiveLimits(),max_total_bytes=100),replace(ArchiveLimits(),max_ratio=2)]:
                with self.assertRaises(ValueError):validate_archive(path,limits)


if __name__=='__main__':unittest.main()

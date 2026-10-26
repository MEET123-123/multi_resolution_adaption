"""Create a portable archive of locally regenerated synthetic dataset."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import hashlib,json
ROOT=Path(__file__).resolve().parents[2]
src=ROOT/"artifacts"/"learning"/"synthetic-10k-v1"
dst=ROOT/"artifacts"/"learning"/"synthetic-10k-v1.zip"
assert (src/"dataset"/"manifest.json").is_file()
with ZipFile(dst,"w",ZIP_DEFLATED,compresslevel=6) as archive:
    for path in sorted(src.rglob("*")):
        if path.is_file():
            archive.write(path,Path(src.name)/path.relative_to(src))
h=hashlib.sha256(dst.read_bytes()).hexdigest()
print(json.dumps({"archive":str(dst),"sizeBytes":dst.stat().st_size,"sha256":h},ensure_ascii=False,indent=2))

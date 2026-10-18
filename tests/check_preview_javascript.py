"""Check the actual generated preview script with Node, without DOM automation."""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

node=sys.argv[1]
paths=[Path(p) for p in sys.argv[2:]] or list(Path('theme/adaptation').glob('*/scenes/preview.html'))
for path in paths:
    scripts=re.findall(r'<script>(.*?)</script>',path.read_text(encoding='utf-8'),re.S)
    if len(scripts)!=1:raise ValueError('missing preview script: '+str(path))
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
        script=Path(tmp)/'preview.js';script.write_text(scripts[0],encoding='utf-8')
        subprocess.run([node,'--check',str(script)],check=True,capture_output=True)
print(f'JavaScript syntax checked: {len(paths)} previews')

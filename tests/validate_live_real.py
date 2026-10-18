"""Run representative concrete gestures for generated real projects."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.tools.batch_convert import validate_live_project as validate

if __name__=='__main__':
    paths=[Path('theme/adaptation')/slug/'scenes' for slug in sys.argv[1:]] or sorted(Path('theme/adaptation').glob('*/scenes'))
    for path in paths:
        if not (path/'project.json').exists():continue
        report=validate(path)
        print(json.dumps({'theme':path.parent.name,**report},ensure_ascii=True),flush=True)

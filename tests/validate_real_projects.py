"""Validate selected generated projects with the batch checks."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.tools.batch_convert import validate

if __name__=='__main__':
    reports=[]
    for slug in sys.argv[1:] or ['wufu','beihai']:
        directory=Path('theme/adaptation')/slug/'scenes'
        reports.append({'theme':slug,**validate(directory,sorted((directory/'packages').glob('*.hwt')))})
    print(json.dumps(reports,ensure_ascii=False,indent=2))
    sys.exit(int(any(r['failures'] for r in reports)))

"""Record actual v2 HWT commands and pointer states without invoking the OS."""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from multires_app.engine.scene_engine import load_model
from multires_app.interaction.tablet_interactions import InteractionSession
from multires_app.interaction.interaction_history import InteractionHistory, restore_runtime


def asset_metadata(package,model):
    assets={}
    with zipfile.ZipFile(package) as archive:
        for name in model['assetNames']:
            if name.startswith(('unlock/lockscreen/','wallpaper/')) and Path(name).suffix.lower() in {'.png','.jpg','.jpeg','.webp','.gif'}:
                with Image.open(io.BytesIO(archive.read(name))) as bitmap:
                    assets[name]={'width':bitmap.width,'height':bitmap.height}
    return assets


def record_packages():
    directory=ROOT/'theme/tablet-2560x1600/v2'
    output=directory/'interaction-history';output.mkdir(parents=True,exist_ok=True)
    reports=[]
    for name,prefix in [('bear','比邻熊'),('dog','线条小狗')]:
        package=next(directory.glob(prefix+'*交互增强v2.hwt'))
        model=load_model(package,manifest='unlock/lockscreen/1600_2560_manifest.xml')
        session=InteractionSession(model,2560,1600,assets=asset_metadata(package,model),record_history=True)
        character='character-bear' if name=='bear' else 'character-white'
        for _ in range(3):session.gesture(character)
        session.gesture(character,dx=-200)
        if name=='dog':session.gesture('character-brown',dx=-200)
        session.gesture(character,duration=700)
        session.gesture('focus-toggle')
        history=session.journal
        document=history.to_dict()
        document['metadata']['sourceSha256']=hashlib.sha256(package.read_bytes()).hexdigest()
        restored=InteractionHistory.from_dict(json.loads(json.dumps(document)))
        assert restored.current==history.current
        restore_runtime(session.runtime,restored.current['runtime'])
        assert session.value('focus')==0
        path=output/(name+'-history.json')
        path.write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
        reports.append({'theme':name,'package':package.name,'history':path.name,'events':len(history.records)-1,
                        'commands':sum(len(n.get('commands',[])) for n in history.records),
                        'settings':session.saved(),'roundTripPassed':True})
    (output/'summary.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    return reports


if __name__=='__main__':print(json.dumps(record_packages(),ensure_ascii=False,indent=2))

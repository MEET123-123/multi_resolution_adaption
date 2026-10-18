"""Inspect real HWT bytes, then replay exported down/move/up interaction XML.

This is offline verification, never a claim of Huawei native-engine approval.
"""
import argparse
import hashlib
import io
import itertools
import json
import math
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from PIL import Image
from multires_app.engine.scene_engine import Runtime, load_model
from multires_app.interaction.tablet_interactions import InteractionSession, defaults_actions

PREFIX='unlock/lockscreen/'
MANIFESTS={'manifest.xml':(1080,1728),'2560_1600_manifest.xml':(1600,2560),'1600_2560_manifest.xml':(2560,1600)}


def run_interaction_suite(models,assets,theme):
    results=[]
    def check(name,callback):
        try:callback();results.append({'case':name,'passed':True})
        except (AssertionError,ValueError,StopIteration,KeyError) as exc:results.append({'case':name,'passed':False,'error':str(exc) or 'assertion failed'})
    def require(condition,message):
        if not condition:raise AssertionError(message)
    for filename,(model,width,height) in models.items():
        label=filename+': '
        fresh=lambda saved=None,system=None:InteractionSession(model,width,height,assets,saved,system)
        characters=[('character-bear','look',5,'react_at','reaction')] if theme=='bear' else [('character-white','look_a',4,'react_a_at','reaction_a'),('character-brown','look_b',4,'react_b_at','reaction_b')]
        for name,var,count,stamp,reaction in characters:
            def tap(name=name,var=var,stamp=stamp,reaction=reaction):
                s=fresh();old=s.value(var);s.gesture(name)
                require(s.value(var)==old,'tap changed costume')
                require(s.value(stamp)==100,'tap feedback not started')
                s.runtime.system['time']=400;s.runtime.cache.clear()
                require(s.value(reaction)>.8,'feedback peak missing')
                s.runtime.system['time']=800;s.runtime.cache.clear()
                require(s.value(reaction)==0,'feedback did not finish')
                require(s.unlocks()==0,'tap unlocked')
            check(label+name+' tap feedback',tap)
            def swipes(name=name,var=var,count=count):
                s=fresh();old=s.value(var)
                for _ in range(count):s.gesture(name,dx=-width*.12,duration=300)
                require(s.value(var)==old,'left swipe did not wrap')
                s.gesture(name,dx=width*.12,duration=900)
                require(s.value(var)==(old+count-1)%count,'slow right swipe misclassified')
                require(s.value('focus')==0,'swipe also became a hold')
                require(s.unlocks()==0,'horizontal swipe unlocked')
            check(label+name+' left/right/wrap',swipes)
            def hold(name=name,var=var):
                s=fresh();old=s.value(var);s.gesture(name,duration=650)
                require(s.value('focus')==1,'hold did not enter focus')
                require(s.value(var)==old,'hold also changed costume')
                visible={e['name'] for e in s.runtime.snapshot(assets,visible_only=True) if e['tag']=='Button'}
                require('motion' not in visible and 'costume' not in visible,'hidden controls still receive touches')
                require('focus-toggle' in visible,'focus mode has no exit')
                s.gesture('focus-toggle');require(s.value('focus')==0,'focus mode cannot exit')
                require(s.unlocks()==0,'hold unlocked')
            check(label+name+' hold/focus/exit',hold)
            def cancel(name=name,var=var,stamp=stamp):
                s=fresh();old=s.value(var);s.gesture(name,dx=width*.035,duration=800,return_to_start=True)
                require(s.value('focus')==0 and s.value(var)==old and s.value(stamp)==-10000,'drag-away-and-back became a tap/hold')
                s.gesture(name,dx=width*.15,dy=-width*.11)
                require(s.value(var)==old and s.unlocks()==0,'ambiguous diagonal accepted')
            check(label+name+' drag cancellation/diagonal',cancel)
        def orphan():
            s=fresh();before=s.saved()
            for t in model['triggers']:
                if t['action']=='up':s.runtime.execute(t)
            require(s.saved()==before and s.unlocks()==0,'up without down caused an action')
        check(label+'orphan release',orphan)
        def all_controls_unlock():
            initial=fresh();names=[e['name'] for e in initial.runtime.snapshot(assets,visible_only=True) if e['tag']=='Button']
            for name in names:
                s=fresh();before=s.saved();s.gesture(name,dy=-width*.13,duration=1000)
                require(s.unlocks()==1,name+' cannot unlock or unlocks twice')
                require(s.saved()==before,name+' upward swipe also activated its action')
                # A repeated native up must not execute a second unlock.
                owner=next(n['id'] for n in model['nodes'].values() if n['attributes'].get('name')==name)
                for t in model['triggers']:
                    if t['owner']==owner and t['action']=='up':s.runtime.execute(t)
                require(s.unlocks()==1,'duplicate up unlocked twice')
        check(label+'upward unlock from every visible control',all_controls_unlock)
        def small_up():
            s=fresh();before=s.saved();s.gesture(characters[0][0],dy=-width*.07)
            require(s.saved()==before and s.unlocks()==0,'short upward movement triggered an action')
        check(label+'short upward movement',small_up)
        def power():
            for battery,charging,expected in [(0,0,0),(15,0,0),(16,0,1),(10,1,1),(10,3,1),(100,0,1)]:
                s=fresh(system={'battery_level':battery,'battery_state':charging})
                require(s.value('effective_motion')==expected,'wrong low-power boundary')
                require(s.value('motion')==1,'low-power mode overwrote preference')
            s=fresh();s.gesture('motion');require(s.value('motion')==2 and s.value('animation_rate')==.5,'slow motion missing')
            s.gesture('motion');require(s.value('motion')==0 and s.value('effective_motion')==0,'manual pause missing')
            s.runtime.system.update(battery_level=10,battery_state=1);s.runtime.cache.clear()
            require(s.value('effective_motion')==0,'charging overrode manual pause')
        check(label+'low power/charging/manual/slow',power)
        def appearance():
            for hour in [0,6,7,18,19,23]:
                s=fresh(system={'hour24':hour})
                require(s.value('palette')==int(hour<7 or hour>=19),'wrong automatic day/night boundary')
                if theme=='bear':
                    s.gesture('appearance');require(s.value('palette_mode')==0 and s.value('palette')==0,'manual light failed')
                    s.gesture('appearance');require(s.value('palette_mode')==1 and s.value('palette')==1,'manual dark failed')
                    s.gesture('appearance');require(s.value('palette_mode')==2,'automatic mode not restored')
        check(label+'day/night/manual appearance',appearance)
        def persistence():
            s=fresh();s.gesture(characters[0][0],dx=-width*.12);s.gesture('motion');s.gesture(characters[0][0],duration=800)
            saved=s.saved();s.down(width/2,height/2)
            rotated=models['2560_1600_manifest.xml' if width>height else '1600_2560_manifest.xml']
            restored=InteractionSession(rotated[0],rotated[1],rotated[2],assets,saved)
            require(restored.saved()==saved,'settings lost on rotation/resume')
            require(restored.value('press_active')==0,'stale gesture restored')
            restored.gesture('focus-toggle',duration=800)
            for name,value in defaults_actions(theme):require(restored.value(name)==int(value),'reset defaults failed: '+name)
            require(restored.unlocks()==0,'reset unlocked')
        check(label+'persistence/rotation/reset',persistence)
        if theme=='dog':
            def independent():
                s=fresh();s.gesture('character-white',dx=-width*.12)
                require(s.value('look_a')==1 and s.value('look_b')==0,'white/brown looks are coupled')
                s.gesture('costume');require(s.value('look_a')==2 and s.value('look_b')==1,'joint costume action failed')
            check(label+'independent and joint dog looks',independent)
    return results


def _audit_package(path,advanced=None):
    path=Path(path);errors=[];checks=Counter();assets={};models={};image_errors=[]
    with zipfile.ZipFile(path) as z:
        names=z.namelist();checks['zipCrc']+=1
        if z.testzip():errors.append('ZIP CRC failure')
        if len(names)!=len(set(names)):errors.append('duplicate ZIP paths')
        for name in names:
            normalized=name.replace('\\','/')
            parts=PurePosixPath(normalized).parts
            if PurePosixPath(normalized).is_absolute() or '..' in parts or (parts and ':' in parts[0]):errors.append('unsafe ZIP path: '+name)
            if name.endswith('.xml'):
                try:ET.fromstring(z.read(name));checks['wellFormedXml']+=1
                except ET.ParseError as exc:errors.append(name+': '+str(exc))
            if name.lower().endswith(('.png','.jpg','.jpeg')):
                try:
                    with Image.open(io.BytesIO(z.read(name))) as im:
                        im.load();assets[name]={'width':im.width,'height':im.height,'format':im.format};checks['decodedImages']+=1
                except Exception as exc:image_errors.append(name+': '+str(exc))
        for nested in ('icons','com.huawei.android.launcher'):
            try:
                with zipfile.ZipFile(io.BytesIO(z.read(nested))) as inner:
                    if inner.testzip():errors.append(nested+': CRC failure')
                    checks['nestedZipCrc']+=1
                    if nested.endswith('launcher') and any(n.startswith('framework-res-hwext') for n in inner.namelist()):errors.append('phone launcher hwext remains')
                    for name in inner.namelist():
                        if name.lower().endswith('.png'):
                            with Image.open(io.BytesIO(inner.read(name))) as im:im.load();checks['decodedNestedImages']+=1
            except Exception as exc:errors.append(nested+': '+str(exc))
        for name in ('home_wallpaper_0.jpg','unlock_wallpaper_0.jpg'):
            meta=assets.get('wallpaper/'+name,{})
            if (meta.get('width'),meta.get('height'))!=(3200,2560):errors.append('wrong wallpaper dimensions: '+name)
            if meta.get('format')!='JPEG':errors.append('wallpaper is not JPEG: '+name)
            checks['wallpaperDimensions']+=1
        try:
            if ET.fromstring(z.read('description.xml')).findtext('screen')!='WUXGA':errors.append('metadata screen is not WUXGA')
            theme=ET.fromstring(z.read('unlock/theme.xml'))
            if not any(e.get('engineType')=='HWThemeEngine' for e in theme):errors.append('wrong theme engine')
        except Exception as exc:errors.append('metadata: '+str(exc))
        if len([n for n in names if n.startswith('preview/')])>20:errors.append('more than 20 previews')
        for name,size in [('cover_land.jpg',(1920,1080)),('preview_land_unlock_0.jpg',(2160,1080))]:
            meta=assets.get('preview/'+name,{})
            if (meta.get('width'),meta.get('height'))!=size:errors.append('wrong preview dimensions: '+name)
        for name,(width,height) in MANIFESTS.items():
            try:
                model=load_model(path,PREFIX+name)
                if model['root'].get('screenWidth')!=str(width):errors.append('wrong orientation/screenWidth: '+name)
                models[name]=(model,width,height);checks['orientationManifests']+=1
            except Exception as exc:errors.append(name+': '+str(exc))
    errors.extend(image_errors)
    if errors:return {'passed':False,'errors':errors,'checks':dict(checks),'nativeDeviceTest':False}
    is_advanced='gesture_kind' in models['manifest.xml'][0]['variables']
    if advanced is True and not is_advanced:errors.append('advanced interaction state machine missing')
    theme='bear' if any(n['attributes'].get('src','').startswith('ani') for n in models['manifest.xml'][0]['nodes'].values()) else 'dog'
    for filename,(model,width,height) in models.items():
        looks=[{'look':i} for i in range(5)] if theme=='bear' else [{'look_a':a,'look_b':b} for a,b in itertools.product(range(4),repeat=2)]
        if not is_advanced:looks=[{'look':i} for i in range(5 if theme=='bear' else 4)]
        settings=list(itertools.product(range(3) if theme=='bear' else [2],range(3),range(2),[6,7,19],[(15,0),(16,0),(10,1)])) if is_advanced else [(0,1,0,8,(86,0))]
        probes=[({**look,'palette_mode':p,'motion':m,'focus':f},hour,battery,0) for look in looks for p,m,f,hour,battery in settings]
        # Enumerate every frame interval and independent look, including slow mode.
        probes.extend(({**look,'motion':m,'focus':0,'palette_mode':0},8,(86,0),tm) for look in looks for m in [1,2] for tm in range(0,14000,100))
        for state,hour,(battery,charging),tm in probes:
            rt=Runtime(model,width,height,state,time_ms=tm)
            rt.system.update(hour24=hour,hour12=hour%12 or 12,battery_level=battery,battery_state=charging,time=tm)
            for e in rt.snapshot(assets,visible_only=True):
                if e['tag']!='Image' or not e['visible']:continue
                checks['sampledImageReferences']+=1
                if e['asset'] not in assets:errors.append(filename+': missing '+e['asset'])
                r=e['rect'];checks['sampledImageBounds']+=1
                if not all(math.isfinite(v) for v in r.values()) or r['x']<-.5 or r['y']<-.5 or r['x']+r['w']>width+.5 or r['y']+r['h']>height+.5:
                    errors.append(filename+': image outside viewport '+e['id'])
            if rt.issues:errors.extend(filename+': '+str(v) for v in rt.issues.values())
    cases=run_interaction_suite(models,assets,theme) if is_advanced else []
    errors.extend(c['case']+': '+c['error'] for c in cases if not c['passed'])
    checks['interactionScenarios']=len(cases)
    return {'passed':not errors,'errors':list(dict.fromkeys(errors))[:100],'checks':dict(checks),'interactionCases':cases,
            'package':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'interactionSuite':'advanced-down-move-up' if is_advanced else 'legacy-structural-and-resource',
            'nativeDeviceTest':False,'scope':'包内实际文件解码、XML及资源/几何采样、导出XML交互重放；不包含华为原生引擎、主题App导入或真机认证流程。'}


def audit_package(path,advanced=None):
    try:return _audit_package(path,advanced)
    except (OSError,zipfile.BadZipFile,ET.ParseError,ValueError,KeyError) as exc:
        return {'passed':False,'package':str(Path(path).resolve()),'errors':[type(exc).__name__+': '+str(exc)],'checks':{},'nativeDeviceTest':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('packages',type=Path,nargs='+');parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();reports=[audit_package(p) for p in args.packages]
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{'package':str(p),'passed':r['passed'],'checks':r['checks'],'errors':r['errors'][:8]} for p,r in zip(args.packages,reports)],ensure_ascii=False,indent=2))
    if not all(r['passed'] for r in reports):raise SystemExit(1)


if __name__=='__main__':
    main()

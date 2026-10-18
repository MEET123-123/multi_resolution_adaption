"""Bounded, explicit interpreter for legacy Huawei lockscreen XML.

No Python/JavaScript eval is used. Unsupported engine features are diagnostics,
never silently accepted as native-engine behavior.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import operator
import posixpath
import re
import zipfile
from multires_app.interaction.interaction_history import state_changes
from urllib.parse import quote
from functools import lru_cache
from collections import Counter, deque
from pathlib import Path
from xml.etree import ElementTree as ET


VISUAL = {"Group", "Image", "Text", "Time", "Date", "DateTime", "Number", "Button",
          "ImageSeries", "Rectangle", "Video", "Unlocker", "MediaControl",
          "MediaProgressbar", "LrcView"}
REF = re.compile(r"[#@]([A-Za-z_][\w.]*)")


@lru_cache(maxsize=32768)
def parse_expression(text):
    tree=ast.parse(text,mode="eval")
    if sum(1 for _ in ast.walk(tree))>1000: raise ExpressionError("expression too complex")
    return tree


class ExpressionError(ValueError):
    pass


class Evaluator:
    """Small whitelist AST evaluator, including lazy ifelse and MAML strings."""
    def __init__(self, lookup):
        self.lookup = lookup

    def evaluate(self, expression):
        if expression is None:
            raise ExpressionError("missing expression")
        if isinstance(expression, (float, int)):
            return expression
        text = REF.sub(lambda m: "ref(" + repr(m[1]) + ")", str(expression))
        text = re.sub(r"\bnot\s*\(", "logical_not(", text)
        try:
            tree = parse_expression(text)
            result = self._node(tree.body)
            if isinstance(result, float) and not math.isfinite(result):
                raise ExpressionError("non-finite result")
            return result
        except (SyntaxError, TypeError, ZeroDivisionError, OverflowError, IndexError, ValueError) as exc:
            raise ExpressionError(str(exc)) from exc

    def _node(self, n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (str, int, float, bool)):
            return n.value
        if isinstance(n, ast.Name) and n.id in {"true", "false"}:
            return int(n.id == "true")
        if isinstance(n, ast.UnaryOp):
            a = self._node(n.operand)
            if isinstance(n.op, ast.USub): return -float(a)
            if isinstance(n.op, ast.UAdd): return float(a)
            if isinstance(n.op, ast.Not): return int(not a)
        if isinstance(n, ast.BinOp):
            a, b = self._node(n.left), self._node(n.right)
            if isinstance(n.op, ast.Add) and (isinstance(a, str) or isinstance(b, str)):
                def fmt(v): return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
                return fmt(a) + fmt(b)
            ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                   ast.Div: operator.truediv, ast.Mod: operator.mod}
            if type(n.op) in ops: return ops[type(n.op)](float(a), float(b))
        if isinstance(n, ast.Compare) and len(n.ops) == 1:
            ops = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                   ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
            if type(n.ops[0]) in ops: return int(ops[type(n.ops[0])](self._node(n.left), self._node(n.comparators[0])))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
            name = n.func.id
            if name == "ref" and len(n.args) == 1: return self.lookup(self._node(n.args[0]))
            if name == "ifelse":
                if len(n.args) < 3 or len(n.args) % 2 != 1:
                    raise ExpressionError("ifelse requires condition/value pairs and fallback")
                for i in range(0, len(n.args)-1, 2):
                    if self._node(n.args[i]): return self._node(n.args[i+1])
                return self._node(n.args[-1])
            args = [self._node(a) for a in n.args]
            funcs = {"eq": lambda a,b:int(a==b), "ne":lambda a,b:int(a!=b),
                     "lt":lambda a,b:int(a<b), "le":lambda a,b:int(a<=b),
                     "gt":lambda a,b:int(a>b), "ge":lambda a,b:int(a>=b),
                     "abs":abs, "min":min, "max":max, "int":lambda a:int(float(a)),
                     "sin":math.sin, "cos":math.cos, "sqrt":math.sqrt, "atan":math.atan,
                     "logical_not":lambda a:int(not a), "strContains":lambda a,b:int(str(b) in str(a)),
                     "strIsEmpty":lambda a:int(not str(a)), "substr":lambda s,a,b=None:str(s)[int(a):int(a)+int(b)] if b is not None else str(s)[int(a):],
                     "digit":lambda a,b:(abs(int(a))//(10**int(b)))%10,
                     "plus":lambda *a:sum(map(float,a))}
            if name in funcs:
                try: return funcs[name](*args)
                except (ValueError, TypeError, OverflowError) as exc: raise ExpressionError(str(exc)) from exc
            raise ExpressionError("unsupported function: " + name)
        raise ExpressionError("unsupported syntax: " + type(n).__name__)


def indexed_tree(root, file):
    nodes = {}
    def visit(node, path, parent=None, top=None):
        uid = file + "::" + path
        nodes[uid] = {"id":uid, "path":path, "tag":node.tag, "attributes":dict(node.attrib),
                      "parent":parent, "top":top or uid, "children":[]}
        if parent: nodes[parent]["children"].append(uid)
        counts = Counter()
        for child in node:
            if not isinstance(child.tag,str): continue
            counts[child.tag] += 1
            visit(child, path+f"/{child.tag}[{counts[child.tag]}]", uid,
                  uid if parent is None else top)
    visit(root, "/"+root.tag+"[1]")
    # top is the direct child of Lockscreen, not the Lockscreen itself.
    root_id = next(iter(nodes))
    def set_top(uid, top):
        nodes[uid]["top"] = top
        for c in nodes[uid]["children"]: set_top(c,top)
    for uid in nodes[root_id]["children"]: set_top(uid,uid)
    return nodes


def load_model(source: Path, manifest=None):
    """Read lockscreen only; desktop widget manifests are deliberately excluded."""
    if source.is_file() and zipfile.is_zipfile(source):
        archive = zipfile.ZipFile(source)
        names = archive.namelist()
        read = archive.read
    else:
        archive = None
        names = [p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()]
        read = lambda name:(source/name).read_bytes()
    try:
        manifests = [n for n in names if n.lower()=="lockscreen/manifest.xml" or n.lower().endswith("/lockscreen/manifest.xml")]
        if manifest is not None:manifests=[n for n in names if n==manifest]
        if len(manifests) != 1:
            raise ValueError("场景引擎需要唯一的 lockscreen/manifest.xml")
        file = manifests[0]
        root = ET.fromstring(read(file))
        if root.tag != "Lockscreen": raise ValueError("当前场景引擎支持 Lockscreen XML")
        nodes = indexed_tree(root,file)
        variables = {n["attributes"]["name"]: n for n in nodes.values()
                     if n["tag"]=="Var" and n["attributes"].get("name")}
        triggers = []
        for node in nodes.values():
            if node["tag"] != "Trigger": continue
            owner = node["parent"]
            while owner and nodes[owner]["tag"] in {"Triggers", "ExternalCommands"}:
                owner=nodes[owner]["parent"]
            commands=[]
            def collect(uid, inherited):
                n=nodes[uid]; conditions=inherited+[n["attributes"]["condition"]] if "condition" in n["attributes"] else inherited
                if n["tag"].endswith("Command") or n["tag"]=="Command":
                    commands.append({"tag":n["tag"],"attributes":n["attributes"],"conditions":conditions,"id":uid})
                for child in n["children"]: collect(child,conditions)
            collect(node["id"],[])
            triggers.append({"id":node["id"],"owner":owner,"action":node["attributes"].get("action","variable-change"),
                             "condition":node["attributes"].get("condition"),"commands":commands})
        controls=sorted({c["attributes"]["name"] for t in triggers for c in t["commands"]
                         if c["tag"]=="VariableCommand" and c["attributes"].get("name")})
        return {"source":str(source.resolve()),"file":file,"root":dict(root.attrib),"nodes":nodes,
                "variables":variables,"triggers":triggers,"controls":controls,"assetNames":names}
    finally:
        if archive: archive.close()


class Runtime:
    def __init__(self, model, width, height, state=None, time_ms=2400):
        self.model=model;self.state=dict(state or {});self.cache={};self.stack=set();self.issues={};self.events=[]
        self.command_log=None  # Opt-in ordered command trace; exploration pays no snapshot cost.
        self.time_ms=time_ms
        self.system={"screen_width":width,"screen_height":height,"time":time_ms,
                     "hour":9,"hour24":9,"minute":41,"second":0,"year":2026,"month":10,"date":13,
                     "day_of_week":3,"battery_level":80,"battery_state":0,
                     "touch_x":0,"touch_y":0,"touch_begin_x":0,"touch_begin_y":0,
                     "media_title":"音乐标题","media_subtitle":"歌手","ownerinfo":"主题场景预览",
                     "call_missed_count":0,"sms_unread_count":0}
        self.system.update(model.get("inputs",{}).get("system",{}))
        self.evaluator=Evaluator(self.lookup)

    def issue(self, key, reason): self.issues[str(key)]=str(reason)

    def value(self, expression, fallback=0, key="expression"):
        try: return self.evaluator.evaluate(expression)
        except ExpressionError as exc:
            self.issue(key, f"{expression}: {exc}");return fallback

    def lookup(self, name):
        if name in self.state:return self.state[name]
        if name in self.system:return self.system[name]
        if name in self.cache:return self.cache[name]
        if name in self.stack:raise ExpressionError("cyclic variable: "+name)
        node=self.model["variables"].get(name)
        if not node:
            if name not in self.model["controls"]:self.issue("input:"+name,"未提供系统/持久变量，预览假设为 0："+name)
            return 0
        self.stack.add(name)
        try:
            a=node["attributes"]
            if "expression" in a:
                result=self.value(a["expression"], "" if a.get("type")=="string" else 0, "Var:"+name)
            else:
                animations=[self.model["nodes"][c] for c in node["children"] if self.model["nodes"][c]["tag"]=="VariableAnimation"]
                playback=self.state.get("__animations",{}).get(name)
                elapsed=self.time_ms-playback["started"] if playback else None
                result=self.animation(animations[0], "value",0,elapsed) if animations else 0
            self.cache[name]=result;return result
        finally:self.stack.remove(name)

    def animation(self, node, attr, default, elapsed=None):
        frames=[self.model["nodes"][c]["attributes"] for c in node["children"] if self.model["nodes"][c]["tag"] in {"AniFrame","Position","Alpha","Size"}]
        points=sorted((float(self.value(f.get("time",0))),self.value(f.get(attr,default))) for f in frames)
        if not points:return default
        duration=points[-1][0];a=node["attributes"]
        loop=a.get("loop","false" if a.get("repeat")=="1" else "true").lower()!="false"
        time=0 if getattr(self,'reference_phase',False) else max(0,self.time_ms if elapsed is None else elapsed)
        t=time % duration if loop and duration>0 else min(time,duration)
        prev=points[0]
        for end in points[1:]:
            if t<=end[0]:
                if not isinstance(prev[1],(float,int)) or not isinstance(end[1],(float,int)):return prev[1]
                ratio=(t-prev[0])/max(1,end[0]-prev[0]);return prev[1]+(end[1]-prev[1])*max(0,ratio)
            prev=end
        return points[-1][1]

    def assign(self,name,value):
        self.state[name]=value;self.cache.clear()

    def source_frame(self,node):
        frames=[self.model['nodes'][c]['attributes'] for c in node['children'] if self.model['nodes'][c]['tag']=='Source']
        frames=sorted(frames,key=lambda f:float(self.value(f.get('time',0))))
        if not frames:return None
        a=node['attributes'];duration=float(self.value(frames[-1].get('time',0)))
        phase=0 if getattr(self,'reference_phase',False) else max(0,self.time_ms-float(self.value(a.get('delay',0))))
        loop=a.get('loop','false' if a.get('repeat')=='1' else 'true').lower()!='false'
        phase=phase%duration if loop and duration>0 else min(phase,duration)
        return next((f for f in frames if float(self.value(f.get('time',0)))>=phase),frames[-1])

    def execute(self, trigger, touch=None):
        if touch:self.system.update(touch)
        if trigger.get("condition") and not self.value(trigger["condition"],0,trigger["id"]):return
        delayed=[]
        for index,command in enumerate(trigger["commands"]):
            ancestor=command['id'];container=None
            while ancestor and ancestor in self.model['nodes'] and ancestor!=trigger['id']:
                node=self.model['nodes'][ancestor]
                if node['tag'] in {'CycleCommand','GroupCommands','LoopCommand'}:container=ancestor
                ancestor=node['parent']
            if container:
                self.issue(container,'原生命令容器尚未实现；保留原脚本，跳过整段以避免把循环误当作单次执行')
                continue
            a=command["attributes"]
            delay=self.value(a.get("delay",0),0,command["id"])
            delayed.append((float(delay),index,command))
        for delay,_,c in sorted(delayed,key=lambda x:(x[0],x[1])):
            a=c["attributes"]
            if not all(self.value(cond,0,c["id"]) for cond in c["conditions"]):continue
            before=copy.deepcopy(self.state) if self.command_log is not None else None
            previous=self.lookup(a['name']) if self.command_log is not None and c['tag']=='VariableCommand' and 'name' in a else None
            if c["tag"]=="VariableCommand":
                if "expression" not in a or "name" not in a:
                    self.issue(c["id"],"VariableCommand missing name/expression");continue
                try:self.assign(a["name"],self.evaluator.evaluate(a["expression"]))
                except ExpressionError as exc:self.issue(c["id"],str(exc))
            elif c["tag"]=="Command" and ".animation" in a.get("target",""):
                name=a["target"].split(".animation")[0];node=self.model["variables"].get(name)
                if node and a.get("value") in {"play","stop"}:
                    playing=copy.deepcopy(self.state.get("__animations",{}))
                    if a["value"]=="stop":
                        value=self.lookup(name);playing.pop(name,None);self.state[name]=value
                    else:
                        self.state.pop(name,None)
                        playing[name]={"started":max(0,self.time_ms-2400)}
                    self.state["__animations"]=playing;self.cache.clear()
                    self.issue(c["id"],"变量动画 play/stop 以交互后 2400ms 快照近似，repeat=1 按单次采样；缓动未模拟")
                else:self.issue(c["id"],"未支持的动画控制："+str(a))
            else:
                self.events.append({"tag":c["tag"],"attributes":a})
                self.issue(c["id"],"外部动作仅记录，不在预览器中调用："+c["tag"])
            if self.command_log is not None:
                record={'trigger':trigger['id'],'command':c['id'],'tag':c['tag'],'timeMs':self.time_ms,
                        'delayMs':delay,'changes':state_changes(before,self.state)}
                if c['tag']=='VariableCommand' and 'name' in a:
                    record['valueChange']={'name':a['name'],'before':previous,'after':copy.deepcopy(self.lookup(a['name']))}
                self.command_log.append(record)
        if any(d>0 for d,_,_ in delayed):self.issue(trigger["id"]+":delay","延迟命令按到期顺序执行为稳定快照，未模拟中途取消")

    def settle(self, before):
        """Propagate Var change triggers with a bounded stabilization budget."""
        for _ in range(8):
            now={name:self.lookup(name) for name in self.model["variables"]}
            fired=[]
            for t in self.model["triggers"]:
                owner=self.model["nodes"].get(t["owner"],{})
                name=owner.get("attributes",{}).get("name")
                if owner.get("tag")=="Var" and t["action"]=="variable-change" and now.get(name)!=before.get(name):fired.append(t)
            if not fired:return
            before=now
            for t in fired:self.execute(t)
        self.issue("state-propagation","变量触发器未在 8 轮内稳定，可能为时钟/循环状态")

    def logical_snapshot(self):
        """Visibility predicates only: no geometry, assets or animation phase."""
        output=[];nodes=self.model['nodes']
        def walk(uid):
            node=nodes[uid]
            if node['parent'] is not None and node['tag'] not in VISUAL:return
            if not self.value(node['attributes'].get('visibility',1),0,uid+':visibility'):return
            if node['tag'] in VISUAL:output.append({'id':uid,'tag':node['tag'],'visible':True,'logicalVisible':True})
            if node['tag'] in {'Group','Lockscreen'}:
                for child in node['children']:walk(child)
        walk(next(iter(nodes)))
        return output

    def snapshot(self, assets, visible_only=False, only_ids=None):
        output=[]; nodes=self.model["nodes"]; global_rect={}
        needed=None
        if only_ids is not None:
            needed=set()
            for uid in only_ids:
                while uid and uid not in needed:
                    needed.add(uid);uid=nodes[uid]['parent']
        def walk(uid, px=0,py=0,visible=True,parent_alpha=1):
            if needed is not None and uid not in needed:return
            node=nodes[uid];a=node["attributes"];tag=node["tag"]
            if tag not in VISUAL and node["parent"] is not None:
                return
            shown=visible and bool(self.value(a.get("visibility",1),0,uid+":visibility"))
            if visible_only and not shown:return
            x=float(self.value(a.get("x",0),0,uid+":x"));y=float(self.value(a.get("y",0),0,uid+":y"))
            alpha=float(self.value(a.get("alpha",255),0,uid+":alpha"))/255*parent_alpha
            for cid in node["children"]:
                anim=nodes[cid]
                if anim["tag"]=="PositionAnimation":
                    x+=float(self.animation(anim,"x",0));y+=float(self.animation(anim,"y",0))
                elif anim["tag"]=="AlphaAnimation":alpha*=float(self.animation(anim,"a",255))/255
            src=a.get("src","")
            if "srcExp" in a:
                expression=a['srcExp']
                src=expression if re.fullmatch(r'[\w./\\ -]+\.(?:png|jpe?g|webp|gif|mp4)',expression,re.I) else str(self.value(expression,"",uid+":srcExp"))
            if a.get("srcid") and src:
                srcid=self.value(a["srcid"],0,uid+":srcid")
                try:index=int(float(srcid))
                except (ValueError,TypeError,OverflowError):
                    self.issue(uid+':srcid','srcid is not numeric: '+repr(srcid));index=0
                base,ext=posixpath.splitext(src);src=f"{base}_{index}{ext}"
            for cid in node['children']:
                if nodes[cid]['tag']=='SourcesAnimation':
                    frame=self.source_frame(nodes[cid])
                    if frame:
                        src=frame.get('src',src);x+=float(self.value(frame.get('x',0)));y+=float(self.value(frame.get('y',0)))
            asset=resolve_asset(self.model,src)
            meta=assets.get(asset,{})
            if tag=='Video' and not meta and a.get('defaultBitmap'):
                poster=resolve_asset(self.model,a['defaultBitmap'])
                if poster in assets:asset=poster;meta=assets[poster];self.issue(uid+':video-poster','视频不可用，显示脚本指定 defaultBitmap')
            w=float(self.value(a.get("w",a.get("width",meta.get("width",0))),0,uid+":w"))
            h=float(self.value(a.get("h",a.get("height",meta.get("height",0))),0,uid+":h"))
            if tag=='Video' and a.get('isBackground','false').lower()=='true':
                if not any(k in a for k in ('w','width')):w=self.system['screen_width']
                if not any(k in a for k in ('h','height')):h=self.system['screen_height']
            text=a.get("text", "09:41" if tag=="Time" else "")
            if tag=='DateTime':
                from multires_app.media.media_support import datetime_text
                text=datetime_text(self,a)
            if "textExp" in a:text=str(self.value(a["textExp"],text,uid+":textExp"))
            if tag in {"Text","Time","Date","DateTime","Number"}:
                size=float(self.value(a.get("size",42),42,uid+":size"));w=w or max(size,len(text)*size*.58);h=h or size*1.3
            else:size=0
            ax=.5 if a.get("align")=="center" else 1 if a.get("align")=="right" else 0
            ay=.5 if a.get("alignV")=="center" else 1 if a.get("alignV")=="bottom" else 0
            gx=px+x-w*ax;gy=py+y-h*ay
            item={"id":uid,"tag":tag,"component":node["top"],"parent":node["parent"],
                  "visible":bool(shown and alpha>0),"logicalVisible":bool(shown),"alpha":max(0,min(1,alpha)),
                  "rect":{"x":gx,"y":gy,"w":max(0,w),"h":max(0,h)},"asset":asset,
                  "text":text,"size":size,"color":a.get("color","#ffffff"),"name":a.get("name",tag),
                  "interactionIds":[t["id"] for t in self.model["triggers"] if t["owner"]==uid]}
            if tag=='Video':item['media']={'playing':bool(self.value(a.get('play',1),1,uid+':play')),'looping':a.get('looping','true')!='false'}
            bitmap_clock=tag=='Time' and src and any(resolve_asset(self.model,posixpath.splitext(src)[0]+'_'+str(i)+posixpath.splitext(src)[1]) in assets for i in (0,1))
            if tag=="ImageSeries" or bitmap_clock:
                if bitmap_clock:
                    from multires_app.media.media_support import clock_text
                    string=clock_text(self,a)
                else:string=str(self.value(a.get("string","''"),"",uid+":string"))
                mapping={pair[0]:pair[1:] for pair in a.get("mapList","").split(",") if len(pair)>=2}
                if bitmap_clock:mapping={':':'dot',**mapping}
                base,ext=posixpath.splitext(src);glyphs=[]
                for char in string:
                    asset_path=resolve_asset(self.model,base+"_"+mapping.get(char,char)+ext)
                    glyph_meta=assets.get(asset_path)
                    if glyph_meta:glyphs.append((asset_path,glyph_meta))
                    else:self.issue(uid+":glyph:"+char,"ImageSeries 字形素材缺失："+asset_path)
                space=float(self.value(a.get("space",0),0,uid+":space"))
                total=sum(meta["width"] for _,meta in glyphs)+max(0,len(glyphs)-1)*space
                cursor=px+x-total*ax
                for asset_path,meta in glyphs:
                    output.append({**item,"asset":asset_path,"rect":{"x":cursor,"y":py+y-meta["height"]*ay,"w":meta["width"],"h":meta["height"]}})
                    cursor+=meta["width"]+space
                self.issue(uid+":export-size",tag+" 已按字形素材预览；原生控件的字形缩放语义需实机确认")
                return
            if tag in VISUAL:
                output.append(item);global_rect[uid]=item["rect"]
                if tag in {'Time','Date','Number'} and src:self.issue(uid+':native-glyph-size','原生图片字形控件的尺寸与缩放尚未支持；当前几何为估计值，需原生引擎验收')
                if src and not meta:self.issue(uid+":asset","素材未找到或依赖系统路径："+src)
                if tag in {"Unlocker","MediaControl","MediaProgressbar","LrcView"} or (tag=='Video' and not meta):
                    self.issue(uid+":render","该控件暂显示几何/占位："+tag)
            if tag in {"Group","Lockscreen"}:
                for c in node["children"]:walk(c,px+x,py+y,shown,alpha)
        walk(next(iter(nodes)))
        return output


def resolve_asset(model, src):
    if not src:return ""
    src=src.replace("\\","/")
    candidates=[posixpath.normpath(posixpath.join(posixpath.dirname(model["file"]),src)),posixpath.normpath(src)]
    # Themes refer to the installed wallpaper using /data/themes/0/... .
    if "/wallpaper/" in src:candidates.append("wallpaper/"+src.rsplit("/wallpaper/",1)[1])
    for prefix in ('/data/themes/0/','/data/skin/'):
        if src.startswith(prefix):candidates.append(src[len(prefix):])
    return next((p for p in candidates if p in model["assetNames"]),candidates[0])


def extract_assets(source, model, output):
    """Extract image assets under a generated assets dir without ZIP traversal."""
    from PIL import Image
    archive=zipfile.ZipFile(source) if source.is_file() and zipfile.is_zipfile(source) else None
    assets={}
    try:
        for name in model["assetNames"]:
            suffix=Path(name).suffix.lower()
            if suffix not in {".png",".jpg",".jpeg",".webp",".gif",".bmp",".mp4",".m4v",".webm"}:continue
            if not name.startswith((posixpath.dirname(model["file"])+"/","wallpaper/")):continue
            if ".." in Path(name).parts or name.startswith(("/","\\")):continue
            import io
            data=archive.read(name) if archive else (source/name).read_bytes()
            kind='image'
            if suffix in {'.mp4','.m4v','.webm'}:
                from multires_app.media.media_support import mp4_size
                kind='video';w,h=mp4_size(data) or (0,0)
            else:
                try:
                    im=Image.open(io.BytesIO(data));w,h=im.size
                except Exception:continue
            dest=output/"assets"/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.exists() or dest.read_bytes()!=data:
                temporary=dest.with_name(dest.name+'.extracting');temporary.write_bytes(data);temporary.replace(dest)
            assets[name]={"width":w,"height":h,"url":"assets/"+quote(name,safe='/')+'?v='+hashlib.sha256(data).hexdigest()[:12],"kind":kind}
    finally:
        if archive:archive.close()
    return assets


def touch_samples(runtime, trigger, rect, max_points=20):
    """Split hit areas at linear touch-condition thresholds, then sample cells."""
    points=[("center",(.5,.5,.5,.5))]
    source_text=str(trigger["commands"])
    if "#touch_" not in source_text:return points
    if trigger["action"] in {"move","up"}:
        points += [("left",(.1,.5,.5,.5)),("right",(.9,.5,.5,.5)),("up",(.5,.1,.5,.5)),("down",(.5,.9,.5,.5))]
    cuts={"x":{0.,1.},"y":{0.,1.}}
    saved=dict(runtime.system)
    for command in trigger["commands"]:
        expressions=command["conditions"]+[command["attributes"].get("expression","")]
        for expression in expressions:
            if "#touch_" not in expression:continue
            try:
                translated=REF.sub(lambda m:"ref("+repr(m[1])+")",expression)
                tree=parse_expression(re.sub(r"\bnot\s*\(","logical_not(",translated))
            except (SyntaxError,ExpressionError):continue
            for call in ast.walk(tree):
                if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Name) or call.func.id not in {"gt","ge","lt","le","eq"} or len(call.args)!=2:continue
                refs={n.args[0].value for n in ast.walk(call.args[0]) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="ref" and n.args and isinstance(n.args[0],ast.Constant)}
                axes=[axis for axis in ("x","y") if "touch_"+axis in refs]
                if len(axes)!=1:continue
                axis=axes[0]
                try:
                    runtime.system.update(touch_x=0,touch_y=0);runtime.cache.clear()
                    zero=float(runtime.evaluator._node(call.args[0]));bound=float(runtime.evaluator._node(call.args[1]))
                    runtime.system["touch_"+axis]=1;runtime.cache.clear();one=float(runtime.evaluator._node(call.args[0]))
                    runtime.system["touch_"+axis]=2;runtime.cache.clear();two=float(runtime.evaluator._node(call.args[0]))
                    slope=one-zero
                    if abs(slope)<1e-9 or abs(two-one-slope)>1e-6:continue
                    position=(bound-zero)/slope
                    fraction=(position-rect[axis])/max(1,rect["w" if axis=="x" else "h"])
                    if 0<fraction<1:cuts[axis].add(round(fraction,5))
                except (ExpressionError,ValueError,TypeError):continue
    runtime.system=saved;runtime.cache.clear()
    midpoints={axis:[(a+b)/2 for a,b in zip(sorted(values),sorted(values)[1:])] for axis,values in cuts.items()}
    cells=[(x,y) for x in midpoints["x"] for y in midpoints["y"]]
    if len(cells)>max_points:
        cells=[cells[round(i*(len(cells)-1)/(max_points-1))] for i in range(max_points)]
    for x,y in cells:
        if abs(x-.5)+abs(y-.5)<.001:continue
        points.append((f"point {x:.2f},{y:.2f}",(x,y,x,y)))
    return points


def explore(model, source_profile, assets, max_states=96, max_depth=5):
    width=source_profile["resolutionPx"]["width"];height=source_profile["resolutionPx"]["height"]
    initial=Runtime(model,width,height,model.get("inputs",{}).get("variables",{}),time_ms=0)
    for t in model["triggers"]:
        if t["action"] in {"init","resume"}:initial.execute(t)
    initial.time_ms=2400;initial.system["time"]=2400;initial.cache.clear()
    diagnostics=dict(initial.issues);states=[];transitions=[];lookup={};queue=deque()
    # State identity excludes derived values and clocks; sampled commands retain
    # their persistent effects, while each transition retains its event trace.
    def register(state,depth,label,active_owner=None,trace=None):
        signature=json.dumps([state,active_owner],sort_keys=True,ensure_ascii=False)
        if signature in lookup:return lookup[signature],False
        if len(states)>=max_states:return None,False
        sid="state-"+str(len(states)).zfill(3);lookup[signature]=sid
        states.append({"id":sid,"label":label,"overrides":dict(state),"depth":depth,"activeOwner":active_owner,"trace":trace or []})
        queue.append(states[-1]);return sid,True
    register(initial.state,0,"初始 / resume 后 2400ms")
    truncated=False
    while queue:
        state=queue.popleft();rt=Runtime(model,width,height,state["overrides"])
        elements=rt.snapshot(assets)
        visible={e["id"]:e for e in elements if e["visible"]}
        state["visibleElements"]=[e["id"] for e in elements if e["visible"]]
        state["controlValues"]={name:rt.value("#"+name,0,"control:"+name) for name in model["controls"]}
        diagnostics.update(rt.issues)
        for trigger in model["triggers"]:
            owner=model["nodes"].get(trigger["owner"],{})
            if trigger["action"] in {"resume","init","pause","variable-change"}:continue
            if owner.get("tag") in VISUAL and trigger["owner"] not in visible and trigger["owner"]!=state.get("activeOwner"):continue
            rect=next((e["rect"] for e in elements if e["id"]==trigger["owner"]),{"x":width/2,"y":height/2,"w":0,"h":0})
            cx=rect["x"]+rect["w"]/2;cy=rect["y"]+rect["h"]/2
            gestures=touch_samples(rt,trigger,rect)
            for gesture,sample in gestures:
                result=Runtime(model,width,height,state["overrides"])
                before={n:result.lookup(n) for n in model["variables"]}
                fx,fy,bx,by=sample
                touch={"touch_begin_x":rect["x"]+bx*rect["w"],"touch_begin_y":rect["y"]+by*rect["h"],"touch_x":rect["x"]+fx*rect["w"],"touch_y":rect["y"]+fy*rect["h"]}
                result.execute(trigger,touch);result.settle(before);diagnostics.update(result.issues)
                active_owner=trigger["owner"] if trigger["action"]=="down" else None if trigger["action"] in {"up","tap"} else state.get("activeOwner")
                signature=json.dumps([result.state,active_owner],sort_keys=True,ensure_ascii=False)
                target=lookup.get(signature)
                if target is None and state["depth"]<max_depth:target,_=register(result.state,state["depth"]+1,owner.get("attributes",{}).get("name",owner.get("tag","事件"))+" / "+trigger["action"]+" / "+gesture,active_owner,state["trace"]+[{"trigger":trigger["id"],"gesture":gesture,"sample":sample}])
                if target is None:truncated=True
                transitions.append({"from":state["id"],"to":target,"trigger":trigger["id"],"owner":trigger["owner"],
                                    "action":trigger["action"],"gesture":gesture,"touch":touch,"sample":sample,"events":result.events,
                                    "changes":{k:v for k,v in result.state.items() if state["overrides"].get(k)!=v},
                                    "bounded":target is None})
    return {"states":states,"transitions":transitions,"diagnostics":diagnostics,
            "coverage":coverage_report(model,transitions,max_states,max_depth,truncated)}


def coverage_report(model, transitions, max_states, max_depth, truncated):
    sampled={t["trigger"] for t in transitions}
    interactive=[t for t in model["triggers"] if t["action"] not in {"resume","init","pause","variable-change"}]
    return {"maxStates":max_states,"maxDepth":max_depth,"truncated":truncated,
            "triggerCount":len(model["triggers"]),"interactiveTriggerCount":len(interactive),"sampledTriggers":len(sampled),
            "boundedTransitions":sum(t["to"] is None for t in transitions),
            "unsampledTriggers":[{"id":t["id"],"owner":t["owner"],"action":t["action"],"reason":"未在已探索的可见/捕获状态中遇到"} for t in interactive if t["id"] not in sampled],
            "note":"仅探索当前可见交互和采样手势；连续变量、外部应用与动画播放未穷举。"}


def replay_state(model, width, height, state, assets):
    """Replay the interaction path in the target's logical screen environment."""
    rt=Runtime(model,width,height,model.get("inputs",{}).get("variables",{}),time_ms=0)
    for trigger in model["triggers"]:
        if trigger["action"] in {"resume","init"}:rt.execute(trigger)
    rt.time_ms=2400;rt.system["time"]=2400;rt.cache.clear()
    triggers={t["id"]:t for t in model["triggers"]}
    for step in state.get("trace",[]):
        trigger=triggers[step["trigger"]]
        elements=rt.snapshot(assets)
        rect=next((e["rect"] for e in elements if e["id"]==trigger["owner"]),{"x":width/2,"y":height/2,"w":0,"h":0})
        cx=rect["x"]+rect["w"]/2;cy=rect["y"]+rect["h"]/2
        fx,fy,bx,by=step["sample"]
        before={name:rt.lookup(name) for name in model["variables"]}
        rt.execute(trigger,{"touch_begin_x":rect["x"]+bx*rect["w"],"touch_begin_y":rect["y"]+by*rect["h"],"touch_x":rect["x"]+fx*rect["w"],"touch_y":rect["y"]+fy*rect["h"]})
        rt.settle(before)
    return rt

"""Build lockscreen scripts + complete asset trees using the existing multi-res engine.

Run with the existing project's Python environment. Does not call or train a model.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import posixpath
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
import re
import shutil
import stat
import sys
import zipfile
from xml.etree import ElementTree as ET

from prepare import ENGINE

sys.dont_write_bytecode = True


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relative(name):
    # Reject paths whose interpretation differs between ZIP/POSIX and Windows.
    p = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts or any(s.rstrip(' .') != s for s in p.parts):
        raise ValueError('unsafe relative resource path: '+name)
    return p.as_posix()


def source_files(source,lockscreen_only=False):
    source=Path(source).resolve()
    if source.is_dir():
        entries=[]
        for path in sorted(source.rglob('*')):
            if path.is_symlink():
                raise ValueError('source symlinks are unsupported: '+str(path))
            if path.is_file():
                entries.append((relative(path.relative_to(source).as_posix()),path))
        return {name:path.read_bytes() for name,path in entries}
    if not source.is_file() or not zipfile.is_zipfile(source):
        raise ValueError('expected theme folder or ZIP/HWT; use --asset-root for a single XML')
    with zipfile.ZipFile(source) as z:
        records={}
        total=0
        manifests=manifest_names({n:None for n in z.namelist()}) if lockscreen_only else []
        prefixes=[str(PurePosixPath(n).parent)+'/' for n in manifests]
        seen=set()
        for info in z.infolist():
            if info.is_dir():
                continue
            name=relative(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('ZIP symlink is unsupported')
            if name.casefold() in seen:
                raise ValueError('duplicate/case-colliding ZIP entry: '+name)
            seen.add(name.casefold())
            if lockscreen_only and not (any(name.startswith(prefix) for prefix in prefixes) or name.startswith('wallpaper/')):
                continue
            total += info.file_size
            if info.file_size > 256*1024*1024 or total > 1024*1024*1024:
                raise ValueError('source exceeds helper memory budget; use reviewed extracted folder')
            if info.file_size / max(1,info.compress_size) > 1000:
                raise ValueError('suspicious ZIP compression ratio')
            records[name]=z.read(info)
        return records


def file_sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def signature(files):
    return {name:sha(data) for name,data in sorted(files.items())}


def manifest_names(files):
    return [n for n in sorted(files) if re.search(r'(?:^|/)(?:ucdlockscreen|lockscreen)/manifest\.xml$',n,re.I)]


def reference_audit(files):
    result={'resolved':[],'external':[],'missing':[],'dynamic':[]}
    for manifest in manifest_names(files):
        root=ET.fromstring(files[manifest])
        for index,node in enumerate(root.iter()):
            if any(k in node.attrib for k in ('srcExp','srcid')):
                result['dynamic'].append({'manifest':manifest,'nodeIndex':index,'tag':node.tag,
                    'expressions':{k:node.attrib[k] for k in ('srcExp','srcid') if k in node.attrib},
                    'status':'requires-state-dependent-resolution'})
            for attr in ('src','defaultBitmap'):
                value=node.attrib.get(attr)
                if not value or (attr == 'src' and node.attrib.get('srcExp')):
                    continue
                value=value.replace('\\','/')
                record={'manifest':manifest,'nodeIndex':index,'tag':node.tag,'attribute':attr,'value':value}
                candidates=[posixpath.normpath(posixpath.join(posixpath.dirname(manifest),value)),posixpath.normpath(value)]
                for prefix in ('/data/themes/0/','/data/skin/'):
                    if value.startswith(prefix):candidates.append(value[len(prefix):])
                if '/wallpaper/' in value:candidates.append('wallpaper/'+value.rsplit('/wallpaper/',1)[1])
                indexed=attr=='src' and bool(node.attrib.get('srcid'))
                found=None if indexed else next((n for n in candidates if n in files),None)
                if found:
                    result['resolved'].append({**record,'resource':found});continue
                families=[]
                if indexed or node.tag in ('ImageSeries','Time','Number','Date') and attr=='src':
                    for name in candidates:
                        base,ext=posixpath.splitext(name)
                        if indexed:
                            pattern=re.compile(re.escape(base+'_')+r'-?\d+'+re.escape(ext)+r'$')
                            families.extend(n for n in files if pattern.fullmatch(n))
                        else:
                            families.extend(n for n in files if n.startswith(base+'_') and posixpath.splitext(n)[1]==ext)
                if families:
                    result['resolved'].append({**record,'family':sorted(set(families))});continue
                external=value.startswith(('/','file:','http:','https:')) or any(n.startswith('../') for n in candidates)
                result['external' if external else 'missing'].append(record)
    return result


def facts(files):
    from PIL import Image
    scripts=[]
    for name in manifest_names(files):
        root=ET.fromstring(files[name])
        if root.tag!='Lockscreen':
            raise ValueError('not a supported Lockscreen XML: '+name)
        scripts.append({'file':name,'sha256':sha(files[name]),'rootAttributes':dict(root.attrib),
            'nodeCount':sum(1 for _ in root.iter()),
            'triggers':[dict(n.attrib) for n in root.iter('Trigger')],
            'resourceExpressions':[{'tag':n.tag,**{k:v for k,v in n.attrib.items() if k in ('src','srcExp','srcid')}}
                                   for n in root.iter() if any(k in n.attrib for k in ('src','srcExp','srcid'))]})
    assets=[]
    for name,data in sorted(files.items()):
        suffix=Path(name).suffix.lower()
        if suffix not in ('.png','.jpg','.jpeg','.webp','.gif','.bmp','.mp4','.m4v','.webm','.ttf','.otf'):
            continue
        entry={'file':name,'sha256':sha(data),'bytes':len(data),'kind':'image'}
        if suffix in ('.mp4','.m4v','.webm'):
            entry['kind']='video'; entry['dimensions']='not inspected by this helper'
        elif suffix in ('.ttf','.otf'):
            entry['kind']='font'
        else:
            try:
                with Image.open(io.BytesIO(data)) as im:
                    entry.update(width=im.width,height=im.height,frames=getattr(im,'n_frames',1),mode=im.mode)
            except Exception as exc:
                entry.update(decodeStatus='failed',reason=str(exc))
        assets.append(entry)
    return {'schema':'lockscreen-source-facts/1','scripts':scripts,'assets':assets,
            'referenceAudit':reference_audit(files),
            'fileCount':len(files),'coordinateWarning':'Image dimensions are not engine logical coordinates.',
            'semantics':'Static inventory only; use engine scene graph for reachable states and groups.'}


def fresh(out, inputs):
    out=Path(out).resolve()
    for source in inputs:
        source=Path(source).resolve()
        if source.is_dir() and (out.is_relative_to(source) or source.is_relative_to(out)) or out==source:
            raise ValueError('output must be separate from input tree: '+str(out))
    if out.exists():
        raise ValueError('output exists; choose a fresh run directory: '+str(out))
    return out


def materialize(files, root):
    root=Path(root).resolve()
    seen=set()
    for name,data in files.items():
        name=relative(name)
        if name.casefold() in seen:
            raise ValueError('case-colliding resource names')
        seen.add(name.casefold())
        dest=(root/name).resolve()
        if not dest.is_relative_to(root):
            raise ValueError('resource escaped output')
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(data)


def collect(project_paths, out, expected_signature=None):
    from multires_app.pipeline.scene_pipeline import export_script
    out=Path(out).resolve()
    if out.exists():
        raise ValueError('delivery output already exists')
    projects=[(Path(p).resolve(),read(p)) for p in project_paths]
    sources={str(Path(p['source']).resolve()) for _,p in projects}
    if len(sources)!=1:
        raise ValueError('all variants must have the same original source')
    source=Path(next(iter(sources)))
    if source.is_dir() and out.is_relative_to(source):
        raise ValueError('delivery output must not be inside source')
    original=source_files(source)
    if expected_signature is not None and signature(original)!=expected_signature:
        raise ValueError('source changed since build started')
    for project_path,_ in projects:
        binding=project_path.parent/'input-signature.json'
        if not binding.is_file() or read(binding)!=signature(original):
            raise ValueError('project has no matching source signature; rebuild using this helper before collecting')
        origin=project_path.parent/'original-archive.json'
        if origin.exists():
            record=read(origin)
            if file_sha(record['path'])!=record['sha256']:
                raise ValueError('original archive changed after intake; rebuild before collecting')
    manifests=manifest_names(original)
    adapted={p['model']['file'] for _,p in projects}
    if adapted!=set(manifests):
        raise ValueError('all lockscreen variants must be processed; no partial theme delivery')
    targets=[d['profile']['id'] for d in projects[0][1]['devices']]
    if not targets or len(targets)!=len(set(targets)):
        raise ValueError('empty/duplicate target IDs')
    if any({d['profile']['id'] for d in p['devices']}!=set(targets) for _,p in projects):
        raise ValueError('target sets differ between lockscreen variants')
    reports=[]
    for target in targets:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',target):
            raise ValueError('unsafe target ID')
        result=dict(original)
        generated={}
        exports=[]
        asset_operations=[]
        variant_checks=[]
        for project_path,project in projects:
            script=project_path.parent/'scripts'/(target+'.xml')
            exported=export_script(project,target,script)
            exports.append(exported)
            build_report=read(project_path.parent/'report.json')
            variant_checks.append({'manifest':project['model']['file'],
                'states':build_report.get('states'),'transitions':build_report.get('transitions'),
                'coverage':build_report.get('coverage'),'diagnosticCount':build_report.get('diagnosticCount'),
                'limitations':build_report.get('limitations'),
                'scope':'Engine exploration and lexical/geometry export checks; not native device validation.'})
            ET.fromstring(script.read_bytes())
            result[project['model']['file']]=script.read_bytes()
            meta=read(script.with_suffix('.resources.json'))
            for name,item in meta.get('files',{}).items():
                name=relative(name)
                path=Path(item['file']).resolve()
                if not path.is_relative_to(project_path.parent):
                    raise ValueError('generated resource outside scene project')
                data=path.read_bytes()
                if sha(data)!=item['sha256'] or name in original:
                    raise ValueError('generated asset digest/collision failure')
                if name in generated and generated[name]!=data:
                    raise ValueError('variant generated resource conflict')
                generated[name]=data
                asset_operations.append({'source':item['source'],'target':name,
                    'operation':'bitmap-glyph-family-resize','scale':item['scale'],
                    'width':item['width'],'height':item['height'],
                    'sourceSha256':sha(original[item['source']]),'targetSha256':sha(data)})
        result.update(generated)
        references=reference_audit(result)
        directory=out/target/'theme'
        materialize(result,directory)
        changed=[{'file':name,'beforeSha256':sha(original[name]),'afterSha256':sha(data)}
                 for name,data in result.items() if name in original and data!=original[name]]
        unexpected=[r['file'] for r in changed if r['file'] not in adapted]
        if unexpected:
            raise ValueError('unexpected non-layout modifications: '+str(unexpected))
        audit={'target':target,'status':'candidate-needs-review','themeDirectory':str(directory),
            'adaptedManifests':sorted(adapted),'sourceFilesPreserved':len(original)-len(changed),
            'generatedAssetCount':len(generated),'changed':changed,'exports':exports,
            'assetOperations':asset_operations,'variantChecks':variant_checks,
            'resourceReferences':references,
            'dependencyStatus':'needs-review' if any(references[k] for k in ('external','missing','dynamic')) else 'static-packaged-references-resolved',
            'allFiles':signature(result),'nativeValidated':False,
            'note':'Complete script + assets directory. Ordinary images may be reused unchanged and sized in XML; bitmap glyph families may be regenerated.'}
        write(out/target/'delivery-audit.json',audit)
        reports.append({k:audit[k] for k in ('target','status','themeDirectory','adaptedManifests','generatedAssetCount')})
    if signature(source_files(source))!=signature(original):
        raise ValueError('source changed during delivery')
    summary={'schema':'lockscreen-delivery/1','source':str(source),'sourceUnchanged':True,
             'targets':reports,'nativeValidated':False,'modelTrainingPerformed':False}
    write(out/'delivery-summary.json',summary)
    return summary


def stage_source(args):
    source=Path(args.source).resolve()
    inputs=[source]
    if args.asset_root:
        inputs.append(Path(args.asset_root))
    out=fresh(args.out,inputs)
    if source.suffix.lower()=='.xml':
        if not args.asset_root:
            raise ValueError('single XML requires --asset-root matching its relative resource references')
        asset_root=Path(args.asset_root).resolve()
        if not asset_root.is_dir():
            raise ValueError('--asset-root must be a folder')
        originals=source_files(asset_root)
        original_xml=source.read_bytes()
        source_snapshot={'xml':sha(original_xml),'assets':signature(originals)}
        staged={'unlock/lockscreen/'+name:data for name,data in originals.items()}
        staged['unlock/lockscreen/manifest.xml']=original_xml
        out.mkdir(parents=True)
        normalized=out/'input'
        materialize(staged,normalized)
    elif source.is_file() and zipfile.is_zipfile(source):
        # The requested product is the lockscreen module, not unrelated global-theme components.
        # Validate a precisely scoped archive with the existing limits before extracting it.
        from multires_app.engine.archive_safety import validate_archive
        archive_origin={'path':str(source),'sha256':file_sha(source)}
        originals=source_files(source,lockscreen_only=True)
        if not manifest_names(originals):
            raise ValueError('archive has no supported direct lockscreen module')
        source_snapshot=signature(originals)
        out.mkdir(parents=True)
        scoped=out/'lockscreen-input.zip'
        with zipfile.ZipFile(scoped,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in originals.items():
                z.writestr(name,data)
        validation=validate_archive(scoped)
        write(out/'intake-report.json',{'scope':'all direct lockscreen/ucdlockscreen modules and wallpaper tree',
            'originalArchive':archive_origin,'scopedArchiveValidation':validation,
            'wholeThemeConverted':False,'note':'Excluded global desktop/icon modules are outside this lockscreen-only task.'})
        normalized=out/'input'
        materialize(originals,normalized)
    else:
        originals=source_files(source)
        source_snapshot=signature(originals)
        normalized=source
        out.mkdir(parents=True)
    files=originals if normalized==source else source_files(normalized)
    before=signature(files)
    inventory=facts(files)
    if not inventory['scripts']:
        raise ValueError('no lockscreen/manifest.xml found')
    write(out/'source-facts.json',inventory)
    write(out/'source-signature.json',source_snapshot)
    return SimpleNamespace(source=source, out=out, root=normalized, files=files, inventory=inventory,
                           signature=before, original_snapshot=source_snapshot,
                           archive=archive_origin if source.is_file() and source.suffix.lower() != ".xml"
                           and zipfile.is_zipfile(source) else None)


def resolved_source_profile(stage, explicit, manifest):
    if explicit:
        return explicit
    if not stage.archive:
        raise ValueError("source-profile is required for XML and extracted folders")
    from multires_app.tools.batch_convert import source_profile as infer_source_profile

    with zipfile.ZipFile(stage.source) as archive:
        for item in archive.infolist():
            if "preview_unlock_0" in item.filename.lower() and (
                item.file_size > 16 * 1024 * 1024
                or item.file_size / max(1, item.compress_size) > 1000
            ):
                raise ValueError("Source preview exceeds the inspection limit; supply --source-profile")
    return infer_source_profile(stage.source, manifest)


def build(args):
    engine=Path(args.engine).resolve()
    sys.path.insert(0,str(engine/'src'))
    from multires_app.pipeline.scene_pipeline import create_project

    stage=stage_source(args)
    source, out, normalized, files = stage.source, stage.out, stage.root, stage.files
    before, source_snapshot = stage.signature, stage.original_snapshot
    archive_origin = stage.archive
    profile=read(args.source_profile) if args.source_profile else None
    profiles=read(args.profiles)
    runtime_inputs=read(args.inputs) if args.inputs else {}
    policy=getattr(args,'interaction_space',None) or runtime_inputs.get('interaction',{}).get('coordinateSpace','source')
    runtime_inputs.setdefault('interaction',{})['coordinateSpace']=policy
    inputs_path=out/'runtime-inputs.json'
    write(inputs_path,runtime_inputs)
    if args.anchor_config:
        config=read(args.anchor_config)
        for p in profiles['profiles']:
            p.setdefault('adaptation',{})['anchorLayout']=config
    projects=[]
    for i,manifest in enumerate(manifest_names(files)):
        directory=out/'scenes'/('variant-'+str(i))
        active_profile=resolved_source_profile(stage,profile,manifest)
        manifest_inputs=getattr(args,'inputs_by_manifest',{}).get(manifest,inputs_path)
        create_project(normalized,active_profile,profiles,directory,args.algorithm,args.max_states,args.max_depth,
                       Path(args.components) if args.components else None,
                       manifest_inputs,manifest)
        write(directory/'input-signature.json',before)
        if source.is_file() and zipfile.is_zipfile(source):
            write(directory/'original-archive.json',archive_origin)
        projects.append(directory/'project.json')
    write(out/'projects.json',{'projects':[str(p) for p in projects]})
    result=collect(projects,out/'deliverables',before)
    if source.suffix.lower()=='.xml':
        unchanged=sha(source.read_bytes())==source_snapshot['xml'] and signature(source_files(args.asset_root))==source_snapshot['assets']
    elif source.is_file() and zipfile.is_zipfile(source):
        unchanged=file_sha(source)==archive_origin['sha256']
    else:
        unchanged=signature(source_files(source))==source_snapshot
    if not unchanged:
        raise ValueError('original inputs changed during build')
    result['originalInputUnchanged']=True
    result['interactionCoordinateSpace']=policy
    if source.is_file() and zipfile.is_zipfile(source):
        result['originalArchive']=archive_origin
        result['intakeScope']='lockscreen-modules-and-wallpapers'
        result['wholeThemeConverted']=False
    write(out/'run-summary.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    inspect=sub.add_parser('inspect')
    inspect.add_argument('source'); inspect.add_argument('--out',required=True)
    b=sub.add_parser('build')
    b.add_argument('--engine',default=str(ENGINE)); b.add_argument('--source',required=True)
    b.add_argument('--asset-root'); b.add_argument('--source-profile')
    b.add_argument('--profiles',required=True); b.add_argument('--out',required=True)
    b.add_argument('--algorithm',default='anchor'); b.add_argument('--components'); b.add_argument('--anchor-config')
    b.add_argument('--inputs'); b.add_argument('--max-states',type=int,default=48); b.add_argument('--max-depth',type=int,default=4)
    b.add_argument('--interaction-space',choices=['source','target'],help='default: source, or the explicit setting in --inputs')
    c=sub.add_parser('collect')
    c.add_argument('--engine',default=str(ENGINE)); c.add_argument('--projects',required=True); c.add_argument('--out',required=True)
    args=p.parse_args()
    try:
        if args.command=='inspect':
            result=facts(source_files(args.source)); write(args.out,result)
        elif args.command=='build':
            result=build(args)
        else:
            sys.path.insert(0,str(Path(args.engine).resolve()/'src'))
            result=collect(read(args.projects)['projects'],Path(args.out))
        print(json.dumps(result if args.command!='inspect' else {'scripts':len(result['scripts']),'assets':len(result['assets'])},ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False)); return 1


if __name__=='__main__':
    raise SystemExit(main())

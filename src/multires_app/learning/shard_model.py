"""Lossless bounded-memory safetensors sharding without torch/mmap.

Retains dtype, shape and exact tensor bytes. Useful for Windows loaders that
fault while mapping one multi-gigabyte storage. Original weights stay intact.
"""
import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path


def shard(source,out,max_bytes=768*1024*1024):
    source=Path(source).resolve();out=Path(out).resolve()
    if out.exists():raise ValueError('use a new sharded model directory')
    with (source/'model.safetensors').open('rb') as stream:
        length=struct.unpack('<Q',stream.read(8))[0]
        if not 0<length<32*1024*1024:raise ValueError('invalid tensor header')
        header=json.loads(stream.read(length));data_offset=8+length
        groups=[];current=[];size=0
        for key,value in sorted(((k,v) for k,v in header.items() if k!='__metadata__'),key=lambda kv:kv[1]['data_offsets'][0]):
            start,end=value['data_offsets'];n=end-start
            if current and size+n>max_bytes:groups.append(current);current=[];size=0
            current.append((key,value));size+=n
        if current:groups.append(current)
        out.mkdir(parents=True);weights={};hashes={};total=0
        for i,group in enumerate(groups):
            name=f'model-{i+1:05d}-of-{len(groups):05d}.safetensors';new={'__metadata__':{'format':'pt'}};position=0
            for key,value in group:
                start,end=value['data_offsets'];n=end-start
                new[key]={**value,'data_offsets':[position,position+n]};position+=n;weights[key]=name
            encoded=json.dumps(new,separators=(',',':')).encode();encoded+=b' '*((-len(encoded))%8)
            with (out/name).open('wb') as target:
                target.write(struct.pack('<Q',len(encoded)));target.write(encoded)
                for key,value in group:
                    start,end=value['data_offsets'];stream.seek(data_offset+start);remaining=end-start
                    while remaining:
                        chunk=stream.read(min(4*1024*1024,remaining))
                        if not chunk:raise ValueError('truncated source tensor')
                        target.write(chunk);remaining-=len(chunk)
            with (out/name).open('rb') as verify:hashes[name]=hashlib.file_digest(verify,'sha256').hexdigest()
            total+=position;print(name,position,flush=True)
    for path in source.iterdir():
        if path.is_file() and path.suffix in {'.json','.txt','.jinja','.model'} and 'safetensors' not in path.name:shutil.copyfile(path,out/path.name)
    (out/'model.safetensors.index.json').write_text(json.dumps({'metadata':{'total_size':total},'weight_map':weights},indent=2),encoding='utf8')
    (out/'sharding-manifest.json').write_text(json.dumps({'source':str(source),'tensorCount':len(weights),'totalTensorBytes':total,
        'shards':hashes,'conversion':'raw tensor byte copy; no dtype or precision change'},indent=2),encoding='utf8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',required=True);a=p.parse_args();shard(a.source,a.out)

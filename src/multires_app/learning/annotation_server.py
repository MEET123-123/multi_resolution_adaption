"""Loopback batch annotation API backed by cached JSONL and an offset index."""
import json
import mimetypes
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,unquote
from .catalog import Catalog


def serve(directory,port=8767):
    catalog=Catalog(directory)
    class Handler(BaseHTTPRequestHandler):
        def respond(self,status,value,kind='application/json; charset=utf-8'):
            data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode() if kind.startswith('application/json') else value
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
        def do_GET(self):
            parsed=urlsplit(self.path);query=parse_qs(parsed.query)
            try:
                if parsed.path in {'/','/api/samples'}:
                    page=catalog.page(int(query.get('offset',['0'])[0]),int(query.get('limit',['50'])[0]),
                        query.get('split',[''])[0],query.get('pending',['0'])[0]=='1')
                    if parsed.path!='/':return self.respond(200,page)
                    from .review_ui import review_document
                    body=review_document(page['samples'])
                    previous=max(0,page['offset']-page['limit']);next_page=page['offset']+page['limit']
                    navigation=f'<p>缓存样本 {page["total"]} 条；当前 {page["offset"]+1}–{min(next_page,page["total"])}。 <a href="/?offset={previous}&limit={page["limit"]}">上一页</a>　<a href="/?offset={next_page}&limit={page["limit"]}">下一页</a></p>'
                    return self.respond(200,body.replace('<main>','<main>'+navigation).encode(),'text/html; charset=utf-8')
                if parsed.path.startswith('/api/sample/'):
                    catalog.refresh();return self.respond(200,catalog.sample(unquote(parsed.path.rsplit('/',1)[1])))
                if parsed.path.startswith('/assets/'):
                    path=(catalog.root/unquote(parsed.path[len('/assets/'):])).resolve()
                    if not path.is_relative_to(catalog.root) or path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:raise ValueError('invalid image path')
                    return self.respond(200,path.read_bytes(),mimetypes.guess_type(path)[0] or 'application/octet-stream')
                self.respond(404,{'error':'not found'})
            except (ValueError,KeyError,OSError) as exc:self.respond(400,{'error':str(exc)})
        def do_POST(self):
            try:
                if self.path!='/api/reviews':return self.respond(404,{'error':'not found'})
                if self.headers.get('X-Multires-Review')!='1':raise ValueError('annotation request header required')
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=16_000_000:raise ValueError('invalid annotation batch size')
                batch=json.loads(self.rfile.read(size));self.respond(200,catalog.append(batch))
            except RuntimeError as exc:self.respond(409,{'error':str(exc)})
            except (ValueError,KeyError,OSError) as exc:self.respond(400,{'error':str(exc)})
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print('Annotation UI: http://127.0.0.1:'+str(server.server_port)+'/',flush=True)
    server.serve_forever()

"""仅监听回环地址的离线GUI；单任务执行、CSRF防护、受限下载。"""
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import platform
import secrets
import threading
import time
import traceback
from urllib.parse import urlsplit,parse_qs
from uuid import uuid4
from . import __version__
from .config import PROTOCOL
from .pipeline import run_action
from .storage import Cancelled,read_json,write_json

class JobManager:
    def __init__(self,root):
        self.root=Path(root).resolve(); self.outputs=self.root/'outputs'
        self.outputs.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock(); self.jobs={}; self.cancel_events={}
        states=sorted(self.outputs.glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:40]
        for path in states:
            try:
                job=read_json(path,limit=8*1024*1024)
                if job.get('id')!=path.parent.name:
                    continue
                if job['status'] in {'queued','running'}:
                    job['status']='failed'; job['error']='上次程序退出时任务未完成，请重新提交。'
                self.jobs[job['id']]=job
            except (ValueError,OSError,KeyError):
                continue

    def snapshot(self,id=None):
        with self.lock:
            value=self.jobs.get(id) if id else dict(jobs=sorted(self.jobs.values(),key=lambda j:j['created'],reverse=True)[:40])
            return json.loads(json.dumps(value,ensure_ascii=False,allow_nan=False))

    def save(self,job):
        write_json(self.outputs/job['id']/'job.json',job)

    def submit(self,action,params):
        if action not in {'demo','prepare','train','predict','evaluate','history','connectivity'} or not isinstance(params,dict):
            raise ValueError('任务类型或参数格式不合法。')
        with self.lock:
            if any(j['status'] in {'queued','running'} for j in self.jobs.values()):
                raise RuntimeError('已有任务运行，请等待完成或取消。')
            id=uuid4().hex[:16]
            job=dict(id=id,action=action,status='queued',progress=0,logs=[],created=time.time(),result=None,error=None)
            self.jobs[id]=job; cancel=threading.Event(); self.cancel_events[id]=cancel
            self.save(job)
            threading.Thread(target=self.execute,args=(job,params,cancel),daemon=True).start()
            return self.snapshot(id)

    def execute(self,job,params,cancel):
        out=self.outputs/job['id']
        def log(message,progress=None):
            with self.lock:
                job['logs'].append(time.strftime('%H:%M:%S')+' '+str(message))
                job['logs']=job['logs'][-200:]
                if progress is not None:
                    job['progress']=int(progress)
                self.save(job)
        with self.lock:
            job['status']='running'; self.save(job)
        try:
            result=run_action(job['action'],params,out,log,cancel)
            with self.lock:
                job.update(status='completed',progress=100,result=result)
        except Cancelled as exc:
            with self.lock:
                job.update(status='cancelled',error=str(exc))
        except Exception as exc:
            with self.lock:
                job.update(status='failed',error=f'{type(exc).__name__}: {exc}')
            (out/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
        finally:
            with self.lock:
                job['finished']=time.time(); self.save(job)
                self.cancel_events.pop(job['id'],None)

    def cancel(self,id):
        with self.lock:
            if id not in self.cancel_events:
                raise ValueError('该任务已结束或不存在。')
            self.cancel_events[id].set()
            return dict(message='已请求取消，待当前计算步骤结束后停止。')

    def defaults(self):
        values=dict(manifest=str(self.root/'examples'/'manifest.example.json'),
                    model=str(self.root/'examples'/'historical_model.json'))
        try:
            manifest=read_json(values['manifest'])
            first=manifest['records'][0]
            candidate=(Path(manifest.get('source_root',self.root/'examples'))/first['path']).resolve()
            if candidate.is_file() and candidate.suffix.lower()=='.edf':
                values['edf_path']=str(candidate)
        except (ValueError,OSError,KeyError,IndexError,TypeError):
            pass
        with self.lock:
            for job in sorted(self.jobs.values(),key=lambda j:j['created']):
                if job['status']=='completed':
                    paths=(job.get('result') or {}).get('output_paths',{})
                    values.update((k,v) for k,v in paths.items() if k not in {'dataset','model'})
                    if paths.get('dataset'):
                        values['dataset']=paths['dataset']
                        # 同一次任务的数据与模型成对复用，避免真实数据默认配上合成模型。
                        values['model']=paths.get('model',str(self.root/'examples'/'historical_model.json'))
        return values

class LocalServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,root,port):
        self.manager=JobManager(root); self.csrf=secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1',port),Handler)

class Handler(BaseHTTPRequestHandler):
    server_version='EEGSeizGuard/1.0'
    def log_message(self,*args):
        pass

    def valid_host(self):
        port=self.server.server_port
        return self.headers.get('Host') in {f'127.0.0.1:{port}',f'localhost:{port}'}

    def respond(self,status,payload,content_type='application/json; charset=utf-8',filename=None):
        if not isinstance(payload,bytes):
            payload=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (ConnectionError,OSError):
            pass

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403,dict(error='仅允许本机地址访问。'))
        try:
            url=urlsplit(self.path); query=parse_qs(url.query)
            manager=self.server.manager
            if url.path=='/api/info':
                return self.respond(200,dict(app_name='头皮脑电癫痫发作预测软件',version=__version__,
                    root=str(manager.root),project_path='G:\\EEGProject\\prediction_restart',
                    python_version=platform.python_version(),protocol=PROTOCOL.to_dict(),
                    csrf_token=self.server.csrf,default_paths=manager.defaults(),history=None))
            if url.path=='/api/jobs':
                result=manager.snapshot(query.get('id',[None])[0])
                return self.respond(200 if result is not None else 404,result or dict(error='任务不存在。'))
            if url.path=='/api/network-demo':
                return self.respond(200,(manager.root/'examples'/'network_demo.json').read_bytes())
            if url.path in {'/api/download','/api/artifact'}:
                id=query.get('job',[''])[0]; name=query.get('name',[''])[0]
                job=manager.snapshot(id)
                allowed={a['name'] for a in (job or {}).get('result',{}).get('artifacts',[])} if job and job.get('result') else set()
                if not job or job['status']!='completed' or name not in allowed:
                    return self.respond(404,dict(error='未完成任务的文件或文件名不存在。'))
                directory=(manager.outputs/id).resolve(); path=(directory/name).resolve()
                if not path.is_relative_to(directory) or not path.is_file() or path.name!=name:
                    return self.respond(403,dict(error='下载路径不合法。'))
                if url.path=='/api/artifact':
                    # 仅允许不会执行主动内容的可视化产物内联显示。
                    mime={'.png':'image/png','.json':'application/json; charset=utf-8'}.get(path.suffix.lower())
                    if mime is None:
                        return self.respond(403,dict(error='该产物仅支持下载。'))
                    return self.respond(200,path.read_bytes(),mime)
                return self.respond(200,path.read_bytes(),'application/octet-stream',name)
            static={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css'}
            for name in ('brain-network.js','brain-network.css','vendor/three.module.js',
                         'vendor/three.core.js','vendor/OrbitControls.js'):
                static['/'+name]=name
            if url.path not in static:
                return self.respond(404,dict(error='页面不存在。'))
            path=manager.root/'web'/static[url.path]
            mime={'.js':'text/javascript','.css':'text/css','.html':'text/html'}.get(path.suffix,'text/plain')
            return self.respond(200,path.read_bytes(),mime+'; charset=utf-8')
        except (ValueError,OSError,KeyError) as exc:
            return self.respond(400,dict(error=str(exc)))

    def do_POST(self):
        if not self.valid_host():
            return self.respond(403,dict(error='仅允许本机地址访问。'))
        origin=self.headers.get('Origin')
        if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}:
            return self.respond(403,dict(error='请求来源不合法。'))
        if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),self.server.csrf):
            return self.respond(403,dict(error='操作校验失败，请刷新界面重试。'))
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=16384 or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                return self.respond(400,dict(error='请求格式或大小不合法。'))
            self.connection.settimeout(10)
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):
                raise ValueError('请求须为JSON对象。')
            if self.path=='/api/jobs':
                return self.respond(202,self.server.manager.submit(data.get('action'),data.get('params',{})))
            if self.path=='/api/cancel':
                return self.respond(200,self.server.manager.cancel(data.get('id')))
            return self.respond(404,dict(error='接口不存在。'))
        except RuntimeError as exc:
            return self.respond(409,dict(error=str(exc)))
        except (ValueError,OSError,TypeError) as exc:
            return self.respond(400,dict(error=str(exc)))

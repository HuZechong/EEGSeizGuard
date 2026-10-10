"""直接运行启动GUI，或使用命令行执行可复现任务。"""
import argparse
from pathlib import Path
import sys
from uuid import uuid4
import webbrowser

def main():
    parser=argparse.ArgumentParser(description='EEGSeizGuard 本地研究软件')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--action',choices=['demo','prepare','train','predict','evaluate','history','connectivity'])
    parser.add_argument('--dataset'); parser.add_argument('--model'); parser.add_argument('--predictions')
    parser.add_argument('--manifest'); parser.add_argument('--project',default='G:/EEGProject/prediction_restart')
    parser.add_argument('--threshold',type=float,default=.5)
    parser.add_argument('--source',choices=['demo','edf'],default='demo')
    parser.add_argument('--edf-path')
    parser.add_argument('--start-seconds',type=float,default=30)
    parser.add_argument('--duration-seconds',type=float,default=120)
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    try:
        if args.action:
            from eegseizguard.pipeline import run_action
            import threading
            import json
            out=root/'outputs'/('cli-'+uuid4().hex[:12])
            def log(message,progress=None):
                print(message,flush=True)
            params={k:v for k,v in vars(args).items() if v is not None}
            if args.action=='connectivity':
                params={k:params[k] for k in ('source','edf_path','start_seconds','duration_seconds') if k in params}
            result=run_action(args.action,params,out,log,threading.Event())
            print(json.dumps(dict(output=str(out),summary=result['summary']),ensure_ascii=False),flush=True)
            return 0
        from eegseizguard.server import LocalServer
        if not 0<=args.port<=65535:
            raise ValueError('端口需在0到65535之间。')
        try:
            server=LocalServer(root,args.port)
        except OSError:
            if args.port!=8765:
                raise
            server=LocalServer(root,0)
        url=f'http://127.0.0.1:{server.server_port}'
        print(f'EEGSeizGuard V1.0\n{url}\nClose this window or press Ctrl+C to stop.',flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=.3)
        except KeyboardInterrupt:
            pass
        finally:
            for event in list(server.manager.cancel_events.values()):
                event.set()
            server.server_close()
        return 0
    except ModuleNotFoundError as exc:
        print(f'缺少运行依赖：{exc.name}。请执行 python -m pip install -r requirements.txt。',file=sys.stderr)
    except Exception as exc:
        print(f'启动/任务失败：{type(exc).__name__}: {exc}',file=sys.stderr)
    return 1

if __name__=='__main__':
    sys.exit(main())

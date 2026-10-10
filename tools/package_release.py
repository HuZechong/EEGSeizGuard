"""打包用户授权的 V1.0 交付，不包含原始EEG、旧任务与缓存。"""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def package_files():
    selected = []
    for path in ROOT.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in {'outputs','__pycache__','.git','.pytest_cache','.codex','.agents','.aws','.venv','node_modules'} or part.startswith('tmp')
               for part in relative.parts):
            continue
        if path.suffix.lower() in {'.py','.js','.css','.html','.txt','.json','.bat','.jpg','.png','.md'}:
            selected.append(path)
    return sorted(selected)

def main():
    import sys
    sys.path.insert(0,str(ROOT))
    from eegseizguard import __version__
    if __version__ != '1.0.0':
        raise ValueError('用户指定的软件版本必须保持V1.0。')
    files = [p for p in package_files() if p.name != 'release_manifest.json']
    manifest = dict(software='EEGSeizGuard',version='V1.0',
                    exclusions=['outputs','raw EEG','cache','private environment files'],
                    files={p.relative_to(ROOT).as_posix():dict(bytes=p.stat().st_size,
                         sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files})
    manifest_path = ROOT/'release_manifest.json'
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    files.append(manifest_path)
    output = ROOT.parent/'EEGSeizGuard_V1.0_可视化增强.zip'
    pending = output.with_suffix('.partial.zip')
    with zipfile.ZipFile(pending,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in files:
            archive.write(path,'EEGSeizGuard/'+path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(pending) as archive:
        bad=archive.testzip()
        if bad:
            raise ValueError('ZIP校验失败：'+bad)
    pending.replace(output)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.sha256.txt').write_text(digest+'  '+output.name+'\n',encoding='utf-8')
    print(json.dumps(dict(path=str(output),files=len(files),bytes=output.stat().st_size,
                         sha256=digest),ensure_ascii=False))

if __name__=='__main__':
    main()

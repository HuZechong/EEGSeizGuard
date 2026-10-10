"""按真实文件顺序生成自有源程序文本与行数/指纹清单。"""
from pathlib import Path
from eegseizguard.storage import atomic_bytes,file_hash,write_json

def main():
    root=Path(__file__).resolve().parent
    files=[root/'main.py']+sorted((root/'eegseizguard').glob('*.py'))
    files+=sorted(p for p in (root/'web').iterdir() if p.suffix in {'.html','.js','.css'})
    entries,texts=[],[]
    for path in files:
        text=path.read_text(encoding='utf-8-sig')
        lines=text.splitlines()
        relative=path.relative_to(root).as_posix()
        entries.append(dict(file=relative,lines=len(lines),nonblank=sum(bool(l.strip()) for l in lines),sha256=file_hash(path)))
        texts.append(f'===== {relative} =====\n'+text.rstrip()+'\n')
    out=root/'source_export'
    atomic_bytes(out/'源程序合并.txt','\n'.join(texts).encode('utf-8-sig'))
    manifest=dict(app='EEGSeizGuard',version='V1.0',source_lines=sum(e['lines'] for e in entries),
                  files=entries,exclusions=['第三方依赖','原始EEG','模型参数','运行结果','测试代码'],
                  note='真实源程序行数；未进行申请材料排版或页数扩写')
    write_json(out/'源程序清单.json',manifest)
    print(f'Exported {len(entries)} files / {manifest["source_lines"]} source lines to {out}')

if __name__=='__main__':
    main()

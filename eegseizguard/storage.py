"""严格读取、原子写入与取消检查，避免留下可误用的半成品。"""
import json
import os
from pathlib import Path
import tempfile
import hashlib

class Cancelled(RuntimeError):
    pass

def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled('任务已取消；本任务的中间文件不会作为完成结果使用。')

def input_file(value, suffix=None):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError('请填写有效的本地文件完整路径。')
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f'文件不存在：{path}')
    if suffix and path.suffix.lower() != suffix:
        raise ValueError(f'需要 {suffix} 文件：{path.name}')
    return path

def read_json(path, limit=4*1024*1024):
    path = input_file(path, '.json')
    if path.stat().st_size > limit:
        raise ValueError('JSON 文件超过大小限制。')
    def reject_constant(value):
        raise ValueError(f'JSON 含非法非有限数：{value}')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'JSON 重复键：{key}')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8-sig'),
                      parse_constant=reject_constant, object_pairs_hook=unique)

def atomic_bytes(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.partial-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def write_json(path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2,
                                 allow_nan=False).encode('utf-8'))

def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()

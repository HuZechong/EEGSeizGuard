"""下载固定官方发行包并校验完整性，仅用于构建，不在软件运行时联网。"""
import base64
import hashlib
import io
from pathlib import Path
import tarfile
import urllib.request

VERSION = '0.186.1'
URL = f'https://registry.npmjs.org/three/-/three-{VERSION}.tgz'
INTEGRITY = 'blFeqb49wRCSGUGj7gtpfnSGHy2lwDk94RhUmS1c/hTby70kvChbWpkJ4Pm1390LqzzvTmzgXKHPEafJwCb8jA=='

def main():
    raw = urllib.request.urlopen(URL, timeout=40).read(30*1024*1024)
    if base64.b64encode(hashlib.sha512(raw).digest()).decode() != INTEGRITY:
        raise ValueError('Three.js 发行包完整性不符。')
    out = Path(__file__).resolve().parents[1] / 'web' / 'vendor'
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        for name, target in [('build/three.module.js', 'three.module.js'),
                             ('build/three.core.js', 'three.core.js'),
                             ('examples/jsm/controls/OrbitControls.js', 'OrbitControls.js'),
                             ('LICENSE', 'LICENSE-three.txt')]:
            content = archive.extractfile('package/' + name).read()
            if target == 'OrbitControls.js':
                content = content.replace(b"from 'three'", b"from './three.module.js'")
            (out / target).write_bytes(content)
    (out / 'README.txt').write_text(
        f'Three.js {VERSION}, MIT license. Official package: {URL}\n'
        'Only local import path in OrbitControls.js was modified.\n'
        'Not part of 头皮脑电癫痫发作预测软件 original source-program export.\n', encoding='utf-8')
    print(f'Vendored Three.js {VERSION}: {out}')

if __name__ == '__main__':
    main()

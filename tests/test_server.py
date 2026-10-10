"""使用真实回环 HTTP 验证本地服务的访问边界和任务生命周期。

任务计算通过可取消的固定替身代替，网络请求与服务处理保持真实。
"""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from eegseizguard.server import LocalServer
from eegseizguard.storage import Cancelled


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.directory.name)
        web = self.root / 'web'
        web.mkdir()
        (web / 'index.html').write_text('<!doctype html><title>合成测试</title>', encoding='utf-8')
        self.server = LocalServer(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()

    def tearDown(self):
        # 确保临时任务全部退出，再移除测试目录。
        for event in list(self.server.manager.cancel_events.values()):
            event.set()
        deadline = time.monotonic() + 3
        while self.server.manager.cancel_events and time.monotonic() < deadline:
            time.sleep(.01)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post(self, data, headers=None, body=None):
        values = {'Content-Type': 'application/json', 'X-CSRF-Token': self.server.csrf}
        if headers:
            values.update(headers)
        return self.request('POST', '/api/jobs',
                            json.dumps(data).encode('utf-8') if body is None else body,
                            values)

    def test_static_page_and_info_are_available(self):
        status, headers, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'<!doctype html>', body)
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        status, _, body = self.request('GET', '/api/info')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['csrf_token'], self.server.csrf)

    def test_unknown_host_and_unauthorized_post_are_forbidden(self):
        status, _, _ = self.request('GET', '/api/info', headers={'Host': 'attacker.invalid'})
        self.assertEqual(status, 403)
        status, _, _ = self.request('POST', '/api/jobs', body=b'{}',
                                   headers={'Content-Type': 'application/json'})
        self.assertEqual(status, 403)
        status, _, _ = self.post({'action': 'demo'}, {'Origin': 'https://attacker.invalid'})
        self.assertEqual(status, 403)

    def test_invalid_json_and_unsupported_action_are_bad_requests(self):
        status, _, _ = self.post({}, body=b'{broken')
        self.assertEqual(status, 400)
        status, _, _ = self.post({'action': 'execute_anything', 'params': {}})
        self.assertEqual(status, 400)

    def test_submit_conflict_cancel_and_incomplete_download(self):
        started = threading.Event()

        def controlled_task(action, params, out, log, cancel):
            self.assertEqual(action, 'demo')
            started.set()
            if not cancel.wait(timeout=3):
                raise RuntimeError('测试未在预期时间内请求取消')
            raise Cancelled('合成测试任务已取消')

        with patch('eegseizguard.server.run_action', side_effect=controlled_task):
            status, _, body = self.post({'action': 'demo', 'params': {}})
            self.assertEqual(status, 202)
            job = json.loads(body)
            self.assertTrue(started.wait(timeout=1))
            status, _, _ = self.post({'action': 'demo', 'params': {}})
            self.assertEqual(status, 409)
            download = '/api/download?' + urlencode({'job': job['id'], 'name': 'model.json'})
            self.assertEqual(self.request('GET', download)[0], 404)
            status, _, _ = self.request(
                'POST', '/api/cancel', body=json.dumps({'id': job['id']}).encode(),
                headers={'Content-Type': 'application/json', 'X-CSRF-Token': self.server.csrf})
            self.assertEqual(status, 200)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                status, _, body = self.request('GET', '/api/jobs?' + urlencode({'id': job['id']}))
                current = json.loads(body)
                if current['status'] == 'cancelled':
                    break
                time.sleep(.01)
            self.assertEqual(status, 200)
            self.assertEqual(current['status'], 'cancelled')
            self.assertEqual(self.request('GET', download)[0], 404)

    def test_download_never_escapes_completed_job_directory(self):
        job_id = 'abcdef1234567890'
        folder = self.server.manager.outputs / job_id
        folder.mkdir()
        (folder / 'public.txt').write_bytes(b'public fixture')
        (self.server.manager.outputs / 'secret.txt').write_bytes(b'secret fixture')
        # 即使结果清单本身被写入恶意文件名，下载仍须检查目录边界。
        self.server.manager.jobs[job_id] = dict(
            id=job_id, status='completed', created=0,
            result={'artifacts': [{'name': 'public.txt'}, {'name': '../secret.txt'},
                                  {'name': '..\\secret.txt'}]})
        url = '/api/download?' + urlencode({'job': job_id, 'name': 'public.txt'})
        status, _, body = self.request('GET', url)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'public fixture')
        for name in ('../secret.txt', '..\\secret.txt', '../../secret.txt'):
            url = '/api/download?' + urlencode({'job': job_id, 'name': name})
            status, _, body = self.request('GET', url)
            with self.subTest(name=name):
                self.assertIn(status, (403, 404))
                self.assertNotIn(b'secret fixture', body)

    def test_inline_artifact_has_safe_mime_and_blocks_active_content(self):
        job_id = '1234567890abcdef'
        folder = self.server.manager.outputs / job_id
        folder.mkdir()
        for name in ('network.json','roc_curve.png','unsafe.html'):
            (folder/name).write_bytes(b'fixture')
        self.server.manager.jobs[job_id] = dict(id=job_id,status='completed',created=0,
            result={'artifacts':[{'name':name} for name in ('network.json','roc_curve.png','unsafe.html')]})
        for name, mime in [('network.json','application/json; charset=utf-8'),('roc_curve.png','image/png')]:
            status, headers, _ = self.request('GET','/api/artifact?'+urlencode({'job':job_id,'name':name}))
            self.assertEqual(status,200)
            self.assertEqual(headers['Content-Type'],mime)
            self.assertNotIn('Content-Disposition',headers)
        self.assertEqual(self.request('GET','/api/artifact?'+urlencode({'job':job_id,'name':'unsafe.html'}))[0],403)

    def test_defaults_do_not_pair_new_real_data_with_old_synthetic_model(self):
        manager=self.server.manager
        manager.jobs={'demo':dict(id='demo',status='completed',created=1,
            result={'output_paths':{'dataset':'demo.npz','model':'demo.json'}}),
            'real':dict(id='real',status='completed',created=2,
            result={'output_paths':{'dataset':'real.npz'}})}
        defaults=manager.defaults()
        self.assertEqual(defaults['dataset'],'real.npz')
        self.assertEqual(defaults['model'],str(self.root/'examples'/'historical_model.json'))
        manager.jobs['trained']=dict(id='trained',status='completed',created=3,
            result={'output_paths':{'dataset':'train.npz','model':'train.json'}})
        defaults=manager.defaults()
        self.assertEqual((defaults['dataset'],defaults['model']),('train.npz','train.json'))


if __name__ == '__main__':
    unittest.main()

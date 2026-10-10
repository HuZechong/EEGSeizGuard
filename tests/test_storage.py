"""外部 JSON 与任务取消不得绕过数据验证。"""
from pathlib import Path
import tempfile
import threading
import unittest

from eegseizguard.storage import Cancelled, check_cancel, read_json, write_json


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.directory.name) / 'input.json'

    def tearDown(self):
        self.directory.cleanup()

    def test_duplicate_keys_and_nonfinite_values_are_rejected(self):
        for text in ['{"coef":1,"coef":2}', '{"coef":NaN}',
                     '{"coef":Infinity}', '{"coef":-Infinity}']:
            self.path.write_text(text, encoding='utf-8')
            with self.subTest(text=text), self.assertRaises(ValueError):
                read_json(self.path)

    def test_failed_json_write_preserves_previous_file(self):
        write_json(self.path, {'completed': True})
        with self.assertRaises(ValueError):
            write_json(self.path, {'coef': float('nan')})
        self.assertEqual(read_json(self.path), {'completed': True})

    def test_cancelled_task_raises_before_work(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            check_cancel(event)


if __name__ == '__main__':
    unittest.main()

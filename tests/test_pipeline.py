"""真实入口的合成 EDF 与冻结模型集成测试。"""
from pathlib import Path
import tempfile
import threading
import unittest

from eegseizguard.data import load_dataset
from eegseizguard.pipeline import run_action
from eegseizguard.storage import Cancelled, read_json, write_json
from test_signal import write_edf


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_edf_preparation_keeps_tail_windows_unknown_and_audits_sources(self):
        write_edf(self.path / 'record.edf', records=70, fs=256)
        manifest = self.path / 'manifest.json'
        write_json(manifest, dict(schema_version=1, source_root=str(self.path), records=[
            dict(path='record.edf', patient='p1', split='inference',
                 annotations_complete=True, seizures=[])]))
        result = run_action('prepare', {'manifest': str(manifest)}, self.path / 'run',
                            lambda *args: None, threading.Event())
        data = load_dataset(result['output_paths']['dataset'])
        self.assertEqual(data.X.shape, (4, 90))
        self.assertEqual(data.y.tolist(), [-1, -1, -1, -1])
        self.assertEqual(data.time_seconds.tolist(), [40., 50., 60., 70.])
        audit = read_json(self.path / 'run' / 'data_audit.json')
        self.assertEqual(audit['excluded_reasons'], {'right_censored': 4})
        provenance = read_json(self.path / 'run' / 'provenance.json')
        self.assertIn('dataset.npz', provenance['files'])

    def test_annotation_complete_flag_must_be_boolean_true(self):
        write_edf(self.path / 'record.edf', records=50)
        manifest = self.path / 'manifest.json'
        write_json(manifest, dict(schema_version=1, records=[
            dict(path='record.edf', patient='p1', split='inference',
                 annotations_complete='true', seizures=[])]))
        with self.assertRaises(ValueError):
            run_action('prepare', {'manifest': str(manifest)}, self.path / 'run',
                       lambda *args: None, threading.Event())
        self.assertFalse((self.path / 'run' / 'dataset.npz').exists())

    def test_demo_runs_complete_pipeline_and_is_labelled_synthetic(self):
        result = run_action('demo', {}, self.path / 'run',
                            lambda *args: None, threading.Event())
        self.assertEqual(result['metrics']['source_kind'], 'synthetic_demo')
        self.assertIn('合成', result['summary'])
        self.assertTrue((self.path / 'run' / 'model.json').is_file())
        self.assertTrue((self.path / 'run' / 'predictions.csv').is_file())
        self.assertTrue((self.path / 'run' / 'provenance.json').is_file())

    def test_missing_seizure_inventory_does_not_mean_no_seizures(self):
        write_edf(self.path / 'record.edf', records=50)
        manifest = self.path / 'manifest.json'
        write_json(manifest, dict(schema_version=1, records=[
            dict(path='record.edf', patient='p1', split='inference',
                 annotations_complete=True)]))
        with self.assertRaises(ValueError):
            run_action('prepare', {'manifest': str(manifest)}, self.path / 'run',
                       lambda *args: None, threading.Event())
        self.assertFalse((self.path / 'run' / 'dataset.npz').exists())

    def test_cancelled_demo_has_no_completed_artifacts(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(Cancelled):
            run_action('demo', {}, self.path / 'run', lambda *args: None, cancelled)
        self.assertFalse((self.path / 'run' / 'model.json').exists())
        self.assertFalse((self.path / 'run' / 'provenance.json').exists())


if __name__ == '__main__':
    unittest.main()

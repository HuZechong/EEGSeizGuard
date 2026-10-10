"""网络估计、输入边界和文件溯源的独立验证；均使用合成信号。"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from scipy import signal

from eegseizguard.config import BANDS, CHANNELS
from eegseizguard.connectivity import (
    BAND_IDS, analyze_window, network_nodes, prepare_demo, run_connectivity,
)
from eegseizguard.signal import CausalFilter
from eegseizguard.storage import Cancelled, file_hash
from test_signal import write_edf


def noop(message, progress):
    pass


class CoherenceTests(unittest.TestCase):
    def test_coherence_matches_scipy_for_every_frequency_band(self):
        x = np.random.default_rng(20).normal(size=(18, 1280))
        x[1] = .65 * x[0] + .5 * x[1]
        frame = analyze_window(x, 30)
        frequencies, reference = signal.coherence(x[0], x[1], fs=128, window='hann',
                                                  nperseg=256, noverlap=128, detrend='constant')
        for index, (name, (lo, hi)) in enumerate(zip(BAND_IDS, BANDS)):
            mask = (frequencies >= lo) & ((frequencies <= hi) if index == 4 else (frequencies < hi))
            actual = np.asarray(frame['matrices'][name])
            self.assertAlmostEqual(actual[0, 1], float(reference[mask].mean()), places=6)
            np.testing.assert_array_equal(actual, actual.T)
            np.testing.assert_allclose(np.diag(actual), 1, atol=1e-7)
            self.assertTrue(np.all((actual >= 0) & (actual <= 1)))

    def test_psd_and_bandpower_use_same_welch_as_reference(self):
        x = np.random.default_rng(9).normal(size=(18, 1280)) * 20
        frame = analyze_window(x, 50)
        frequencies, psd = signal.welch(x, fs=128, window='hann', nperseg=256,
                                         noverlap=128, detrend='constant', axis=-1)
        np.testing.assert_allclose(frame['psd']['values'], psd[:, frequencies <= 45], atol=1e-7)
        for name, (lo, hi) in zip(BAND_IDS, BANDS):
            grid = np.r_[lo, frequencies[(frequencies > lo) & (frequencies < hi)], hi]
            expected = np.array([np.trapezoid(np.interp(grid, frequencies, row), grid) for row in psd])
            np.testing.assert_allclose(frame['bandpower'][name], expected, atol=1e-7)

    def test_flat_channels_are_unknown_not_perfect_or_zero_connections(self):
        x = np.random.default_rng(15).normal(size=(18, 1280))
        x[2] = 0
        x[6] = 400
        frame = analyze_window(x, 30)
        for matrix in frame['matrices'].values():
            self.assertTrue(all(value is None for value in matrix[2]))
            self.assertTrue(all(row[6] is None for row in matrix))
        self.assertFalse(frame['quality']['valid_channels'][2])
        self.assertEqual(set(frame['quality']['flat_channels']), {CHANNELS[2], CHANNELS[6]})
        self.assertTrue(frame['quality']['warnings'])
        json.dumps(frame, allow_nan=False)

    def test_invalid_samples_are_rejected(self):
        for x in [np.zeros((17, 1280)), np.zeros((18, 1279)), np.full((18, 1280), np.inf),
                  np.full((18, 1280), np.nan), np.full((18, 1280), 1e300)]:
            with self.subTest(shape=x.shape), self.assertRaises(ValueError):
                analyze_window(x, 30)

    def test_node_ids_follow_montage_and_are_explicitly_schematic(self):
        nodes = network_nodes()
        self.assertEqual([n['id'] for n in nodes], list(CHANNELS))
        self.assertEqual([n['electrodes'] for n in nodes], [name.split('-') for name in CHANNELS])
        np.testing.assert_allclose(np.linalg.norm([n['position'] for n in nodes], axis=1), 1, atol=1e-6)
        self.assertTrue(all(n['position'][1] > 0 for n in nodes))

    def test_demo_reproducible_and_never_claimed_as_patient_data(self):
        first = prepare_demo(duration_seconds=10)
        second = prepare_demo(duration_seconds=10)
        self.assertEqual(first, second)
        self.assertEqual(first['source']['kind'], 'synthetic_demo')
        self.assertIs(first['source']['contains_patient_data'], False)
        self.assertEqual(first['software_version'], 'V1.0')
        self.assertEqual(first['frames'][0]['start_seconds'], 30)
        self.assertEqual(first['frames'][0]['end_seconds'], 40)
        self.assertEqual(len(first['frames'][0]['waveform']['samples']), 18)
        self.assertEqual(len(first['frames'][0]['waveform']['t']), 1280)
        self.assertEqual(first['frames'][0]['waveform']['display_sampling_hz'], 128)
        self.assertIn('非个体解剖定位', first['method']['positioning'])


class ConnectivityTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.edf = self.root / 'signals.edf'

    def tearDown(self):
        self.temp.cleanup()

    def test_invalid_requests_fail_before_creating_output(self):
        requests = [dict(source='other'), dict(source=[]), dict(source={}), dict(source=None),
                    dict(start_seconds=0), dict(start_seconds=35),
                    dict(duration_seconds=0), dict(duration_seconds=610),
                    dict(duration_seconds=True), dict(duration_seconds='120'),
                    dict(start_seconds=float('inf')), dict(duration_seconds=float('nan')),
                    dict(source='demo', edf_path='record.edf'), dict(unknown=5)]
        for index, params in enumerate(requests):
            target = self.root / str(index)
            with self.subTest(params=params), self.assertRaises(ValueError):
                run_connectivity(params, target, noop, None)
            self.assertFalse(target.exists())

    def test_pre_cancelled_task_does_not_create_artifacts(self):
        cancelled = threading.Event()
        cancelled.set()
        target = self.root / 'cancelled'
        with self.assertRaises(Cancelled):
            run_connectivity({}, target, noop, cancelled)
        self.assertFalse(target.exists())

    def test_mid_analysis_cancel_does_not_write_network(self):
        cancelled = threading.Event()
        def cancel_after_first_frame(message, progress):
            if progress > 20:
                cancelled.set()
        target = self.root / 'cancel_mid'
        with self.assertRaises(Cancelled):
            run_connectivity(dict(duration_seconds=30), target, cancel_after_first_frame, cancelled)
        self.assertFalse((target / 'network.json').exists())

    def test_edf_uses_full_history_and_original_quantization(self):
        fs, seconds = 256, 50
        digital = np.random.default_rng(61).integers(-1000, 1000, (18, fs * seconds), dtype=np.int16)
        write_edf(self.edf, samples=digital, fs=fs, records=seconds)
        out = self.root / 'result'
        result = run_connectivity(dict(source='edf', edf_path=str(self.edf),
                                       start_seconds=30, duration_seconds=20), out, noop, None)
        network = json.loads((out / 'network.json').read_text(encoding='utf-8'))
        volts = (digital.astype(float) * 1e-6).astype(np.float32).astype(np.float64)
        filtered = (CausalFilter(fs).transform(volts) * 1e6).astype(np.float32)
        expected = analyze_window(filtered[:, 30 * 128:40 * 128], 30)
        self.assertEqual(network['frames'][0], expected)
        self.assertEqual(network['source']['kind'], 'edf')
        self.assertEqual(len(network['source']['sha256']), 64)
        self.assertEqual(result['network_summary']['frame_count'], 2)
        self.assertNotIn('frames', result)
        self.assertNotIn('network', result)
        self.assertEqual(len(result['artifacts']), 3)
        self.assertTrue((out / 'network_edges.csv').read_text(encoding='utf-8-sig').startswith('source_kind'))

    def test_file_change_rejects_export(self):
        write_edf(self.edf, records=40)
        target = self.root / 'changed'
        with patch('eegseizguard.connectivity._sha256', side_effect=['before', 'after']):
            with self.assertRaisesRegex(ValueError, '发生变化'):
                run_connectivity(dict(source='edf', edf_path=str(self.edf), duration_seconds=10),
                                 target, noop, None)
        self.assertFalse((target / 'network.json').exists())

    def test_requested_interval_must_be_fully_observed(self):
        write_edf(self.edf, records=39)
        target = self.root / 'short'
        with self.assertRaisesRegex(ValueError, '超过EDF时长'):
            run_connectivity(dict(source='edf', edf_path=str(self.edf), duration_seconds=10),
                             target, noop, None)
        self.assertFalse((target / 'network.json').exists())

    def test_pipeline_exports_network_and_provenance_fingerprints(self):
        from eegseizguard.pipeline import run_action
        write_edf(self.edf, records=40)
        target = self.root / 'integrated'
        params = dict(source='edf', edf_path=str(self.edf), duration_seconds=10)
        result = run_action('connectivity', params, target, noop, None)
        provenance = json.loads((target / 'provenance.json').read_text(encoding='utf-8'))
        self.assertEqual(provenance['action'], 'connectivity')
        self.assertEqual(provenance['params'], params)
        self.assertEqual(provenance['input_files']['edf']['sha256'], file_hash(self.edf))
        self.assertEqual(set(provenance['files']), {'network.json', 'network_edges.csv', 'network_report.txt'})
        for name, digest in provenance['files'].items():
            self.assertEqual(digest, file_hash(target / name))
        self.assertIn('provenance.json', [a['name'] for a in result['artifacts']])


if __name__ == '__main__':
    unittest.main()

"""时间协议、因果性与 EDF 拒绝路径的独立回归测试。

EDF 测试文件全部为临时合成数据，不包含任何患者信息。
"""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from eegseizguard.config import CHANNELS, PROTOCOL
from eegseizguard.signal import (
    CausalFilter, EDFReader, extract_features, label_window, validate_events,
)


def write_edf(path, labels=None, samples=None, unit='uV', fs=128, reserved='', records=2):
    """按 EDF 规范生成合成信号，用于测试文件边界与校准。"""
    labels = list(CHANNELS if labels is None else labels)
    count = len(labels)
    if samples is None:
        samples = np.tile(np.arange(fs * records, dtype=np.int16), (count, 1))

    def field(value, width):
        value = str(value).encode('ascii')
        if len(value) > width:
            raise ValueError('测试 EDF 字段过长')
        return value.ljust(width, b' ')

    fixed = b''.join([
        field('0', 8), field('Synthetic', 80), field('Test', 80),
        field('01.01.26', 8), field('00.00.00', 8),
        field(256 * (count + 1), 8), field(reserved, 44),
        field(records, 8), field(1, 8), field(count, 4),
    ])
    heads = [
        (labels, 16), ([''] * count, 80), ([unit] * count, 8),
        ([-32768] * count, 8), ([32767] * count, 8),
        ([-32768] * count, 8), ([32767] * count, 8),
        ([''] * count, 80), ([fs] * count, 8), ([''] * count, 32),
    ]
    body = [fixed]
    body.extend(b''.join(field(value, width) for value in values) for values, width in heads)
    for record in range(records):
        for channel in range(count):
            values = np.asarray(samples[channel, record * fs:(record + 1) * fs], dtype='<i2')
            body.append(values.tobytes())
    Path(path).write_bytes(b''.join(body))


class LabelTests(unittest.TestCase):
    def test_half_open_prediction_interval(self):
        events = [(4000, 4020)]
        self.assertEqual(label_window(1900, 7200, events)[0], 0)
        self.assertEqual(label_window(1910, 7200, events)[0], 1)
        self.assertEqual(label_window(3700, 7200, events)[0], 1)
        self.assertEqual(label_window(3710, 7200, events)[0], -1)

    def test_right_censoring_applies_to_both_classes(self):
        self.assertEqual(label_window(3000, 4100, [(4000, 4020)])[0], -1)
        self.assertEqual(label_window(5100, 7200, [])[0], 0)
        self.assertEqual(label_window(5110, 7200, [])[0], -1)

    def test_insufficient_event_history_and_cluster_are_unknown(self):
        self.assertEqual(label_window(500, 7200, [(1000, 1010)])[0], -1)
        self.assertEqual(label_window(3600, 7200, [(3500, 3510), (4000, 4020)])[0], -1)
        self.assertEqual(label_window(4010, 7200, [(4000, 4020)])[0], -1)

    def test_bad_events_are_rejected(self):
        invalid = [ [[True, 10]], [[float('nan'), 10]], [[10, 10]],
                    [[10, 30], [20, 40]], [[10, 10001]] ]
        for events in invalid:
            with self.subTest(events=events), self.assertRaises(ValueError):
                validate_events(events, 10000)


class SignalTests(unittest.TestCase):
    def test_future_samples_do_not_change_past(self):
        rng = np.random.default_rng(12)
        before = rng.normal(size=(18, 4000))
        after = before.copy()
        after[:, 2000:] += 1000
        a = CausalFilter(256).transform(before)
        b = CausalFilter(256).transform(after)
        np.testing.assert_array_equal(a[:, :1000], b[:, :1000])

    def test_arbitrary_stream_blocks_preserve_state_and_decimation_phase(self):
        x = np.random.default_rng(21).normal(size=(18, 4001))
        whole = CausalFilter(256).transform(x)
        stream = CausalFilter(256)
        chunks = [stream.transform(x[:, :701]), stream.transform(x[:, 701:2030]),
                  stream.transform(x[:, 2030:])]
        np.testing.assert_allclose(np.concatenate(chunks, axis=1), whole, atol=1e-12)

    def test_features_retain_channel_order_amplitude_and_frequency(self):
        time = np.arange(1280) / 128
        x = np.tile(20 * np.sin(2 * np.pi * 10 * time), (18, 1))
        x[1] *= 2
        features = extract_features(x).reshape(18, 5)
        self.assertEqual(features.shape, (18, 5))
        self.assertEqual(int(np.argmax(features[0])), 2)
        self.assertAlmostEqual(float(features[1, 2] - features[0, 2]), np.log10(4), places=5)

    def test_nonfinite_signal_and_wrong_shape_are_rejected(self):
        for x in [np.ones((17, 1280)), np.full((18, 1280), np.nan),
                  np.full((18, 1280), np.inf)]:
            with self.subTest(shape=x.shape), self.assertRaises(ValueError):
                extract_features(x)
        with self.assertRaises(ValueError):
            CausalFilter(256).transform(np.full((18, 20), np.nan))


class EDFTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.directory.name) / 'synthetic.edf'

    def tearDown(self):
        self.directory.cleanup()

    def test_unit_conversion_and_reversed_lead(self):
        labels = list(CHANNELS)
        labels[0] = 'F7-FP1'
        samples = np.full((18, 256), 10, dtype=np.int16)
        samples[0] *= -1
        write_edf(self.path, labels, samples, unit='mV')
        reader = EDFReader(self.path)
        self.assertEqual(reader.fs, 128)
        data = np.concatenate(list(reader.blocks()), axis=1)
        np.testing.assert_array_equal(data, np.full((18, 256), 10000))

    def test_truncated_and_extra_data_are_rejected(self):
        write_edf(self.path)
        original = self.path.read_bytes()
        for data in [original[:-1], original + b'\x00']:
            self.path.write_bytes(data)
            with self.assertRaises(ValueError):
                EDFReader(self.path)

    def test_missing_lead_unsupported_unit_and_edfplus_are_rejected(self):
        for kwargs in [dict(labels=list(CHANNELS)[:-1]), dict(unit='ohm'),
                       dict(reserved='EDF+C')]:
            write_edf(self.path, **kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                EDFReader(self.path)

    def test_duplicate_lead_conflict_is_not_silently_chosen(self):
        labels = list(CHANNELS) + [CHANNELS[0]]
        samples = np.zeros((19, 256), dtype=np.int16)
        samples[-1] = 500
        write_edf(self.path, labels, samples)
        reader = EDFReader(self.path)
        with self.assertRaises(ValueError):
            list(reader.blocks())

    def test_preview_at_file_end_matches_full_causal_pipeline(self):
        rng = np.random.default_rng(22)
        samples = rng.integers(-150, 150, size=(18, 70*256), dtype=np.int16)
        write_edf(self.path, samples=samples, fs=256, records=70)
        reader = EDFReader(self.path)
        blocks = np.concatenate(list(reader.blocks()), axis=1)
        expected = (CausalFilter(256).transform((blocks*1e-6).astype(np.float32).astype(float))*1e6).astype(np.float32)
        preview = reader.segment(60, 10, channels=18, max_points=2000)
        np.testing.assert_array_equal(preview['samples'], expected[:, 60*128:70*128])
        self.assertEqual(preview['t'][-1], 10-1/128)
        for args in [(60,11),(True,10),(0,61),(0,1,19),(0,1,8,100000)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                reader.segment(*args)

    def test_preview_keeps_duplicate_lead_validation(self):
        labels = list(CHANNELS) + [CHANNELS[0]]
        samples = np.zeros((19, 256), dtype=np.int16)
        samples[-1] = 500
        write_edf(self.path, labels, samples)
        with self.assertRaises(ValueError):
            EDFReader(self.path).segment(0, 1)


if __name__ == '__main__':
    unittest.main()

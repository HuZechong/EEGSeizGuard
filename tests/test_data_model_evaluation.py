"""数据泄漏、部署模型校验和评价口径的独立回归测试。"""
import copy
from pathlib import Path
import tempfile
import threading
import unittest

import numpy as np

from eegseizguard.config import feature_contract
from eegseizguard.data import Dataset, canonical_patient, load_dataset, save_dataset
from eegseizguard.evaluation import evaluate
from eegseizguard.model import load_model, predict, train
from eegseizguard.storage import Cancelled, write_json


def dataset():
    """训练、验证、测试各自独立患者；所有信号为合成特征。"""
    x = np.tile(np.arange(8, dtype=float)[:, None], (1, 90))
    return Dataset(
        X=x, y=np.array([0, 1, 0, 1, 0, 1, 0, 1]),
        patient=np.array(['p1', 'p1', 'p2', 'p2', 'v1', 'v1', 't1', 't1']),
        split=np.array(['train'] * 4 + ['validation'] * 2 + ['test'] * 2),
        time_seconds=np.array([40, 50, 40, 50, 40, 50, 40, 50], dtype=float),
        record=np.array(['r1', 'r1', 'r2', 'r2', 'rv', 'rv', 'rt', 'rt']),
        event=np.array(['', 'e1', '', 'e2', '', 'ev', '', 'et']),
        metadata={'feature_contract': feature_contract(), 'kind': 'synthetic_test'},
    )


def model():
    return dict(schema_version=1, feature_contract=feature_contract(),
                mean=[0.] * 90, scale=[1.] * 90, coef=[0.01] * 90,
                intercept=0., threshold=.5, training_patients=['p1'],
                validation_patients=['v1'])


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()


class DatasetTests(TemporaryFiles):
    def test_one_record_cannot_be_assigned_to_multiple_patients(self):
        data=dataset()
        data.record[2]=data.record[0]
        with self.assertRaisesRegex(ValueError,'记录'):
            data.validate()

    def test_positive_windows_require_event_identity(self):
        data=dataset()
        data.event[1]=''
        with self.assertRaisesRegex(ValueError,'事件'):
            data.validate()

    def test_byte_string_fields_are_decoded_consistently(self):
        data=dataset()
        for name in ('patient','split','record','event'):
            setattr(data,name,getattr(data,name).astype('S'))
        self.assertEqual(data.validate().patient[0],'p1')
        self.assertEqual(data.split[0],'train')

    def test_patient_normalization_is_idempotent_and_accepts_canonical_ids(self):
        expected = 'CHB-MIT:chb01+chb21'
        for identity in ('chb01', 'chb21', 'CHB01', 'CHB-MIT:chb01',
                         'CHB-MIT:chb21', expected, b'chb21'):
            with self.subTest(identity=identity):
                normalized = canonical_patient(identity)
                self.assertEqual(normalized, expected)
                self.assertEqual(canonical_patient(normalized), normalized)

    def test_byte_array_patient_aliases_cannot_cross_partitions(self):
        data = dataset()
        data.patient = np.array([b'chb01'] * 4 + [b'chb21'] * 2 + [b'chb04'] * 2)
        with self.assertRaises(ValueError):
            data.validate()

    def test_patient_aliases_cannot_cross_partitions(self):
        data = dataset()
        data.patient = np.array(['chb01'] * 4 + ['chb21'] * 2 + ['chb04'] * 2)
        with self.assertRaisesRegex(ValueError, '泄漏'):
            data.validate()

    def test_duplicate_window_is_rejected(self):
        data = dataset()
        data.time_seconds[1] = data.time_seconds[0]
        with self.assertRaises(ValueError):
            data.validate()

    def test_nonfinite_features_unknown_label_values_and_protocol_mismatch(self):
        for bad in ('nan', 'inf', 'label', 'schema'):
            data = dataset()
            if bad == 'nan':
                data.X[0, 0] = np.nan
            elif bad == 'inf':
                data.X[0, 0] = np.inf
            elif bad == 'label':
                data.y[0] = 2
            else:
                data.metadata['feature_contract']['channels'].reverse()
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                data.validate()

    def test_npz_roundtrip_and_object_array_rejection(self):
        data = dataset()
        file = self.path / 'features.npz'
        save_dataset(file, data)
        restored = load_dataset(file)
        np.testing.assert_array_equal(restored.X, data.X)
        np.testing.assert_array_equal(restored.y, data.y)
        # 对象数组必须由 allow_pickle=False 拒绝，不能反序列化执行内容。
        np.savez_compressed(file, X=data.X.astype(object), y=data.y,
                            patient=data.patient, split=data.split,
                            time_seconds=data.time_seconds, record=data.record,
                            event=data.event, metadata=np.array('{}'))
        with self.assertRaises(ValueError):
            load_dataset(file)


class ModelTests(TemporaryFiles):
    def test_valid_json_model_has_stable_probabilities(self):
        file = self.path / 'model.json'
        write_json(file, model())
        loaded = load_model(file)
        p = predict(np.zeros((2, 90)), loaded)
        np.testing.assert_array_equal(p, [.5, .5])

    def test_invalid_dimensions_scale_and_threshold_are_rejected(self):
        for bad in ('coef', 'scale', 'threshold'):
            value = model()
            if bad == 'coef':
                value['coef'] = [0.] * 89
            elif bad == 'scale':
                value['scale'][0] = 0.
            else:
                value['threshold'] = True
            file = self.path / 'model.json'
            write_json(file, value)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                load_model(file)

    def test_json_vectors_are_numeric_without_implicit_bool_or_string_conversion(self):
        for invalid in (True, '1.5'):
            value = model()
            value['mean'][0] = invalid
            file = self.path / 'model.json'
            write_json(file, value)
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                load_model(file)

    def test_model_patient_aliases_cannot_overlap(self):
        value = model()
        value['training_patients'] = ['chb01']
        value['validation_patients'] = ['CHB-MIT:chb21']
        file = self.path / 'model.json'
        write_json(file, value)
        with self.assertRaises(ValueError):
            load_model(file)

    def test_validation_and_test_features_do_not_affect_fit(self):
        data = dataset()
        first_dir, second_dir = self.path / 'first', self.path / 'second'
        first = train(data, first_dir, lambda *args: None, threading.Event())
        altered = copy.deepcopy(data)
        altered.X[altered.split != 'train'] += 1e6
        second = train(altered, second_dir, lambda *args: None, threading.Event())
        for name in ('mean', 'scale', 'coef', 'intercept'):
            np.testing.assert_array_equal(first[name], second[name])
        np.testing.assert_allclose(first['mean'], np.full(90, 1.5))

    def test_single_class_train_and_cancel_do_not_write_model(self):
        data = dataset()
        data.y[data.split == 'train'] = 0
        with self.assertRaises(ValueError):
            train(data, self.path, lambda *args: None, threading.Event())
        data = dataset()
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(Cancelled):
            train(data, self.path, lambda *args: None, cancelled)
        self.assertFalse((self.path / 'model.json').exists())


class EvaluationTests(unittest.TestCase):
    def test_unknown_is_excluded_from_denominator(self):
        result = evaluate(np.array([0, 1, -1]), [.1, .9, .999], ['p1', 'p1', 'p1'])
        self.assertEqual(result['excluded_unknown'], 1)
        self.assertEqual(result['pooled']['n'], 2)
        self.assertEqual(result['pooled']['auroc'], 1.)
        self.assertEqual(result['pooled']['ap'], 1.)
        self.assertEqual(result['pooled']['prevalence'], .5)

    def test_single_class_metrics_are_null_and_macro_support_reported(self):
        result = evaluate(np.array([0, 1, 0, 0]), [.1, .9, .1, .9],
                          ['mixed', 'mixed', 'negative', 'negative'])
        self.assertEqual(result['macro_support']['auroc'], 1)
        self.assertEqual(result['macro_support']['ap'], 1)
        negative = next(row for row in result['patients'] if row['patient'] == 'negative')
        self.assertIsNone(negative['auroc'])
        self.assertIsNone(negative['ap'])
        self.assertEqual(result['macro']['auroc'], 1.)

    def test_macro_patient_weight_is_independent_of_window_count(self):
        result = evaluate(np.array([0, 1, 0, 0, 1, 1]),
                          [.1, .9, .9, .8, .2, .1],
                          ['short', 'short', 'long', 'long', 'long', 'long'])
        self.assertAlmostEqual(result['macro']['auroc'], .5)

    def test_all_unknown_invalid_probability_and_label_are_rejected(self):
        for y, p in [(np.array([-1, -1]), [.1, .9]),
                     (np.array([0, 1]), [.1, np.nan]),
                     (np.array([0, 1]), [.1, 1.1]),
                     (np.array([0, 2]), [.1, .9])]:
            with self.subTest(y=y, p=p), self.assertRaises(ValueError):
                evaluate(y, p, ['p1', 'p1'])


if __name__ == '__main__':
    unittest.main()

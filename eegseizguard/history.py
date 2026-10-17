"""只读导入既有研究预测，重新计算窗口指标并保留事件评估来源。"""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
import re

from .storage import atomic_bytes, check_cancel, file_hash, read_json, write_json


def _number(value, label, *, lower=None, upper=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'历史预测的 {label} 必须是有限数值。')
    if lower is not None and value < lower or upper is not None and value > upper:
        raise ValueError(f'历史预测的 {label} 超出合法范围。')
    return float(value)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'历史预测含重复 JSON 键：{key}')
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError(f'历史预测含非法数值：{value}')


def _metric(values, names):
    for name in names:
        if name in values:
            return values[name]
    raise ValueError('重算指标缺少字段：' + '/'.join(names))


def _verify_metric(actual, expected, label):
    if actual is None or expected is None:
        equal = actual is None and expected is None
    else:
        actual = _number(actual, label)
        expected = _number(expected, label)
        equal = math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)
    if not equal:
        raise ValueError(f'重算 {label} 与原始研究指标不一致；停止生成完成结果。')


def _metric_checks(recomputed, original):
    macro = recomputed['macro']
    checks = {}
    for label, aliases, source_key in (
            ('auroc', ('auroc', 'macro_auroc'), 'macro_auroc'),
            ('ap', ('ap', 'auprc', 'macro_ap', 'macro_auprc'), 'macro_auprc')):
        actual = _metric(macro, aliases)
        expected = original[source_key]
        _verify_metric(actual, expected, 'macro ' + label)
        checks[label] = dict(recomputed=actual, source=expected, matches=True)
    patients = recomputed['patients']
    if isinstance(patients, list):
        patients = {row.get('patient', row.get('patient_id')): row for row in patients}
    if not isinstance(patients, dict) or set(patients) != set(original['per_patient']):
        raise ValueError('重算患者清单与历史窗口指标清单不一致。')
    for patient, expected in original['per_patient'].items():
        row = patients[patient]
        for label, aliases, source_key in (
                ('auroc', ('auroc',), 'auroc'),
                ('ap', ('ap', 'auprc'), 'auprc')):
            _verify_metric(_metric(row, aliases), expected[source_key], patient + ' ' + label)
    checks['per_patient_matches'] = True
    return checks


def _formatted(value):
    return '不可计算' if value is None else f'{value:.8f}'


def import_history(project, out, log=None, cancel=None):
    """读取原项目封存测试预测；不打开 EEG、不训练或调整历史模型。

    project 可以指向 EEGProject 或其 prediction_restart 子目录。完成结果包含
    全部未知标签窗口，指标函数自行排除未知标签。输出目录不能与原项目交叠。
    """
    from .evaluation import evaluate

    project = Path(project).expanduser().resolve()
    if (project / 'prediction_restart').is_dir():
        project = project / 'prediction_restart'
    if not project.is_dir():
        raise ValueError('原研究项目目录不存在。')
    out = Path(out).expanduser().resolve()
    if out.is_relative_to(project) or project.is_relative_to(out):
        raise ValueError('历史导入的输出目录不能位于原研究项目内或包含原项目。')
    roots = {
        'predictions': project / '05_raw_baseline/artifacts/r1_main/logreg_test_local/predictions.jsonl',
        'prediction_metadata': project / '05_raw_baseline/artifacts/r1_main/logreg_test_local/predictions_meta.json',
        'window_metrics': project / '05_raw_baseline/artifacts/r1_main/logreg_test_local/window_metrics.json',
        'event_metrics': project / '06_evaluation/artifacts/r1_main/logreg_test_local/metrics.json',
        'split': project / '04_patient_split/artifacts/r1_main/split.json',
    }
    check_cancel(cancel)
    for name, path in roots.items():
        if not path.is_file() or not path.resolve().is_relative_to(project):
            raise ValueError(f'缺少合法的历史 {name} 文件：{path}')
    if roots['predictions'].stat().st_size > 512 * 1024 * 1024:
        raise ValueError('历史预测文件超过512MB的导入上限。')
    targets = [out / name for name in ('metrics.json', 'predictions.csv', 'history_report.txt')]
    if any(path.exists() for path in targets):
        raise FileExistsError('输出已有历史导入文件；请使用新的任务输出目录。')
    original = read_json(roots['window_metrics'])
    prediction_metadata = read_json(roots['prediction_metadata'])
    events = read_json(roots['event_metrics'], limit=16 * 1024 * 1024)
    split = read_json(roots['split'])
    if original.get('partition') != 'test' or original.get('scientific_result') is not True:
        raise ValueError('历史窗口指标必须是已完成的真实测试结果。')
    if events.get('partition') != 'test' or events.get('scientific_result') is not True or events.get('model') != 'logreg':
        raise ValueError('历史事件指标必须是已完成的 LogReg 测试结果。')
    if (prediction_metadata.get('partition') != 'test' or prediction_metadata.get('model') != 'logreg'
            or prediction_metadata.get('scientific_result') is not True):
        raise ValueError('历史预测元数据必须指向真实的 LogReg 测试结果。')
    expected_patients = split.get('test')
    if (not isinstance(expected_patients, list) or not expected_patients
            or len(expected_patients) != len(set(expected_patients))):
        raise ValueError('历史冻结测试患者清单不合法。')
    expected_patients = set(expected_patients)
    hashes = {name: file_hash(path) for name, path in roots.items()}
    if prediction_metadata.get('predictions_sha256') != hashes['predictions']:
        raise ValueError('历史预测内容与原研究元数据 SHA256 不一致。')
    if prediction_metadata.get('split_sha256') != hashes['split']:
        raise ValueError('历史患者划分与预测元数据 SHA256 不一致。')
    if events.get('checkpoint_sha256') != prediction_metadata.get('checkpoint_sha256'):
        raise ValueError('历史预测和事件评估不是同一个模型。')
    for key in ('split_sha256', 'config_sha256', 'windows_sha256', 'labels_summary_sha256', 'cache_manifest_sha256'):
        if events.get('identity', {}).get(key) != prediction_metadata.get(key):
            raise ValueError(f'历史预测和事件评估的 {key} 不一致。')
    if log:
        log('只读导入原项目 LogReg 测试预测，并重新核对全部患者窗口指标。')
    labels, probabilities, patients, ids = [], [], [], set()
    times = {}
    text = io.StringIO(newline='')
    writer = csv.writer(text, lineterminator='\n')
    writer.writerow(['patient', 'record', 'time_seconds', 'y', 'probability'])
    with roots['predictions'].open('r', encoding='utf-8-sig', newline='') as stream:
        for index, line in enumerate(iter(lambda: stream.readline(65537), ''), 1):
            check_cancel(cancel)
            if len(line) > 65536:
                raise ValueError(f'历史预测第 {index} 行超过64KB。')
            if not line.strip():
                raise ValueError(f'历史预测第 {index} 行为空。')
            if index > 1000000:
                raise ValueError('历史预测超过100万窗口的导入上限。')
            try:
                row = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
                if not isinstance(row, dict):
                    raise ValueError('每行必须为 JSON 对象。')
                window, patient, record = row['window_id'], row['patient_id'], row['record_id']
                if (not isinstance(window, str) or not window or len(window) > 512
                        or any(c.isspace() for c in window) or window in ids):
                    raise ValueError('window_id 为空、异常或重复。')
                if not isinstance(patient, str) or patient not in expected_patients:
                    raise ValueError('patient_id 不在冻结测试患者清单内。')
                if not isinstance(record, str) or not re.fullmatch(r'CHB-MIT:chb\d{2}_[A-Za-z0-9_+-]+', record):
                    raise ValueError('record_id 格式异常。')
                if not record.startswith(patient + '_') or not window.startswith(record + ':'):
                    raise ValueError('患者、记录与窗口标识不一致。')
                label = row['y']
                if type(label) is not int or label not in (-1, 0, 1):
                    raise ValueError('y 只能为整数 -1、0 或 1。')
                if row.get('partition') != 'test':
                    raise ValueError('预测不是冻结测试分区。')
                probability = _number(row['prob'], 'prob', lower=0, upper=1)
                end = _number(row['end_seconds'], 'end_seconds', lower=0)
                if end <= times.get(record, -1):
                    raise ValueError('同一记录的决策时间不严格递增。')
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f'历史预测第 {index} 行校验失败：{exc}') from exc
            ids.add(window)
            times[record] = end
            labels.append(label)
            probabilities.append(probability)
            patients.append(patient)
            writer.writerow([patient, record, format(end, '.12g'), label, format(probability, '.17g')])
            if log and index % 10000 == 0:
                log(f'已校验 {index:,} 个历史窗口。')
    if not labels or set(patients) != expected_patients:
        raise ValueError('历史预测为空或没有覆盖完整冻结测试患者。')
    check_cancel(cancel)
    recomputed = evaluate(labels, probabilities, patients, .5)
    checks = _metric_checks(recomputed, original)
    check_cancel(cancel)
    if any(file_hash(path) != hashes[name] for name, path in roots.items()):
        raise ValueError('历史源文件在读取过程中变化；停止导入。')
    event_keys = ('eligible_events', 'predicted_events', 'fully_covered_events',
                  'event_sensitivity', 'false_alarms', 'alarms', 'far_per_hour',
                  'conditional_background_hours', 'monitoring_hours', 'time_in_warning_fraction')
    event_facts = {key: events['event'].get(key) for key in event_keys}
    alarm = events['alarm_policy']
    history = dict(kind='read_only_historical_import', project=str(project),
                   source_files={name: dict(path=str(path), sha256=hashes[name]) for name, path in roots.items()},
                   source_sha256=hashes['predictions'], windows=len(labels),
                   unknown_windows=labels.count(-1), patient_count=len(expected_patients),
                   score_semantics='uncalibrated', threshold=.5,
                   metric_verification=checks, original_window_metrics=original,
                   historical_event_facts=event_facts,
                   historical_alarm_policy=dict(strategy=alarm.get('strategy'), threshold=alarm.get('threshold'),
                       selection_partition=alarm.get('selection_partition'), selection_rule=alarm.get('selection_rule')),
                   event_metrics_recomputed=False)
    recomputed['history'] = history
    recomputed['source_kind'] = 'historical'
    report = (
        '头皮脑电癫痫发作预测软件 只读历史导入报告\n'
        f'来源：{roots["predictions"]}\nSHA256：{hashes["predictions"]}\n'
        f'窗口：{len(labels):,}；患者：{len(expected_patients)}；未知标签窗口：{labels.count(-1):,}\n'
        f'宏 AUROC：{_formatted(checks["auroc"]["recomputed"])}；原始：{_formatted(checks["auroc"]["source"])}；核对一致\n'
        f'宏 AP：{_formatted(checks["ap"]["recomputed"])}；原始：{_formatted(checks["ap"]["source"])}；核对一致\n'
        '逐患者 AUROC/AP 与原始结果核对一致。未知标签保持 -1，并从监督指标中排除。\n'
        f'原研究报警策略：{alarm.get("strategy")}；策略在 {alarm.get("selection_partition")} 分区冻结。\n'
        f'原事件评估：合格事件 {event_facts["eligible_events"]}，命中 {event_facts["predicted_events"]}，'
        f'事件敏感度 {event_facts["event_sensitivity"]}，误报警 {event_facts["false_alarms"]}，'
        f'条件性 FAR/h {event_facts["far_per_hour"]}。\n'
        '事件指标来自原研究评估文件，本次未重新执行序列报警评估。零误报警须同时结合事件命中数解读。\n'
        '输出为未校准风险分数，不表示临床发作概率。本次未训练模型、调整阈值或修改原研究数据。\n')
    check_cancel(cancel)
    write_json(targets[0], recomputed)
    atomic_bytes(targets[1], text.getvalue().encode('utf-8-sig'))
    atomic_bytes(targets[2], report.encode('utf-8'))
    return dict(summary=f'只读导入 {len(labels):,} 窗口、{len(expected_patients)} 名测试患者；原指标核对一致。',
                metrics=recomputed, source=history,
                artifacts=[dict(name='metrics.json', label='历史指标与来源核对'),
                           dict(name='predictions.csv', label='历史窗口风险分数'),
                           dict(name='history_report.txt', label='只读历史导入报告')],
                output_paths=dict(metrics=str(targets[0]), predictions=str(targets[1]), report=str(targets[2])))

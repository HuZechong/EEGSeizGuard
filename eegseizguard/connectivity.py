"""从真实时序估计头皮双极导联相干性；不推断皮层网络或发作风险。

采用固定 Welch 参数与有界十秒窗口。三维坐标只是标准电极示意位置的
中点方向投影，不能替代个体影像、源定位或电极实测位置。
"""
import csv
import hashlib
import io
import math
from pathlib import Path

import numpy as np
from scipy import signal

from .config import BANDS, CHANNELS, PROTOCOL
from .signal import CausalFilter, EDFReader
from .storage import atomic_bytes, check_cancel, input_file, write_json

FS = PROTOCOL.sampling_hz
WIDTH = FS * PROTOCOL.window_seconds
BAND_IDS = ('delta', 'theta', 'alpha', 'beta', 'gamma')
BAND_LABELS = ('δ Delta', 'θ Theta', 'α Alpha', 'β Beta', 'γ Gamma')
MAX_FRAMES = 60
DEMO_SEED = 1701


def network_nodes():
    """球面位置 x 向右、y 向上、z 向前，仅供空间布局。"""
    electrodes = {
        'FP1': (-.30, .43, .85), 'FP2': (.30, .43, .85),
        'F7': (-.82, .20, .52), 'F8': (.82, .20, .52),
        'F3': (-.44, .72, .45), 'F4': (.44, .72, .45),
        'C3': (-.58, .80, 0), 'C4': (.58, .80, 0),
        'T7': (-.98, .12, 0), 'T8': (.98, .12, 0),
        'P7': (-.82, .20, -.52), 'P8': (.82, .20, -.52),
        'P3': (-.44, .72, -.45), 'P4': (.44, .72, -.45),
        'O1': (-.30, .43, -.85), 'O2': (.30, .43, -.85),
        'FZ': (0, .85, .53), 'CZ': (0, 1, 0), 'PZ': (0, .85, -.53),
    }
    nodes = []
    for index, name in enumerate(CHANNELS):
        pair = name.split('-')
        positions = [np.asarray(electrodes[e], dtype=float) for e in pair]
        midpoint = sum(p / np.linalg.norm(p) for p in positions)
        midpoint /= np.linalg.norm(midpoint)
        hemisphere = 'midline' if abs(midpoint[0]) < .001 else ('left' if midpoint[0] < 0 else 'right')
        region = ('额侧' if midpoint[2] > .35 else ('枕顶侧' if midpoint[2] < -.35 else '中央侧'))
        nodes.append(dict(id=name, index=index, label=name, electrodes=pair,
                          position=np.round(midpoint, 6).tolist(),
                          hemisphere=hemisphere, region=region))
    return nodes


def _json_numbers(values, decimals=7):
    values = np.round(np.asarray(values, dtype=float), decimals)
    result = values.astype(object)
    result[~np.isfinite(values)] = None
    return result.tolist()


def analyze_window(samples_uv, start_seconds):
    """返回一帧。不可估计的相干性用 null；频带为左闭右开，末带含45Hz。"""
    x = np.asarray(samples_uv, dtype=np.float64)
    if x.shape != (len(CHANNELS), WIDTH) or not np.isfinite(x).all():
        raise ValueError('网络分析需要18导联、128Hz、完整10秒的有限时序。')
    if np.max(np.abs(x)) > 1e12:
        raise ValueError('信号量级超过数值处理预算，请核验EDF物理单位与校准。')
    if isinstance(start_seconds, bool) or not isinstance(start_seconds, (int, float)) or not math.isfinite(start_seconds):
        raise ValueError('网络窗口起点必须为有限秒数。')
    # 与 scipy.signal.coherence 使用相同的分段、去均值和周期 Hann 窗。
    segments = np.lib.stride_tricks.sliding_window_view(x, 256, axis=1)[:, ::128, :]
    centered = segments - segments.mean(axis=-1, keepdims=True)
    taper = signal.get_window('hann', 256, fftbins=True)
    transformed = np.fft.rfft(centered * taper, axis=-1)
    cross = np.einsum('isf,jsf->ijf', transformed.conj(), transformed, optimize=True) / segments.shape[1]
    power = np.real(np.diagonal(cross, axis1=0, axis2=1)).T.copy()
    denominator = power[:, None, :] * power[None, :, :]
    coherence = np.full_like(denominator, np.nan)
    valid_channels = np.std(x, axis=1) > 1e-8
    # 只剔除相对于本导联最大谱功率低于浮点量级的频点，不人为提高连接。
    meaningful = power > np.maximum(np.max(power, axis=1, keepdims=True) * 1e-12, np.finfo(float).tiny)
    valid_bins = meaningful[:, None, :] & meaningful[None, :, :]
    valid_bins &= valid_channels[:, None, None] & valid_channels[None, :, None]
    np.divide(np.abs(cross) ** 2, denominator, out=coherence, where=valid_bins)
    np.clip(coherence, 0, 1, out=coherence)
    frequencies = np.fft.rfftfreq(256, d=1 / FS)
    psd = power / (FS * np.sum(taper ** 2))
    psd[:, 1:-1] *= 2
    matrices, bandpower = {}, {}
    for index, (name, (lo, hi)) in enumerate(zip(BAND_IDS, BANDS)):
        mask = (frequencies >= lo) & ((frequencies <= hi) if index == 4 else (frequencies < hi))
        selected = coherence[:, :, mask]
        counts = np.isfinite(selected).sum(axis=-1)
        average = np.divide(np.nansum(selected, axis=-1), counts,
                            out=np.full((18, 18), np.nan), where=counts > 0)
        matrices[name] = _json_numbers(average)
        # 功率与原模型保持同一频带积分边界，但此处展示线性 µV²。
        grid = np.r_[lo, frequencies[(frequencies > lo) & (frequencies < hi)], hi]
        values = np.stack([np.interp(grid, frequencies, row) for row in psd])
        bandpower[name] = _json_numbers(np.trapezoid(values, grid, axis=-1))
    # 保留全部128Hz采样；简单抽点会让高频EEG在波形视图中混叠。
    display_indices = np.arange(WIDTH)
    psd_visible = frequencies <= 45
    flat = [CHANNELS[i] for i in np.flatnonzero(~valid_channels)]
    warnings = ['平坦导联不参与连接估计：' + '、'.join(flat)] if flat else []
    return dict(start_seconds=float(start_seconds), end_seconds=float(start_seconds + 10),
                matrices=matrices, bandpower=bandpower,
                waveform=dict(t=_json_numbers(display_indices / FS),
                              samples=_json_numbers(x[:, display_indices], 4),
                              units='µV', display_sampling_hz=FS,
                              note='完整128Hz采样，与相干性输入一致；屏幕像素不足时应缩短显示时间范围。'),
                psd=dict(frequency_hz=_json_numbers(frequencies[psd_visible]),
                         values=_json_numbers(psd[:, psd_visible]), units='µV²/Hz'),
                quality=dict(valid_channels=valid_channels.tolist(), flat_channels=flat,
                             warnings=warnings, flat_std_threshold_uv=1e-8))


def _validated_params(params):
    if not isinstance(params, dict):
        raise ValueError('网络分析参数必须为对象。')
    unknown = set(params) - {'source', 'edf_path', 'start_seconds', 'duration_seconds'}
    if unknown:
        raise ValueError('无法识别的网络参数：' + '、'.join(sorted(unknown)))
    source = params.get('source', 'demo')
    if not isinstance(source, str) or source not in {'demo', 'edf'}:
        raise ValueError('网络数据来源必须为 demo 或 edf。')
    values = []
    for key, default in [('start_seconds', 30), ('duration_seconds', 120)]:
        value = params.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value % 10:
            raise ValueError(f'{key} 必须为10的整数倍秒数。')
        values.append(int(value))
    start, duration = values
    if not 30 <= start <= 86400 - 10:
        raise ValueError('起点需在30秒预热之后，且在24小时记录预算内。')
    if not 10 <= duration <= MAX_FRAMES * 10:
        raise ValueError('分析时长须为10至600秒，最多60个窗口。')
    if start + duration > 86400:
        raise ValueError('分析终点超过24小时记录预算。')
    if source == 'demo' and start + duration > 3600:
        raise ValueError('合成功能演示的终点最多为3600秒。')
    if source == 'demo' and params.get('edf_path'):
        raise ValueError('演示模式不能同时指定EDF文件，以免混淆数据来源。')
    return source, start, duration


def _sha256(path, cancel):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            check_cancel(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def _frames_from_blocks(blocks, native_fs, start, duration, log, cancel):
    """从记录零点滤波，仅保留分析段和不足一帧的缓冲。"""
    causal = CausalFilter(native_fs)
    beginning, finish = start * FS, (start + duration) * FS
    seen, buffer, frames = 0, np.empty((18, 0), dtype=np.float32), []
    for block in blocks:
        check_cancel(cancel)
        volts = (block * 1e-6).astype(np.float32).astype(np.float64)
        filtered = (causal.transform(volts) * 1e6).astype(np.float32)
        a, b = max(beginning - seen, 0), min(finish - seen, filtered.shape[1])
        if b > a:
            buffer = np.concatenate((buffer, filtered[:, a:b]), axis=1)
        seen += filtered.shape[1]
        while buffer.shape[1] >= WIDTH:
            check_cancel(cancel)
            frames.append(analyze_window(buffer[:, :WIDTH], start + 10 * len(frames)))
            buffer = buffer[:, WIDTH:]
            log(f'计算第 {len(frames)}/{duration // 10} 个相干性窗口',
                15 + int(70 * len(frames) / (duration // 10)))
        if seen >= finish:
            break
    if len(frames) != duration // 10 or buffer.shape[1]:
        raise ValueError('EDF不足以覆盖请求的完整网络分析窗口。')
    return frames


def _demo_blocks(end_seconds, cancel):
    """固定种子的合成多导联时序；没有真实患者，也不模拟诊断标签。"""
    rng = np.random.default_rng(DEMO_SEED)
    time_seen = 0
    # 逐块滤波随机驱动源，左右共同成分随时间缓变，供交互验证。
    sos = signal.butter(2, [4, 8], btype='bandpass', fs=FS, output='sos')
    zi = np.zeros((len(sos), 3, 2))
    nodes = network_nodes()
    sides = np.array([n['position'][0] for n in nodes])
    for _ in range(math.ceil(end_seconds / 10)):
        check_cancel(cancel)
        t = (np.arange(WIDTH) + time_seen) / FS
        shared, zi = signal.sosfilt(sos, rng.normal(size=(3, WIDTH)), axis=-1, zi=zi)
        independent = rng.normal(scale=8, size=(18, WIDTH))
        blend = .5 + .45 * np.sin(2 * np.pi * t / 100)
        left = 48 * shared[0] * blend + 22 * shared[2]
        right = 48 * shared[1] * (1 - blend) + 22 * shared[2]
        weights = np.clip((sides + .8) / 1.6, 0, 1)[:, None]
        common = (1 - weights) * left + weights * right
        alpha = (6 + np.arange(18)[:, None] % 4) * np.sin(2 * np.pi * 10 * t + np.arange(18)[:, None] / 3)
        yield independent + common + alpha
        time_seen += WIDTH


def _payload(frames, source):
    return dict(schema_version=1, software_version='V1.0', source=source,
                method=dict(name='Welch magnitude-squared coherence',
                            display_name='Welch 幅值平方相干性',
                            sampling_hz=FS, window_seconds=10, stride_seconds=10,
                            welch_segment_seconds=2, welch_samples=256, overlap_samples=128,
                            taper='periodic Hann', detrend='constant',
                            estimator='mean_f(|mean_segments(conj(FFT_i) * FFT_j)|² / (P_ii * P_jj))',
                            band_average='频带内离散频点等权平均；左闭右开，gamma含45Hz端点',
                            preprocessing='从文件零点连续因果0.5–45Hz带通、60Hz陷波、因果抗混叠降采样至128Hz；前30秒预热',
                            quantization='float32 V input; float32 µV filtered samples',
                            positioning='标准电极示意坐标的中点方向投影；非个体解剖定位',
                            coordinate_axes=dict(x='右', y='上', z='前'),
                            bandpower_units='µV²', matrix_missing_value=None,
                            steps=['原始时序与来源核验', '连续因果滤波与18双极导联',
                                   '10秒窗口与Welch分段', '五频带相干性矩阵', '阈值筛选与三维示意', '逐窗回放与导出'],
                            limitations=['连接表示头皮双极导联的统计相干性，不表示因果或解剖连接。',
                                         '共用电极、参考方式和容积传导会影响相干性；隐藏共用电极连接不能完全消除这些影响。',
                                         '三维外形为示意图，不是患者MRI或皮层源定位。',
                                         '10秒窗口的有限谱平均会造成估计偏差；阈值仅用于显示，未经统计显著性检验。',
                                         '网络探索不参与逻辑回归预测，不得依据连接数推断发作风险。']),
                nodes=network_nodes(),
                bands=[dict(id=name, label=label, low=lo, high=hi)
                       for name, label, (lo, hi) in zip(BAND_IDS, BAND_LABELS, BANDS)],
                frames=frames)


def prepare_demo(start_seconds=30, duration_seconds=120, log=None, cancel=None):
    """生成可离线展示的确定性数据，返回值明确标记为 synthetic_demo。"""
    _, start, duration = _validated_params(dict(source='demo', start_seconds=start_seconds,
                                               duration_seconds=duration_seconds))
    log = log or (lambda message, progress: None)
    frames = _frames_from_blocks(_demo_blocks(start + duration, cancel), FS,
                                start, duration, log, cancel)
    return _payload(frames, dict(kind='synthetic_demo', label='合成数据 · 功能演示',
                                seed=DEMO_SEED, contains_patient_data=False,
                                native_sampling_hz=FS, analysis_start_seconds=start,
                                analysis_duration_seconds=duration,
                                note='固定随机种子的合成电信号，仅用于交互和流程验证，不代表真实预测效果。'))


def _write_edges(path, network, cancel=None):
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerow(['source_kind', 'start_seconds', 'end_seconds', 'band',
                     'lead_a', 'lead_b', 'coherence', 'shared_electrode'])
    for frame in network['frames']:
        check_cancel(cancel)
        for name, matrix in frame['matrices'].items():
            for i in range(18):
                for j in range(i + 1, 18):
                    if matrix[i][j] is not None:
                        shared = bool(set(network['nodes'][i]['electrodes']) & set(network['nodes'][j]['electrodes']))
                        writer.writerow([network['source']['kind'], frame['start_seconds'], frame['end_seconds'],
                                         name, CHANNELS[i], CHANNELS[j], matrix[i][j], int(shared)])
    atomic_bytes(path, buffer.getvalue().encode('utf-8-sig'))


def run_connectivity(params, out, log, cancel):
    """统一任务入口。完整网络单独落盘，不塞入任务状态响应。"""
    source, start, duration = _validated_params(params)
    check_cancel(cancel)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    log('核验网络分析参数与信号来源', 5)
    if source == 'demo':
        network = prepare_demo(start, duration, log, cancel)
    else:
        path = input_file(params.get('edf_path'), '.edf')
        before = path.stat()
        reader = EDFReader(path)
        if reader.duration > 86400:
            raise ValueError('单条EDF超过24小时预算，请先核验记录。')
        if start + duration > reader.duration + 1e-8:
            raise ValueError(f'分析终点 {start + duration} 秒超过EDF时长 {reader.duration:g} 秒。')
        digest = _sha256(path, cancel)
        log('从记录零点连续滤波，保留原始状态与采样相位', 10)
        frames = _frames_from_blocks(reader.blocks(), reader.fs, start, duration, log, cancel)
        check_cancel(cancel)
        after_digest = _sha256(path, cancel)
        after = path.stat()
        if digest != after_digest or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('EDF在网络分析期间发生变化，结果不予导出。')
        network = _payload(frames, dict(kind='edf', label=path.name, path=str(path), sha256=digest,
                                       native_sampling_hz=reader.fs, recording_duration_seconds=reader.duration,
                                       analysis_start_seconds=start, analysis_duration_seconds=duration,
                                       annotation_status='网络分析未读取发作标注，不提供风险标签'))
    check_cancel(cancel)
    log('保存网络矩阵、逐帧连接和处理说明', 90)
    write_json(out / 'network.json', network)
    _write_edges(out / 'network_edges.csv', network, cancel)
    lines = ['EEGSeizGuard V1.0 — 头皮导联相干性网络',
             '数据来源：' + network['source']['label'],
             f'分析区间：[{start}, {start + duration}) 秒；{duration // 10} 个连续10秒窗口。',
             '流程：' + ' → '.join(network['method']['steps']),
             '网络输入：完整的18导联128Hz因果预处理时序，不是90维模型特征。',
             'Welch：2秒周期Hann窗，50%重叠，分段去均值。',
             '频带相干性：频带内离散频点等权平均；除最后一带外右端点不计入。',
             '功率：µV²；波形：µV（保留完整128Hz时序）；连接：无量纲[0,1]。',
             'network_edges.csv 包含所有可估计的无向连接，未应用网页显示阈值或共享电极过滤。',
             '平坦导联连接使用null，不以零相干性代替未知。']
    if source == 'demo':
        lines.append('这是合成功能演示，不能作为软件真实效果或患者分析结果。')
    else:
        lines.append('原始EDF SHA256：' + network['source']['sha256'])
    lines.extend(network['method']['limitations'])
    atomic_bytes(out / 'network_report.txt', '\n'.join(lines).encode('utf-8-sig'))
    check_cancel(cancel)
    summary = dict(source_kind=network['source']['kind'], source_label=network['source']['label'],
                   frame_count=len(network['frames']), channels=18, band_count=5,
                   start_seconds=start, duration_seconds=duration,
                   window_seconds=10, sampling_hz=FS,
                   frames_with_warnings=sum(bool(f['quality']['warnings']) for f in network['frames']))
    return dict(summary=f'{network["source"]["label"]} · 18导联 · {len(network["frames"])}个网络窗口',
                network_summary=summary, network_artifact='network.json',
                input_files={} if source == 'demo' else dict(edf=dict(path=str(path), sha256=digest)),
                artifacts=[dict(name='network.json', label='完整网络矩阵与波形'),
                           dict(name='network_edges.csv', label='逐帧连接数据 CSV'),
                           dict(name='network_report.txt', label='网络分析方法与说明')])

"""有界内存的经典 EDF 读取、因果预处理、频带特征与删失标签。

不推断文件之间连续性。EDF+、BDF 和不完整标注必须由专门工具核验，不能
静默当作连续经典 EDF。所有电位校准为微伏，输出点保持原始可用时刻。
"""
import math
from pathlib import Path
import re
import numpy as np
from scipy import signal
from .config import BANDS, CHANNELS, PROTOCOL

def normalize_channel(label):
    label = re.sub(r'\s+', '', label.upper()).removeprefix('EEG')
    parts = label.split('-')
    aliases = {'T3': 'T7', 'T4': 'T8', 'T5': 'P7', 'T6': 'P8'}
    return '-'.join(aliases.get(p, p) for p in parts)

class EDFReader:
    """只接受固定长度记录、合法物理单位、等采样率的18双极导联。

    对同名/反向重复导联逐块核验量化误差，冲突即失败。拒绝缺导联、截断、
    多余字节、非有限校准以及无法验证连续性的 EDF+，避免猜测数据含义。
    """
    def __init__(self, path):
        self.path = Path(path)
        with self.path.open('rb') as handle:
            fixed = handle.read(256)
            if len(fixed) != 256 or fixed[:8].strip() != b'0':
                raise ValueError('不是支持的经典 EDF 文件。')
            def number(a, b, cast=float):
                try:
                    value = cast(fixed[a:b].decode('ascii').strip())
                except (ValueError, UnicodeError) as exc:
                    raise ValueError('EDF 固定头字段不合法。') from exc
                if not math.isfinite(value):
                    raise ValueError('EDF 头字段不是有限数。')
                return value
            self.header_bytes = number(184, 192, int)
            if fixed[192:236].strip():
                raise ValueError('只支持经典 EDF；EDF+ 的时间连续性需单独审计。')
            self.n_records = number(236, 244, int)
            self.record_seconds = number(244, 252)
            count = number(252, 256, int)
            if not 1 <= count <= 256 or self.header_bytes != 256*(1+count):
                raise ValueError('EDF 通道数量或头长度异常。')
            if self.n_records <= 0 or self.record_seconds <= 0:
                raise ValueError('EDF 必须声明正数的记录数量与时长。')
            raw = handle.read(256*count)
            if len(raw) != 256*count:
                raise ValueError('EDF 信号头被截断。')
        fields, offset = {}, 0
        for name, width in [('label',16),('transducer',80),('unit',8),('pmin',8),
                            ('pmax',8),('dmin',8),('dmax',8),('prefilter',80),
                            ('spr',8),('reserved',32)]:
            fields[name] = [raw[offset+i*width:offset+(i+1)*width].decode('ascii').strip()
                            for i in range(count)]
            offset += count*width
        self.channels = []
        for i in range(count):
            channel = {key: fields[key][i] for key in fields}
            for key in ('pmin','pmax','dmin','dmax'):
                channel[key] = float(channel[key])
                if not math.isfinite(channel[key]):
                    raise ValueError('EDF 校准含非有限数。')
            channel['spr'] = int(channel['spr'])
            if channel['spr'] <= 0:
                raise ValueError('EDF 每记录采样点数必须为正。')
            self.channels.append(channel)
        self.offsets = np.cumsum([0]+[c['spr'] for c in self.channels])
        self.record_bytes = int(self.offsets[-1])*2
        if self.record_bytes > 16*1024*1024:
            raise ValueError('单个 EDF 记录超过16MB的内存预算。')
        if self.path.stat().st_size != self.header_bytes+self.record_bytes*self.n_records:
            raise ValueError('EDF 长度与头声明不一致（截断或含多余数据）。')
        labels = [normalize_channel(c['label']) for c in self.channels]
        self.plan = []
        for pair in CHANNELS:
            a, b = pair.split('-')
            options = [(i, 1) for i, lab in enumerate(labels) if lab == pair]
            options += [(i, -1) for i, lab in enumerate(labels) if lab == b+'-'+a]
            if not options:
                raise ValueError(f'缺少双极导联：{pair}；本版不猜测参考方式。')
            self.plan.append(options)
        selected = {i for options in self.plan for i, _ in options}
        rates = [self.channels[i]['spr']/self.record_seconds for i in selected]
        self.fs = rates[0]
        if any(not math.isclose(r, self.fs, abs_tol=1e-9) for r in rates):
            raise ValueError('所需通道的采样率不一致。')
        ratio = self.fs/PROTOCOL.sampling_hz
        if ratio < 1 or not math.isclose(ratio, round(ratio), abs_tol=1e-9):
            raise ValueError('只支持128Hz及其整数倍采样率的因果降采样。')
        self.factor = round(ratio)
        for i in selected:
            c = self.channels[i]
            unit = c['unit'].lower().replace('µ','u').replace('μ','u')
            if unit not in {'v','mv','uv','nv'}:
                raise ValueError(f'不支持的电位单位：{unit}')
            if not c['pmax'] > c['pmin'] or not c['dmax'] > c['dmin']:
                raise ValueError('EDF 校准上下限异常。')
            c['lsb'] = (c['pmax']-c['pmin'])/(c['dmax']-c['dmin'])*{'v':1e6,'mv':1e3,'uv':1,'nv':.001}[unit]
            c['base'] = c['pmin']*{'v':1e6,'mv':1e3,'uv':1,'nv':.001}[unit]
        self.duration = self.n_records*self.record_seconds
        self.selected = selected

    def blocks(self):
        count = max(1, min(int(60/self.record_seconds), (16*1024*1024)//self.record_bytes))
        before = self.path.stat()
        with self.path.open('rb') as handle:
            handle.seek(self.header_bytes)
            for first in range(0, self.n_records, count):
                n = min(count, self.n_records-first)
                raw = handle.read(n*self.record_bytes)
                if len(raw) != n*self.record_bytes:
                    raise ValueError('EDF 在读取过程中被截断。')
                digital = np.frombuffer(raw, dtype='<i2').reshape(n, -1)
                physical = {}
                for i in self.selected:
                    c = self.channels[i]
                    values = digital[:,self.offsets[i]:self.offsets[i+1]].reshape(-1)
                    physical[i] = (values.astype(float)-c['dmin'])*c['lsb']+c['base']
                output = []
                for options in self.plan:
                    index, sign = options[0]
                    base = physical[index]*sign
                    for other, polarity in options[1:]:
                        tolerance = .5*(self.channels[index]['lsb']+self.channels[other]['lsb'])+1e-9
                        if np.max(np.abs(base-physical[other]*polarity)) > tolerance:
                            raise ValueError('重复/反向导联存在冲突，需核验原始数据。')
                    output.append(base)
                yield np.stack(output)
        after = self.path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('EDF 在任务过程中发生变化，请重新运行。')

    def segment(self, start_second, seconds, channels=8, max_points=400):
        """流式保留文件起点的因果状态，仅缓存请求区间；片段最多60秒。

        输出为微伏。展示点抽稀不用于重新分析；采样坐标与原文件一致。
        与特征管线采用相同的float32伏量化与滤波顺序。
        """
        from contextlib import closing
        values = (start_second, seconds, channels, max_points)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise ValueError('波形片段参数须为有限数字。')
        if not 0 <= start_second < self.duration or not 0 < seconds <= 60 or start_second + seconds > self.duration:
            raise ValueError('波形区间须在文件内，且长度不超过60秒。')
        if not 1 <= channels <= 18 or int(channels) != channels or not 50 <= max_points <= 2000 or int(max_points) != max_points:
            raise ValueError('波形导联数或显示点数超出范围。')
        channels, max_points = int(channels), int(max_points)
        fs = PROTOCOL.sampling_hz
        lo, hi = round(start_second * fs), round((start_second + seconds) * fs)
        if hi <= lo:
            raise ValueError('请求片段不足一个采样点。')
        before = self.path.stat()
        causal, cursor, pieces = CausalFilter(self.fs), 0, []
        with closing(self.blocks()) as blocks:
            for block in blocks:
                volts = (block * 1e-6).astype(np.float32).astype(np.float64)
                filtered = (causal.transform(volts) * 1e6).astype(np.float32)
                end = cursor + filtered.shape[1]
                if end > lo and cursor < hi:
                    pieces.append(filtered[:channels, max(0, lo-cursor):min(hi-cursor, filtered.shape[1])].copy())
                cursor = end
                if cursor >= hi:
                    break
        after = self.path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('EDF在波形读取期间改变。')
        clipped = np.concatenate(pieces, axis=1) if pieces else np.empty((channels, 0))
        if clipped.shape[1] != hi-lo:
            raise ValueError('波形区间不完整。')
        step = max(1, int(math.ceil(clipped.shape[1] / max_points)))
        clipped = clipped[:, ::step]
        return dict(start_second=lo/fs, seconds=(hi-lo)/fs, fs=int(fs),
                    display_sampling_hz=fs/step, decimation_note='展示抽稀，分析仍使用128Hz原采样',
                    channels=list(CHANNELS[:channels]),
                    t=(np.arange(clipped.shape[1])*step/fs).tolist(), samples=clipped.tolist())

class CausalFilter:
    """跨块保留 IIR 状态和降采样相位；不用双向滤波与未来样本。"""
    def __init__(self, native_fs):
        ratio = native_fs/PROTOCOL.sampling_hz
        if not math.isfinite(native_fs) or ratio < 1 or not math.isclose(ratio, round(ratio), abs_tol=1e-9):
            raise ValueError('采样率需为128Hz的正整数倍。')
        parts = [signal.butter(4,.5,btype='highpass',fs=native_fs,output='sos'),
                 signal.butter(4,45,btype='lowpass',fs=native_fs,output='sos'),
                 signal.tf2sos(*signal.iirnotch(60,30,fs=native_fs))]
        if native_fs > 128:
            order, cutoff = signal.ellipord(45,64,.2,60,fs=native_fs)
            parts.append(signal.ellip(order,.2,60,cutoff,fs=native_fs,output='sos'))
        self.sos = np.concatenate(parts)
        self.state = np.zeros((len(self.sos),18,2))
        self.factor, self.seen = round(ratio), 0

    def transform(self, x):
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[0] != 18 or not x.shape[1] or not np.isfinite(x).all():
            raise ValueError('信号须为有限的(18, samples)微伏矩阵。')
        filtered, self.state = signal.sosfilt(self.sos,x,axis=-1,zi=self.state)
        first = (-self.seen)%self.factor
        self.seen += x.shape[-1]
        return filtered[:,first::self.factor]

def extract_features(signal_uv, fs=128):
    x = np.asarray(signal_uv)
    if x.shape != (18,1280) or fs != 128 or not np.isfinite(x).all():
        raise ValueError('Welch 输入须为128Hz、10秒、18导联的有限微伏信号。')
    freq, psd = signal.welch(x,fs=fs,window='hann',nperseg=256,noverlap=128,
                             detrend='constant',scaling='density',axis=-1)
    columns = []
    for lo, hi in BANDS:
        grid = np.r_[lo,freq[(freq>lo)&(freq<hi)],hi]
        values = np.stack([np.interp(grid,freq,p) for p in psd])
        columns.append(np.log10(np.trapezoid(values,grid,axis=-1)+1e-12))
    return np.stack(columns,axis=-1).reshape(90).astype(np.float32)

def validate_events(events, duration):
    pairs = []
    for pair in events:
        if not isinstance(pair,(list,tuple)) or len(pair) != 2:
            raise ValueError('发作列表须为 [[起始秒,结束秒], ...]。')
        a,b = pair
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in pair):
            raise ValueError('发作时间须为有限数字。')
        if not 0 <= a < b <= duration:
            raise ValueError('发作时间超出文件或起止顺序错误。')
        pairs.append((float(a),float(b)))
    pairs.sort()
    if any(pairs[i][0] < pairs[i-1][1] for i in range(1,len(pairs))):
        raise ValueError('发作事件重叠，需核验标注。')
    return pairs

def label_window(end_seconds, duration, events, protocol=PROTOCOL):
    """返回(y,reason,event_index)。正负类共同要求完整35分钟未来。

    发作需有完整历史窗口且不与此前发作后期重叠，符合原 r1 事件资格。
    标签0仅代表录制条件的未来阴性，文件开始前病史仍未知。
    """
    t, start = end_seconds,end_seconds-protocol.window_seconds
    if not math.isfinite(t+duration) or t < protocol.warmup_seconds+protocol.window_seconds or t > duration:
        return -1,'invalid_window',None
    if any(start<b+protocol.postictal_seconds and a<t for a,b in events):
        return -1,'ictal_or_known_postictal',None
    if any(t<=a<t+protocol.sph_seconds for a,b in events):
        return -1,'within_sph',None
    if t+protocol.horizon > duration:
        return -1,'right_censored',None
    matched = [(i,a,b) for i,(a,b) in enumerate(events) if t+protocol.sph_seconds<=a<t+protocol.horizon]
    for i,a,b in matched:
        history = a-protocol.horizon-protocol.warmup_seconds
        if history>=0 and not any(c<a and d+protocol.postictal_seconds>history for c,d in events):
            return 1,'eligible_preictal',i
    if matched:
        return -1,'preictal_of_ineligible_event',None
    return 0,'observed_horizon_negative',None

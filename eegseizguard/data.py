"""非pickle特征包：强校验后才能训练，患者身份规范化后才能划分。"""
from dataclasses import dataclass
import io
import json
from pathlib import Path
import zipfile
import numpy as np
from .config import feature_contract
from .storage import atomic_bytes, input_file

def canonical_patient(value):
    if isinstance(value,(bytes,np.bytes_)):
        value=value.decode('utf-8',errors='strict')
    value = str(value).strip()
    if value.upper().startswith('CHB-MIT:'):
        value='CHB-MIT:'+value.split(':',1)[1].lower()
    elif value.lower().startswith('chb'):
        value=value.lower()
    if value in {'chb01','chb21','CHB-MIT:chb01','CHB-MIT:chb21','chb01+chb21'}:
        return 'CHB-MIT:chb01+chb21'
    if value.startswith('chb'):
        return 'CHB-MIT:'+value
    return value

@dataclass
class Dataset:
    X: np.ndarray
    y: np.ndarray
    patient: np.ndarray
    split: np.ndarray
    time_seconds: np.ndarray
    record: np.ndarray
    event: np.ndarray
    metadata: dict

    def validate(self):
        x = np.asarray(self.X)
        if x.ndim != 2 or x.shape[1] != 90 or not 0 < len(x) <= 500000:
            raise ValueError('X 必须是1至500000行、90列的特征矩阵。')
        if x.dtype.kind not in 'fi' or not np.isfinite(x).all():
            raise ValueError('特征含NaN、无穷或非数值；请核验数据，不能静默填补。')
        n = len(x)
        for name in ('y','patient','split','time_seconds','record','event'):
            if np.asarray(getattr(self,name)).shape != (n,):
                raise ValueError(f'{name} 长度必须与 X 行数相同。')
        if self.y.dtype.kind not in 'iu' or not np.isin(self.y,[-1,0,1]).all():
            raise ValueError('标签 y 须为整数 -1/0/1，-1 表示未知。')
        for name in ('patient','split','record','event'):
            if getattr(self,name).dtype.kind not in 'US':
                raise ValueError(f'{name} 必须是非对象字符串数组。')
            if getattr(self,name).dtype.kind=='S':
                setattr(self,name,np.array([v.decode('utf-8',errors='strict') for v in getattr(self,name)]))
        self.patient = np.array([canonical_patient(p) for p in self.patient])
        if any(not p or len(p)>128 for p in self.patient) or any(not r for r in self.record):
            raise ValueError('患者或记录标识为空/过长。')
        if not np.isin(self.split,['train','validation','test','inference']).all():
            raise ValueError('split 只能是 train/validation/test/inference。')
        if self.time_seconds.dtype.kind not in 'fi' or not np.isfinite(self.time_seconds).all() or (self.time_seconds<0).any():
            raise ValueError('time_seconds 必须为非负有限数字。')
        memberships = {}
        recording_patients = {}
        coordinates = set()
        for p,s,r,t in zip(self.patient,self.split,self.record,self.time_seconds):
            if p in memberships and memberships[p] != s:
                raise ValueError(f'患者泄漏：{p} 同时位于多个分区。')
            memberships[p] = s
            if r in recording_patients and recording_patients[r]!=p:
                raise ValueError('同一记录被赋给不同患者，可能产生数据泄漏。')
            recording_patients[r]=p
            key = (str(p),str(r),float(t))
            if key in coordinates:
                raise ValueError('存在重复的患者/记录/窗口时刻。')
            coordinates.add(key)
        if any(not str(e).strip() for e in self.event[self.y==1]):
            raise ValueError('阳性窗口必须关联真实事件标识。')
        if self.metadata.get('feature_contract') != feature_contract():
            raise ValueError('特征顺序或信号协议不匹配本软件版本。')
        return self

def load_dataset(path):
    path = input_file(path,'.npz')
    # 压缩包在解压前检查声明大小；禁止对象数组与 pickle。
    if path.stat().st_size > 600*1024*1024:
        raise ValueError('数据包超过600MB大小预算，请分批处理。')
    expected = {'X','y','patient','split','time_seconds','record','event','metadata'}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names)!=8 or set(names)!={n+'.npy' for n in expected}:
            raise ValueError('NPZ 内容字段不完整、重复或包含额外项。')
        if sum(i.file_size for i in archive.infolist())>600*1024*1024:
            raise ValueError('NPZ 解压后的大小超过600MB预算。')
    with np.load(path,allow_pickle=False) as archive:
        arrays = {key:archive[key] for key in expected}
    metadata = arrays.pop('metadata')
    if metadata.shape != () or metadata.dtype.kind not in 'US' or len(str(metadata.item()))>4*1024*1024:
        raise ValueError('metadata 须为标量 JSON 字符串。')
    parsed = json.loads(str(metadata.item()))
    if not isinstance(parsed,dict):
        raise ValueError('metadata 必须为JSON对象。')
    return Dataset(**arrays,metadata=parsed).validate()

def save_dataset(path,dataset):
    dataset.validate()
    buffer = io.BytesIO()
    np.savez_compressed(buffer,X=dataset.X,y=dataset.y,patient=dataset.patient,
                        split=dataset.split,time_seconds=dataset.time_seconds,
                        record=dataset.record,event=dataset.event,
                        metadata=np.array(json.dumps(dataset.metadata,ensure_ascii=False,allow_nan=False)))
    atomic_bytes(path,buffer.getvalue())

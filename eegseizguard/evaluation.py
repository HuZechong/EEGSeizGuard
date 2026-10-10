"""独立患者宏平均与合并窗指标；AP明确区别于梯形PR曲线面积。"""
import csv
import io
import math
import numpy as np
from sklearn.metrics import (average_precision_score, roc_auc_score, f1_score,
                             balanced_accuracy_score, confusion_matrix, roc_curve,
                             precision_recall_curve)
from .storage import atomic_bytes, input_file
from .data import canonical_patient

def one_metrics(y,p,threshold):
    two_classes = len(np.unique(y))==2
    predicted = (p>=threshold).astype(int)
    return dict(n=len(y),positives=int((y==1).sum()),prevalence=float(y.mean()),
                auroc=float(roc_auc_score(y,p)) if two_classes else None,
                ap=float(average_precision_score(y,p)) if two_classes else None,
                macro_f1=float(f1_score(y,predicted,labels=[0,1],average='macro',zero_division=0)),
                balanced_accuracy=float(balanced_accuracy_score(y,predicted)) if two_classes else None,
                confusion=confusion_matrix(y,predicted,labels=[0,1]).tolist(),
                support='both_classes' if two_classes else 'single_class')

def evaluate(y,probability,patient,threshold=.5):
    y,p,patient = np.asarray(y),np.asarray(probability,dtype=float),np.asarray(patient)
    if isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or not 0<=threshold<=1:
        raise ValueError('阈值必须为0到1的有限数字。')
    if y.ndim!=1 or not len(y) or p.shape!=y.shape or patient.shape!=y.shape:
        raise ValueError('标签、分数、患者标识须为同长度的一维数组。')
    if y.dtype.kind not in 'iu' or not np.isin(y,[-1,0,1]).all():
        raise ValueError('评估标签必须为整数 -1/0/1。')
    if not np.isfinite(p).all() or not ((p>=0)&(p<=1)).all():
        raise ValueError('预测分数含非有限数或超出[0,1]。')
    if patient.dtype.kind not in 'US' or any(not str(v).strip() for v in patient):
        raise ValueError('患者标识须为非空字符串。')
    patient = np.array([canonical_patient(v) for v in patient])
    known = y!=-1
    if not known.any():
        raise ValueError('全部标签未知，无法计算分类效果；仍可导出分数。')
    patients = []
    for identity in sorted(set(patient[known])):
        selected = known&(patient==identity)
        patients.append(dict(patient=str(identity),**one_metrics(y[selected],p[selected],threshold)))
    keys = ('auroc','ap','macro_f1','balanced_accuracy','prevalence')
    macro = {}
    support = {}
    for key in keys:
        values = [row[key] for row in patients if row[key] is not None]
        macro[key] = float(np.mean(values)) if values else None
        support[key] = len(values)
    return dict(macro=macro,pooled=one_metrics(y[known],p[known],threshold),patients=patients,
                macro_support=support,excluded_unknown=int((~known).sum()),threshold=threshold,
                definitions=dict(ap='average_precision_score；不是梯形PR面积',
                                 macro='逐患者等权；缺类AUROC/AP为空并报告支持数',
                                 score='未校准模型分数；不可解释为临床发作概率',
                                 scope='窗口分类评估；不推断事件敏感度或FAR/h'))

def curves(y,p):
    y,p=np.asarray(y),np.asarray(p)
    known=y!=-1
    if len(np.unique(y[known]))!=2:
        return {}
    fpr,tpr,_=roc_curve(y[known],p[known],drop_intermediate=True)
    precision,recall,_=precision_recall_curve(y[known],p[known])
    # 展示点降采样仅影响图形，指标由完整数据计算。
    def points(a,b):
        ids=np.unique(np.linspace(0,len(a)-1,min(300,len(a)),dtype=int))
        return [[float(a[i]),float(b[i])] for i in ids]
    return {'roc':points(fpr,tpr),'pr':points(recall,precision)}

def write_predictions(path,dataset,p,mask=None):
    selected=np.ones(len(p),dtype=bool) if mask is None else mask
    stream=io.StringIO(newline='')
    writer=csv.writer(stream)
    writer.writerow(['patient','record','time_seconds','y','probability'])
    for i in np.flatnonzero(selected):
        writer.writerow([dataset.patient[i],dataset.record[i],float(dataset.time_seconds[i]),int(dataset.y[i]),float(p[i])])
    atomic_bytes(path,stream.getvalue().encode('utf-8-sig'))

def read_predictions(path):
    path=input_file(path,'.csv')
    if path.stat().st_size>150*1024*1024:
        raise ValueError('CSV 超过150MB预算。')
    rows,coordinates=[],set()
    with path.open(encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        if reader.fieldnames!=['patient','record','time_seconds','y','probability']:
            raise ValueError('CSV字段或顺序错误，须使用本软件导出格式。')
        for row in reader:
            if len(rows)>=500000 or None in row or any(v is None for v in row.values()):
                raise ValueError('CSV行数或字段异常。')
            identity=canonical_patient(row['patient'])
            t=float(row['time_seconds'])
            key=(identity,row['record'],t)
            if not math.isfinite(t) or t<0 or not identity or not row['record'] or key in coordinates:
                raise ValueError('CSV含重复窗口或非法时间/标识。')
            coordinates.add(key)
            rows.append((identity,row['record'],t,int(row['y']),float(row['probability'])))
    if not rows:
        raise ValueError('CSV没有预测行。')
    return rows

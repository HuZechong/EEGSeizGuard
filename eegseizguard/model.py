"""仅训练分区拟合归一化；数值JSON模型部署，无pickle反序列化。"""
import warnings
import numpy as np
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.exceptions import ConvergenceWarning
from .config import feature_contract
from .data import canonical_patient
from .storage import check_cancel,read_json,write_json

def load_model(path):
    model=read_json(path)
    if not isinstance(model,dict) or model.get('schema_version')!=1 or model.get('feature_contract')!=feature_contract():
        raise ValueError('模型版本、通道顺序或特征协议不兼容。')
    for key in ('mean','scale','coef'):
        raw=model.get(key)
        if not isinstance(raw,list) or any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in raw):
            raise ValueError(f'模型 {key} 必须是数字数组，不接受字符串或布尔值。')
        arr=np.asarray(raw,dtype=float)
        if arr.shape!=(90,) or not np.isfinite(arr).all():
            raise ValueError(f'模型 {key} 须为90个有限数字。')
        if key=='scale' and not (arr>0).all():
            raise ValueError('模型归一化尺度必须全部大于0。')
    for key in ('intercept','threshold'):
        value=model.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not np.isfinite(value):
            raise ValueError(f'模型{key}不是有限数字。')
    if not 0<=model['threshold']<=1:
        raise ValueError('模型阈值越界。')
    for key in ('training_patients','validation_patients'):
        values=model.get(key)
        if not isinstance(values,list) or not all(isinstance(p,str) and p for p in values) or len(set(values))!=len(values):
            raise ValueError('模型患者来源元数据不完整。')
        model[key]=[canonical_patient(p) for p in values]
        if len(set(model[key]))!=len(values):
            raise ValueError('模型包含同一患者的重复别名。')
    if set(model['training_patients'])&set(model['validation_patients']):
        raise ValueError('模型训练与验证患者重叠。')
    return model

def predict(X,model):
    X=np.asarray(X,dtype=float)
    if X.ndim!=2 or X.shape[1]!=90 or not np.isfinite(X).all():
        raise ValueError('推理特征须为有限的(N,90)矩阵。')
    with np.errstate(over='raise',invalid='raise'):
        score=((X-np.asarray(model['mean']))/np.asarray(model['scale']))@np.asarray(model['coef'])+model['intercept']
    return expit(score)

def train(dataset,out,log,cancel):
    dataset.validate()
    subsets={s:(dataset.split==s)&(dataset.y!=-1) for s in ('train','validation','test')}
    for s in ('train','validation','test'):
        if not subsets[s].any():
            raise ValueError(f'{s} 分区没有可评价样本，请按患者分配并核验标注。')
    selected=subsets['train']
    if len(np.unique(dataset.y[selected]))!=2:
        raise ValueError('训练分区缺少一类，不能训练二分类模型。')
    check_cancel(cancel)
    # 固定种子、每患者每类最多200窗；事件轮流取样，避免长事件垄断预算。
    rng=np.random.default_rng(20260929)
    chosen=[]
    for identity in sorted(set(dataset.patient[selected])):
        for label in (0,1):
            indices=np.flatnonzero(selected&(dataset.patient==identity)&(dataset.y==label))
            groups={e:list(rng.permutation(indices[dataset.event[indices]==e])) for e in sorted(set(dataset.event[indices]))}
            count=0
            while any(groups.values()) and count<200:
                for bucket in groups.values():
                    if bucket and count<200:
                        chosen.append(bucket.pop()); count+=1
    ids=np.asarray(chosen,dtype=int)
    weights=np.zeros(len(ids))
    patients=dataset.patient[ids]
    labels=dataset.y[ids]
    events=dataset.event[ids]
    for identity in set(patients):
        classes=set(labels[patients==identity])
        for label in classes:
            here=(patients==identity)&(labels==label)
            names=set(events[here]) if label==1 else {''}
            for event in names:
                mask=here&(events==event) if label==1 else here
                weights[mask]=1/(len(set(patients))*len(classes)*len(names)*mask.sum())
    weights/=weights.mean()
    log(f'拟合训练患者归一化与逻辑回归：{len(set(patients))}名患者，{len(ids)}个窗。',30)
    scaler=StandardScaler().fit(dataset.X[ids],sample_weight=weights)
    classifier=LogisticRegression(C=1.0,max_iter=1000,solver='lbfgs',random_state=20260929)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always',ConvergenceWarning)
        classifier.fit(scaler.transform(dataset.X[ids]),labels,sample_weight=weights)
    check_cancel(cancel)
    if any(issubclass(w.category,ConvergenceWarning) for w in caught):
        raise RuntimeError('逻辑回归未收敛；任务失败，不能将该模型当作有效结果。')
    model=dict(schema_version=1,feature_contract=feature_contract(),mean=scaler.mean_.tolist(),
               scale=scaler.scale_.tolist(),coef=classifier.coef_[0].tolist(),intercept=float(classifier.intercept_[0]),
               threshold=.5,training_patients=sorted(set(patients)),
               validation_patients=sorted(set(dataset.patient[subsets['validation']])),
               score_semantics='uncalibrated',provenance=dict(kind=dataset.metadata.get('kind','user_data'),
               seed=20260929,training_windows=len(ids),threshold_policy='fixed_0.5_no_test_tuning'))
    write_json(out/'model.json',model)
    log('模型已冻结；使用固定0.5阈值评估独立测试患者。',70)
    return model

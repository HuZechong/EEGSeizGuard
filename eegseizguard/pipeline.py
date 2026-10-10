"""GUI与命令行共用的任务管线，所有产物位于独立任务目录。"""
from collections import Counter
import io
import json
from pathlib import Path
import platform
import numpy as np
import scipy
import sklearn
from .config import PROTOCOL,feature_contract
from .data import Dataset,load_dataset,save_dataset,canonical_patient
from .evaluation import evaluate,curves,read_predictions,write_predictions
from .model import train,predict,load_model
from .signal import EDFReader,CausalFilter,extract_features,validate_events,label_window
from .storage import (atomic_bytes,read_json,write_json,file_hash,input_file,check_cancel)
from .charts import make_charts

def waveform_preview(reader,record_id,identity,channels=8):
    """从 EDF 中间取 10 秒因果滤波波形用于界面预览，限制总点数避免任务记录膨胀。"""
    start = 30.0 if reader.duration >= 40 else max(0.0, reader.duration - 10)
    preview = reader.segment(start,10,channels,max_points=1280)
    preview['record']=record_id
    preview['patient']=identity
    return preview

def artifact(name,label):
    return dict(name=name,label=label)

def dataset_summary(dataset):
    return dict(windows=len(dataset.y),unknown=int((dataset.y==-1).sum()),
                patients=len(set(dataset.patient)),splits=dict(Counter(dataset.split.tolist())),
                kind=dataset.metadata.get('kind','user_data'))

def write_report(out,metrics,kind):
    lines=['头皮脑电癫痫发作预测软件 V1.0','结果类型：'+kind,
           '算法：Welch五频带log10功率 + 标准化 + 逻辑回归',
           '任务：录制条件下的发作前期二分类；SPH=300秒，SOP=1800秒。',
           '分数未经校准，仅供研究。窗级结果不代表事件预警效果。',
           '未知标签排除数：'+str(metrics['excluded_unknown']),
           '阈值：'+str(metrics['threshold']),'逐患者等权宏平均：']
    for key,value in metrics['macro'].items():
        rendered='无法计算' if value is None else f'{value:.6f}'
        lines.append(f'{key}: {rendered}；支持患者数 {metrics["macro_support"][key]}')
    lines+=['AP使用average precision定义，不等于梯形PR面积。',
            '缺类患者AUROC/AP不赋值，详见metrics.json。',
            '病史在文件开始前未知，阴性仅指完整未来预测时域内未见发作。']
    atomic_bytes(out/'report.txt','\n'.join(lines).encode('utf-8-sig'))

def prepare(manifest,out,log,cancel):
    path=input_file(manifest,'.json')
    config=read_json(path)
    if not isinstance(config,dict) or config.get('schema_version')!=1:
        raise ValueError('清单需要 schema_version=1。')
    records=config.get('records')
    if not isinstance(records,list) or not 1<=len(records)<=5000:
        raise ValueError('清单 records 须为1至5000条记录。')
    source_root=Path(config.get('source_root',path.parent)).resolve()
    features,labels,patients,splits,times,identities,event_ids=[],[],[],[],[],[],[]
    waveform_previews=[]
    excluded=Counter()
    sources=[]
    seen=set()
    assignments={}
    for index,record in enumerate(records):
        check_cancel(cancel)
        if not isinstance(record,dict) or record.get('annotations_complete') is not True:
            raise ValueError('每条记录必须明确 annotations_complete=true；未核验标注不能生成阴性。')
        source=(source_root/record.get('path','')).resolve()
        if not source.is_relative_to(source_root):
            raise ValueError('EDF路径超出 source_root。')
        source=input_file(source,'.edf')
        if source in seen:
            raise ValueError('同一EDF在清单中重复出现。')
        seen.add(source)
        identity=canonical_patient(record.get('patient',''))
        split=record.get('split')
        if not identity or split not in {'train','validation','test','inference'}:
            raise ValueError('清单患者标识为空或split非法。')
        if identity in assignments and assignments[identity]!=split:
            raise ValueError('同一患者不能跨分区，chb01与chb21亦视为同一患者。')
        assignments[identity]=split
        log(f'读取 {index+1}/{len(records)}：{source.name}',int(5+80*index/len(records)))
        reader=EDFReader(source)
        if 'seizures' not in record or not isinstance(record['seizures'],list):
            raise ValueError('清单必须显式填写 seizures 数组；空数组代表已核验无发作。')
        events=validate_events(record['seizures'],reader.duration)
        if reader.duration*128>128*86400:
            raise ValueError('单条记录超过24小时，请核验头信息并分段处理。')
        record_id=source.relative_to(source_root).as_posix()
        sources.append(dict(path=str(source),sha256=file_hash(source),duration=reader.duration,
                            record=record_id,patient=identity))
        if len(waveform_previews)<4:
            waveform_previews.append(waveform_preview(reader,record_id,identity))
        causal=CausalFilter(reader.fs)
        buffer=np.empty((18,0),dtype=np.float32)
        origin,total=0,0
        width=1280
        next_end=5120
        record_id=source.relative_to(source_root).as_posix()
        for block in reader.blocks():
            check_cancel(cancel)
            # 与原项目一致：导联先量化为float32伏，再因果滤波，缓存为float32微伏。
            volts=(block*1e-6).astype(np.float32).astype(np.float64)
            converted=(causal.transform(volts)*1e6).astype(np.float32)
            total+=converted.shape[1]
            buffer=np.concatenate((buffer,converted),axis=1)
            while next_end<=total:
                if len(features)>=500000:
                    raise ValueError('特征包超过500000窗口预算，请分批准备数据。')
                window=buffer[:,next_end-width-origin:next_end-origin]
                t=next_end/128
                y,reason,event=label_window(t,reader.duration,events)
                features.append(extract_features(window))
                labels.append(y); patients.append(identity); splits.append(split)
                times.append(t); identities.append(record_id)
                event_ids.append('' if event is None else f'{record_id}:event:{event}')
                if y==-1:
                    excluded[reason]+=1
                next_end+=width
            discard=max(0,min(buffer.shape[1],next_end-width-origin))
            buffer=buffer[:,discard:]
            origin+=discard
        if file_hash(source)!=sources[-1]['sha256']:
            raise ValueError('源EDF读取期间改变，任务失败。')
    if not features:
        raise ValueError('没有完整10秒窗口；文件需要超过40秒。')
    dataset=Dataset(np.asarray(features,dtype=np.float32),np.asarray(labels,dtype=np.int8),
                    np.array(patients),np.array(splits),np.array(times),np.array(identities),
                    np.array(event_ids),dict(feature_contract=feature_contract(),kind='user_edf',
                    sources=sources,manifest_sha256=file_hash(path),cross_file_join=False,
                    annotation_semantics='user_confirmed_complete_file_inventory')).validate()
    check_cancel(cancel)
    save_dataset(out/'dataset.npz',dataset)
    summary=dataset_summary(dataset)
    write_json(out/'data_audit.json',dict(summary=summary,excluded_reasons=dict(excluded),sources=sources,
               protocol=PROTOCOL.to_dict(),history_censored_note='每条记录开始前病史未知，禁止视作严格远离发作背景'))
    return dict(summary=f'已准备{summary["windows"]}个窗口；未知{summary["unknown"]}个。',
                data_summary=summary,waveform_previews=waveform_previews,
                artifacts=[artifact('dataset.npz','特征数据包'),artifact('data_audit.json','数据审计')],
                output_paths=dict(dataset=str(out/'dataset.npz')))

def demo_dataset(out,log,cancel):
    rng=np.random.default_rng(20260929)
    X,y,p,split,t,record,event=[],[],[],[],[],[],[]
    grid=np.arange(1280)/128
    for i in range(7):
        phase=rng.uniform(0,2*np.pi,(18,1))
        for j in range(24):
            check_cancel(cancel)
            label=int(j%4==0)
            signal=12*np.sin(2*np.pi*(7 if label else 11)*grid+phase)+rng.normal(0,5,(18,1280))
            X.append(extract_features(signal.astype(np.float32))); y.append(-1 if j==23 else label)
            p.append(f'DEMO:subject{i+1:02d}'); split.append('train' if i<3 else 'validation' if i<5 else 'test')
            t.append(40+j*10); record.append(f'synthetic{i+1:02d}'); event.append('demo_positive' if label else '')
        log(f'合成流程演示数据：{i+1}/7名虚拟对象',5+int(15*(i+1)/7))
    dataset=Dataset(np.array(X),np.array(y,dtype=np.int8),np.array(p),np.array(split),np.array(t),
                    np.array(record),np.array(event),dict(feature_contract=feature_contract(),kind='synthetic_demo',
                    note='合成频率差异仅验证软件流程，不是患者实验或预测性能证据'))
    save_dataset(out/'dataset.npz',dataset)
    return dataset

def training_result(dataset,out,log,cancel):
    model=train(dataset,out,log,cancel)
    p=predict(dataset.X,model)
    mask=dataset.split=='test'
    metrics=evaluate(dataset.y[mask],p[mask],dataset.patient[mask],model['threshold'])
    metrics['source_kind']=dataset.metadata.get('kind','user_data')
    metrics['curves']=curves(dataset.y[mask],p[mask])
    write_predictions(out/'predictions.csv',dataset,p,mask)
    write_json(out/'metrics.json',metrics)
    write_report(out,metrics,metrics['source_kind'])
    return dict(summary='训练完成；模型已冻结，独立测试指标按患者等权计算。',metrics=metrics,
                artifacts=[artifact('model.json','模型'),artifact('predictions.csv','独立测试预测'),
                           artifact('metrics.json','评估指标'),artifact('report.txt','效果报告')],
                output_paths=dict(model=str(out/'model.json'),predictions=str(out/'predictions.csv')))

def run_action(action,params,out,log,cancel):
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    file_keys={'prepare':['manifest'],'train':['dataset'],'predict':['dataset','model'],
               'evaluate':['predictions']}.get(action,[])
    input_files={key:dict(path=str(input_file(params.get(key))),sha256=file_hash(input_file(params.get(key))))
                 for key in file_keys}
    if action=='prepare':
        result=prepare(params.get('manifest'),out,log,cancel)
    elif action in {'train','demo'}:
        dataset=demo_dataset(out,log,cancel) if action=='demo' else load_dataset(params.get('dataset'))
        result=training_result(dataset,out,log,cancel)
        if action=='train':
            result['output_paths']['dataset']=str(input_file(params.get('dataset'),'.npz'))
        if action=='demo':
            result['summary']='合成数据演示完成：仅验证功能，指标不代表真实癫痫预测效果。'
            result['artifacts'].insert(0,artifact('dataset.npz','合成演示特征包'))
            result['output_paths']['dataset']=str(out/'dataset.npz')
    elif action=='predict':
        dataset=load_dataset(params.get('dataset'))
        model=load_model(params.get('model'))
        declared=set(model['training_patients'])|set(model['validation_patients'])
        if set(dataset.patient[dataset.split=='test'])&declared:
            raise ValueError('独立测试患者出现在模型训练或验证来源中，拒绝泄漏评估。')
        log('校验模型协议并生成全部窗口的未校准风险分数。',30)
        p=predict(dataset.X,model)
        write_predictions(out/'predictions.csv',dataset,p)
        metrics=None
        mask=dataset.split=='test'
        if ((dataset.y!=-1)&mask).any():
            metrics=evaluate(dataset.y[mask],p[mask],dataset.patient[mask],model['threshold'])
            metrics['source_kind']=dataset.metadata.get('kind','user_data')
            metrics['curves']=curves(dataset.y[mask],p[mask])
            write_json(out/'metrics.json',metrics); write_report(out,metrics,metrics['source_kind'])
        # 单条记录预览，不能连接不同文件的相对秒数。
        first=dataset.record[mask][0] if mask.any() else dataset.record[0]
        ids=np.flatnonzero(dataset.record==first)
        ids=ids[np.unique(np.linspace(0,len(ids)-1,min(200,len(ids)),dtype=int))]
        previews=[]
        sources=dataset.metadata.get('sources') or []
        first_patient=str(dataset.patient[ids[0]])
        matching=[s for s in sources if s.get('record')==str(first) and s.get('patient')==first_patient]
        # 旧数据包未记 record，只有唯一文件名匹配时才允许展示，避免混淆患者。
        if not matching:
            matching=[s for s in sources if Path(s.get('path','')).name==Path(str(first)).name]
        if len(matching)==1:
            try:
                source=matching[0]
                if file_hash(source['path'])!=source.get('sha256'):
                    raise ValueError('波形源文件已改变。')
                previews.append(waveform_preview(EDFReader(input_file(source['path'],'.edf')),
                                                 str(first),first_patient))
            except (ValueError,OSError):
                previews=[]
        result=dict(summary='全部窗口分数已导出；未知标签不参与分类评估。',metrics=metrics,
                    preview=[dict(patient=str(dataset.patient[i]),record=str(first),time_seconds=float(dataset.time_seconds[i]),
                                  y=int(dataset.y[i]),probability=float(p[i])) for i in ids],
                    waveform_previews=previews,
                    artifacts=[artifact('predictions.csv','窗口预测')],output_paths=dict(predictions=str(out/'predictions.csv'),
                    dataset=str(input_file(params['dataset'])),model=str(input_file(params['model']))))
        if metrics:
            result['artifacts'] += [artifact('metrics.json','独立测试指标'),artifact('report.txt','效果报告')]
    elif action=='evaluate':
        rows=read_predictions(params.get('predictions'))
        y=np.array([r[3] for r in rows]); p=np.array([r[4] for r in rows]); patients=np.array([r[0] for r in rows])
        metrics=evaluate(y,p,patients,params.get('threshold',.5))
        metrics['source_kind']='external_csv_descriptive'
        metrics['curves']=curves(y,p)
        write_json(out/'metrics.json',metrics); write_report(out,metrics,'CSV描述性分析，独立性由数据来源决定')
        result=dict(summary='CSV分析完成；来源独立性不能由CSV本身证明。',metrics=metrics,
                    artifacts=[artifact('metrics.json','评估指标'),artifact('report.txt','分析报告')])
    elif action=='history':
        from .history import import_history
        result=import_history(params.get('project'),out,log,cancel)
    elif action=='connectivity':
        from .connectivity import run_connectivity
        result=run_connectivity(params,out,log,cancel)
    else:
        raise ValueError('不支持的任务类型。')
    check_cancel(cancel)
    metrics=result.get('metrics')
    if metrics:
        warnings=[]
        chart_names=make_charts(metrics,out,warnings)
        if warnings:
            result.setdefault('warnings',[]).extend(warnings)
            for warning in warnings:
                log(warning)
        if chart_names:
            labels={'roc_curve.png':'ROC曲线图','pr_curve.png':'PR曲线图',
                    'patient_metrics.png':'患者指标对比图','confusion_matrix.png':'混淆矩阵图'}
            result['artifacts'] += [artifact(name,labels[name]) for name in chart_names]
    input_files.update(result.pop('input_files',{}))
    if any(file_hash(item['path'])!=item['sha256'] for item in input_files.values()):
        raise ValueError('输入文件在任务过程中改变；拒绝将结果标为完成。')
    write_json(out/'provenance.json',dict(action=action,params=params,protocol=PROTOCOL.to_dict(),
               feature_contract=feature_contract(),python=platform.python_version(),
               input_files=input_files,
               dependencies=dict(numpy=np.__version__,scipy=scipy.__version__,sklearn=sklearn.__version__),
               files={item['name']:file_hash(out/item['name']) for item in result.get('artifacts',[])}))
    result.setdefault('artifacts',[]).append(artifact('provenance.json','运行与文件指纹'))
    log('任务完成，产物已保存。',100)
    return result

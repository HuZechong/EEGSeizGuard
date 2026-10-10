"""matplotlib 图表生成：ROC/PR/患者对比/混淆矩阵，产物为 PNG 并纳入任务产物。

图表仅用于界面展示与结果归档，指标一律以 metrics.json 数值为准。
"""
import io

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'SimSun']
plt.rcParams['axes.unicode_minus'] = False

from .storage import atomic_bytes


def _save(fig, path):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=110, bbox_inches='tight')
    plt.close(fig)
    atomic_bytes(path, buf.getvalue())


def _pooled(metrics, key):
    value = (metrics.get('pooled') or {}).get(key)
    return None if value is None else float(value)


def roc_chart(metrics, path):
    points = (metrics.get('curves') or {}).get('roc')
    if not points:
        return None
    fig, ax = plt.subplots(figsize=(5.4, 4.4))
    x = np.asarray([p[0] for p in points]); y = np.asarray([p[1] for p in points])
    auc = _pooled(metrics, 'auroc')
    label = 'ROC' if auc is None else f'ROC (AUC = {auc:.4f})'
    ax.plot(x, y, color='#169d7d', lw=1.8, label=label)
    ax.plot([0, 1], [0, 1], ls='--', color='#b7c4c0', lw=1, label='随机基线')
    ax.set_xlim(0, 1); ax.set_ylim(0, 1.02)
    ax.set_xlabel('假阳性率 (FPR)'); ax.set_ylabel('真阳性率 (TPR)')
    ax.set_title('ROC 曲线（合并已知标签窗口）'); ax.legend(loc='lower right', fontsize=9)
    ax.grid(alpha=.25, lw=.5)
    _save(fig, path)
    return path.name


def pr_chart(metrics, path):
    points = (metrics.get('curves') or {}).get('pr')
    if not points:
        return None
    fig, ax = plt.subplots(figsize=(5.4, 4.4))
    recall = np.asarray([p[0] for p in points]); precision = np.asarray([p[1] for p in points])
    ap = _pooled(metrics, 'ap')
    baseline = float((metrics.get('pooled') or {}).get('prevalence') or 0)
    label = 'PR' if ap is None else f'PR (AP = {ap:.4f})'
    ax.plot(recall, precision, color='#2b6cb0', lw=1.8, label=label)
    ax.axhline(baseline, ls='--', color='#b7c4c0', lw=1, label=f'阳性率基线 ({baseline:.4f})')
    ax.set_xlim(0, 1); ax.set_ylim(0, 1.02)
    ax.set_xlabel('召回率 (Recall)'); ax.set_ylabel('精确率 (Precision)')
    ax.set_title('PR 曲线（合并已知标签窗口）'); ax.legend(loc='upper right', fontsize=9)
    ax.grid(alpha=.25, lw=.5)
    _save(fig, path)
    return path.name


def patient_chart(metrics, path):
    rows = metrics.get('patients')
    if not rows:
        return None
    usable = [r for r in rows if r.get('auroc') is not None or r.get('ap') is not None]
    if not usable:
        return None
    names = [str(r['patient']) for r in usable]
    auc = [float(r['auroc']) if r.get('auroc') is not None else np.nan for r in usable]
    ap = [float(r['ap']) if r.get('ap') is not None else np.nan for r in usable]
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 4.0))
    for ax, values, title, color in ((axes[0], auc, '各患者 AUROC', '#169d7d'),
                                     (axes[1], ap, '各患者 AP', '#2b6cb0')):
        ax.barh(names, values, color=color, alpha=.85)
        ax.set_xlim(0, 1)
        ax.set_xlabel(title)
        ax.grid(axis='x', alpha=.25, lw=.5)
        for i, v in enumerate(values):
            if not np.isnan(v):
                ax.text(min(v + .01, .98), i, f'{v:.4f}', ha='right' if v>.85 else 'left', va='center', fontsize=8)
    fig.suptitle('患者指标对比（宏平均为各患者等权）', y=1.02, fontsize=11)
    fig.tight_layout()
    _save(fig, path)
    return path.name


def confusion_chart(metrics, path):
    matrix = (metrics.get('pooled') or {}).get('confusion')
    if not matrix or len(matrix) != 2:
        return None
    m = np.asarray(matrix, dtype=int)
    fig, ax = plt.subplots(figsize=(4.4, 4.0))
    im = ax.imshow(m, cmap='Blues')
    labels = [['真阴性 TN', '假阳性 FP'], ['假阴性 FN', '真阳性 TP']]
    for i in range(2):
        for j in range(2):
            ax.text(j, i, f'{labels[i][j]}\n{m[i, j]}', ha='center', va='center',
                    fontsize=12, color='white' if m[i, j] > m.max() / 2 else '#264653')
    ax.set_xticks([0, 1]); ax.set_yticks([0, 1])
    ax.set_xticklabels(['预测阴性', '预测阳性'])
    ax.set_yticklabels(['真实阴性', '真实阳性'])
    ax.set_title('混淆矩阵（合并窗口）')
    fig.colorbar(im, ax=ax, fraction=.046)
    _save(fig, path)
    return path.name


def make_charts(metrics, out_dir, warnings=None):
    """为评估指标生成全部图表，返回生成的文件名列表；缺数据时跳过对应图。"""
    if not metrics:
        return []
    names = []
    for factory, filename in ((roc_chart, 'roc_curve.png'), (pr_chart, 'pr_curve.png'),
                              (patient_chart, 'patient_metrics.png'),
                              (confusion_chart, 'confusion_matrix.png')):
        try:
            name = factory(metrics, out_dir / filename)
            if name:
                names.append(name)
        except Exception as exc:
            plt.close('all')
            if warnings is not None:
                warnings.append(f'{filename} 绘制失败：{type(exc).__name__}: {exc}')
            continue
    return names

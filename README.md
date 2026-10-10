# EEGSeizGuard — 头皮脑电癫痫发作预测软件

研究版 V1.0 · 本机离线运行 · 单一算法可追溯输出

头皮脑电（scalp EEG）癫痫发作预测研究软件：基于 Welch 频带特征与逻辑回归的发作预测基线，提供数据准备、模型训练、风险预测、效果分析与三维脑网络可视化的一体化研究工作台。

> **研究用途声明**：本软件仅用于研究与基线能力验证，预测结果不用于临床决策。合成演示数据不代表真实预测效果。

## 功能

| 模块 | 说明 |
|---|---|
| 数据准备 | EDF 严格解析（经典 EDF）、连续因果 0.5–45Hz 带通滤波、SPH/SOP 发作预测标签、患者级隔离、未知标签保留 |
| 模型训练 | Welch 五频带（δ/θ/α/β/γ）90 维功率特征 + 逻辑回归；数值 JSON 模型，拒绝 pickle |
| 风险预测 | 逐窗口风险打分，前向无泄漏（纯因果预处理） |
| 效果分析 | AUROC / AP / F1 / 平衡准确率，患者级宏平均；ROC、PR、患者对比、混淆矩阵图表 PNG 导出 |
| 三维脑网络 | 18 双极导联 Welch 幅值平方相干性五频带矩阵，three.js 三维交互可视化（示意坐标，非个体解剖定位） |
| 安全与可追溯 | 本机回环 HTTP + Host/Origin/CSRF 校验、下载白名单、输入指纹（SHA256）、取消检查 |

## 快速开始

```bash
# 依赖（Python 3.12+）
pip install -r requirements.txt

# 首次使用先获取三维可视化依赖（Three.js，经 SHA-512 完整性校验从官方发行包生成）
python -X utf8 tools/vendor_three.py

# 启动（Windows 也可双击 启动软件.bat）
python -X utf8 main.py
# 浏览器打开 http://127.0.0.1:8765
```

`examples/manifest.example.json` 为真实 CHB-MIT 数据清单示例；无数据时可用"合成演示数据"体验完整流程。

## 测试

```bash
python -X utf8 -m unittest discover -s tests
```

62 项测试覆盖：因果滤波与分块状态、EDF 异常、标签边界及右删失、患者隔离、非有限值、类型校验、训练归一化泄漏、指标缺类与患者宏平均、任务取消、完整管线、本机 HTTP 访问与下载限制、相干性网络估计回归。

## 目录结构

```
main.py                  入口（本地服务）
eegseizguard/            核心模块（signal/data/model/evaluation/history/pipeline/server/connectivity/charts/storage）
web/                     前端工作台（index.html / app.js / style.css / brain-network.js）
web/vendor/              第三方依赖 Three.js（MIT，见 THIRD_PARTY_NOTICES.txt）
tests/                   单元测试
examples/                数据清单与历史模型示例
tools/                   打包与依赖获取工具
source_export/           软著源程序导出（源程序合并.txt / 清单.json）
```

## 算法口径

- 预处理：连续因果 0.5–45Hz 带通 + 60Hz 陷波 + 因果抗混叠降采样至 128Hz（前 30 秒预热）
- 特征：Welch 五频带功率（90 维，18 导联 × 5 频带）
- 预测：逻辑回归，风险分数未校准，仅用于研究排序
- 评估：患者级隔离；SPH=300s / SOP=1800s；宏平均 AUROC/AP
- 网络：Welch 幅值平方相干性（2s 周期 Hann 窗、50% 重叠），共享电极连接默认隐藏（可能人为增高相干性），阈值仅为显示用途、非显著性检验

## 第三方依赖

- NumPy / SciPy / scikit-learn / matplotlib：科学计算
- Three.js r186（MIT）：三维可视化，本地打包，见 `web/vendor/LICENSE-three.txt`

## 许可与材料

本项目为软件著作权登记（软著）而整理，著作权人与开发完成日期见申报材料。源码按"前 30 页 + 后 30 页"策略准备（总行数 >3000），完整源码见 `source_export/源程序合并.txt`。

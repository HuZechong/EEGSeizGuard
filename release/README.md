# V1.0 交付包

软件名称：头皮脑电癫痫发作预测软件（EEGSeizGuard）
版本：V1.0

## 文件

- `头皮脑电癫痫发作预测软件_V1.0_可视化增强.zip`
- 大小：1787180 字节
- SHA-256：`74f46ee27789cb81d0e0fcccf66731f36436b025204328588f328faff2de5832`
- 校验：Windows 执行 `certutil -hashfile <zip> SHA256`；Linux/macOS 执行 `sha256sum <zip>`

## 内容

由 `tools/package_release.py` 从本仓库源码生成，包含入口、核心模块、前端工作台、测试、示例、打包工具，以及 `source_export/` 源程序导出（18 个文件 / 3429 行）。不含原始 EEG 数据、模型参数、运行结果与第三方依赖库。

## 名称口径

界面标题、生成报告、导出文件名、源程序清单统一使用「头皮脑电癫痫发作预测软件」；`eegseizguard` 保留为 Python 包名与仓库标识，以维持导入兼容与既有链接。

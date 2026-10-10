"""验证图形标注的统计口径，避免以患者宏平均标注合并窗口曲线。"""
from pathlib import Path
import unittest
from unittest.mock import patch
from eegseizguard.charts import roc_chart, pr_chart

class ChartTests(unittest.TestCase):
    def test_roc_and_pr_legend_use_pooled_statistics(self):
        metrics = dict(macro=dict(auroc=.55, ap=.15),
                       pooled=dict(auroc=.73, ap=.42, prevalence=.2),
                       curves=dict(roc=[[0,0],[1,1]],pr=[[0,1],[1,.2]]))
        for factory, expected in [(roc_chart,'0.7300'),(pr_chart,'0.4200')]:
            with self.subTest(factory=factory.__name__), patch('eegseizguard.charts._save') as save:
                factory(metrics,Path('unused.png'))
                fig=save.call_args.args[0]
                try:
                    self.assertIn(expected,fig.axes[0].lines[0].get_label())
                    self.assertIn('合并',fig.axes[0].get_title())
                finally:
                    import matplotlib.pyplot as plt
                    plt.close(fig)

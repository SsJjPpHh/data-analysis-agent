import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "static" / "js" / "research_markdown.js"


def render_markdown(source: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser renderer contract test")
    script = (
        f"const renderer = require({json.dumps(str(RENDERER))});"
        f"process.stdout.write(renderer.render({json.dumps(source)}));"
    )
    completed = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_renderer_formats_standard_markdown_and_blocks_unsafe_html():
    rendered = render_markdown(
        """## 结论

**主要发现**与 `R` 输出。

变量名 baa_table_one 与 missingness_summary_2 必须保留下划线。

1. 第一项
2. 第二项

> 仅用于研究解释。

[安全链接](https://example.com) [危险链接](javascript:alert(1))

```r
summary(df)
```

<script>alert('xss')</script>
"""
    )

    assert "<h2>结论</h2>" in rendered
    assert "<strong>主要发现</strong>" in rendered
    assert "<code>R</code>" in rendered
    assert "baa_table_one" in rendered
    assert "missingness_summary_2" in rendered
    assert "<ol>" in rendered
    assert "<blockquote>" in rendered
    assert '<a href="https://example.com"' in rendered
    assert 'href="javascript:' not in rendered
    assert "<pre><code" in rendered
    assert "&lt;script&gt;" in rendered
    assert "<script>" not in rendered


def test_renderer_recovers_r_plain_text_sections_tables_and_lists():
    rendered = render_markdown(
        """数据集：当前上传数据（1941 行 × 22 列）
【变量字典】变量 | 推测标签 | R类型 | 缺失(n, %)
age | 年龄（岁） | numeric | 0 (0.0%)
gender | 性别 | numeric | 0 (0.0%)

【分类变量取值分布】
  gender: 2=1014 (52.2%); 1=927 (47.8%)
  edu: 2=1114 (57.4%); 1=365 (18.8%)

【下一步统计路线建议】
1. 基线描述
2. 关联分析
   - 二分类结局使用 logistic 回归
   - 连续结局使用线性回归
"""
    )

    assert "<h3>变量字典</h3>" in rendered
    assert '<table class="rsw-md-table">' in rendered
    assert "<th>变量</th>" in rendered
    assert "<td>age</td>" in rendered
    assert "<h3>分类变量取值分布</h3>" in rendered
    assert "gender: 2=1014" in rendered
    assert "<h3>下一步统计路线建议</h3>" in rendered
    assert "<ol>" in rendered
    assert "<ul>" in rendered
    assert "【变量字典】变量 |" not in rendered

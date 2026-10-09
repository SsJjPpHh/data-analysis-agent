# 测试目录

测试供开发与问题排查使用，不参与应用启动；普通用户安装 `requirements.txt` 即可，不需要安装开发工具或运行整套测试。

| 目录 | 用途 |
| --- | --- |
| `core/` | API、数据身份、Agent、方案、结果、恢复与模型外发。 |
| `ui/` | HTML/JavaScript 交互、Markdown、模板库与状态显示。 |
| `runtime/` | 当前 Python / Qt、本机 R、各平台路径、隔离、包安装和进程管理。 |
| `science/` | 固定方法、领域 workflow、独立数值参考和产物合同。 |

原先平铺的 `Test/` 已整理为本目录，旧目录不再保留。完全相同的重复测试及未使用的辅助函数已清除；目前仍验证现有实现的回归和科学参考用例保留。

## 按需运行

先安装开发依赖，再选择要检查的范围：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/core tests/ui -q
python -m pytest tests/runtime -q
python -m pytest tests/science -q
```

JavaScript 交互检查使用 Node.js；科学参考和部分运行时检查需要本机 R 及对应包。缺少条件时部分测试会跳过，应查看跳过原因；设置 `BAA_REQUIRE_SCIENCE_R=1` 可要求参考依赖必须可用。各平台原生用例仅在对应系统执行，不要求所有用户先运行平台验收。

`conftest.py` 将测试运行数据放在临时目录；`BAA_TEST_R_LIBS` 可选择测试 R 库。日志、截图、JUnit、科学运行记录等生成产物应写到源码目录之外。完整命令见 [开发与验证](../docs/development.md)。

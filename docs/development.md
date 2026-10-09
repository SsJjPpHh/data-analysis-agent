# 开发与验证

先按 [README](../README.md) 准备 Python、R 和运行依赖，再安装开发工具：

```bash
python -m pip install -r requirements-dev.txt
```

启动独立窗口使用 `python -m desktop`。开发前端/API 时可用 `python app.py` 打开回环服务；这不是公网部署入口。

## 仓库与本机目录

| 文件或目录 | 用途 |
| --- | --- |
| `.git/` | 本地版本历史；保留供开发使用。 |
| `.gitignore` | 排除密钥、运行数据、环境和生成文件。 |
| `.venv/` | 本机安装的 Python / Qt 依赖，可重新创建，不纳入 Git。不要逐个删除依赖内部的文件。 |
| `tests/`、`scripts/` | 验证源码和方法合同；普通启动不执行整套测试。 |
| `LICENSE`、依赖清单、`pyproject.toml` | 开源许可、依赖和开发工具配置。 |

Finder / 编辑器信息文件、lint 缓存和生成的测试结果可以清除。研究数据、模型配置、会话和产物写入源码仓库之外的运行目录。源码目录无需保存历史检查点、安装包或个人开发报告。

## 快速检查

```bash
python -m compileall api agent data desktop infrastructure LLM r_runtime scripts -q
python -m ruff check .
python scripts/audit_analysis_templates.py
python scripts/sync_science_dependencies.py --check
```

JavaScript 语法及交互测试需要 Node.js；它不是启动应用的依赖：

```bash
node --check static/js/research_workbench.js
node --check static/js/research_markdown.js
```

`tests/` 按核心、界面、运行环境和科学方法分类。运行选定模块进行有针对性的检查，例如：

```bash
python -m pytest tests/core/test_analysis_catalog.py tests/core/test_workflow_registry.py tests/core/test_model_sharing_settings.py -q
```

测试按当前行为保留，不按创建时间决定去留。重复用例和未使用的辅助函数已清理；仍覆盖当前实现的回归与科学参考用例保留。整理后的目录如下，详见 [测试说明](../tests/README.md)：

| 目录 | 范围 |
| --- | --- |
| `tests/core/` | 数据、API、Agent、冻结方案、结果、模型外发与恢复。 |
| `tests/ui/` | 页面合同、Markdown、模板交互、会话恢复与状态显示。 |
| `tests/runtime/` | Python / Qt、本机 R、平台路径、隔离、安装与取消。 |
| `tests/science/` | 方法实现、输入绑定、产物合同与独立数值参考。 |

测试不参与普通启动；无需为使用应用安装开发工具或运行测试。

## R 方法参考检查

完整测试会调用真实本机 R，创建合成数据和临时产物。默认模式下部分测试可以因缺少 R 或包而跳过；绿色结果需要同时查看 skip 数量与原因。

`scripts/science-reference-packages.txt` 是完整参考检查的依赖清单，不是普通用户的启动安装清单。先将所需包安装在测试用 R 库中。独立 R 检查器读取 R 标准环境变量 `R_LIBS_USER`；pytest 的 `tests/conftest.py` 会将 `BAA_TEST_R_LIBS` 传入 `R_LIBS_USER`。使用自定义包库时应让两者指向同一位置，多目录按操作系统路径分隔符分隔。不要依赖历史开发产物目录中的包。

在 macOS / Linux 上，依赖及完整检查命令如下。将路径替换为已创建的测试包库及仓库外输出目录；使用 R 已有默认包库时省略前两行：

```bash
export R_LIBS_USER="/absolute/path/to/test-r-library"
export BAA_TEST_R_LIBS="$R_LIBS_USER"
Rscript scripts/check_science_dependencies.R /absolute/path/outside-repo/science-runtime.txt
BAA_REQUIRE_SCIENCE_R=1 python -m pytest tests -q --tb=short
```

Windows 使用 PowerShell 设置环境变量。如需排查本机后端，可运行以下检查，再按已安装包选择方法用例：

```powershell
$env:BAA_REQUIRE_SCIENCE_R = "1"
python -m pytest tests/runtime/test_windows_r_isolation.py tests/runtime/test_windows_portability.py tests/runtime/test_research_r_smoke.py::test_local_rscript_research_smoke -q --tb=short
```

Windows 原生后端使用 AppContainer、限定目录 ACL 和 Job Object，相关系统调用检查只在 Windows 上运行；其他系统自动跳过。模拟平台的测试验证分支与策略。不同 R 包及外部工具对平台的支持各不相同，按本机实际分析需求安装。上述检查供开发与问题排查使用，不是开源发布或用户启动的前置要求。

依赖检查器将 R 及包版本写到指定文件；不传输出路径时默认生成根目录的 `science-runtime.txt`，该文件已由 Git 忽略。

`BAA_REQUIRE_SCIENCE_R=1` 使参考测试中缺失 R 或参考依赖成为失败。检查前仍需满足当前平台的 R 隔离要求。更改固定方法或参考测试后，运行 `python scripts/sync_science_dependencies.py` 更新依赖清单，并检查对应数值与失败案例。

## 本机检查与真实模型

项目不保留 GitHub Actions 配置；以上检查均由开发者在本机按需要执行。

自动测试主要使用模拟模型。真实 DeepSeek 验证应使用自己的 API Key、单独运行目录和合成数据，实际检查上传、工具调用、冻结方案、R 执行、失败处理和导出；不要把模拟模型测试或固定方法参考测试称作真实模型端到端验收。

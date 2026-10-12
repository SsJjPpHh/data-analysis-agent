# Windows 首次安装与本机 R 验收（v1.0）

本方案复用当前运行机制和已有测试，不调用模型，不需要 API Key，不安装 R 包。
自检通过只代表这台机器上的最小后端验收，不代表所有科学方法、桌面交互或模型端到端验收。
截至本补丁编写时，未在真实 Windows 10/11 上运行；发布说明中的这一限制仍然有效。

## 首次安装

在真实 Windows 10 或 11 的普通用户 PowerShell 中执行。安装 Python >=3.10、
R >=4.3，使用本地 NTFS 上的源码、R、包库和输出目录；先复制云盘占位文件和网络共享文件。
建议在含中文和空格的目录解压源码，例如用户目录下的“科研 工具”。

```powershell
cd "C:\Users\你的用户名\科研 工具\data-analysis-agent"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pytest==9.0.1
# R 通常可由 PATH、注册表或常见安装目录发现。必要时指定实际安装路径：
# $env:BAA_RSCRIPT_BIN = "C:\Program Files\R\R-4.5.1\bin\Rscript.exe"
# 为现有后端 smoke 用例准备三个包（单独执行，自检不会自动安装）：
# & $env:BAA_RSCRIPT_BIN --vanilla -e 'install.packages(c("jsonlite","ggplot2","readr"), repos="https://cloud.r-project.org")'
$report = Join-Path $env:USERPROFILE ("daa-windows-check-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
.\.venv\Scripts\python.exe scripts\windows_selfcheck.py --output $report
$LASTEXITCODE
Get-Content -Encoding UTF8 (Join-Path $report "report.txt")
```

直接使用虚拟环境 Python，无需激活环境或修改 PowerShell 执行策略。
如果使用自定义测试包库，将已存在的路径设置为 `BAA_TEST_R_LIBS`；脚本同步到
`R_LIBS_USER`，再使用应用的 R 发现、版本检查和包库准备机制。
该准备机制可能创建个人包库目录并做临时写入检查。
脚本创建独立的合成测试工作区，不读取原有会话或模型配置；测试结束清理工作区。
临时 ACL 和 AppContainer 的创建及回收由现有后端完成。

输出目录必须位于仓库之外且尚不存在，防止旧报告被误用。保留
`report.json`、`report.txt`、各组 `.xml` 和 `.log`，记录验收的源码 commit、
Windows 版本及架构；分别在 Windows 10、11 上运行后才能声称二者均已验证。

## 检查与判定

| 检查 | 执行方式与通过依据 |
| --- | --- |
| Python 与依赖 | 记录解释器、系统、架构，核对 requirements.txt 固定版本和 pytest 可用性。安装元数据检查不证明 Qt 窗口可用。 |
| R 发现与版本、包库 | HostREnvironment.locate/prepare；尊重 BAA_RSCRIPT_BIN，验证 >=4.3 和可写包库，记录实际 R、R_HOME、库路径。 |
| 专用临时目录 | private_r_temp_directory 创建、中文文件写入和清理；不另造临时目录策略。 |
| 平台模拟回归 | 现有 test_windows_portability.py 的 11 项；结果单独标记，不能代替原生验收。 |
| 原生统计、中文与边界 | 现有 test_native_* 四项：中文输入/输出路径、合成数据线性回归斜率=2、helper、CSV/PNG、中文 stdout、R 临时写入；禁止读取私有文件、改写输入、越界写入和本地网络连接。 |
| 隔离资源回收 | 同四项中的 AppContainer SID/ACL 回收、子进程继承、超时及取消后心跳停止；必须原生 Windows。 |
| 真实应用后端 | 先检查 jsonlite/ggplot2/readr 可加载，再复用 test_local_rscript_research_smoke：4 行合成数据，n=4、mean_age=48.75、outcome_rate=0.5，检查表格和 PNG/PDF/TIFF。 |

逐项输出 PASS / FAIL / SKIP；pytest 的 JUnit 结果用于保留每个用例的状态及失败详情。
指定组缺失用例、无有效报告、收集异常或非正常退出均不能通过。
退出码：**0** = 所有选定检查通过；**1** = 至少一个失败；**2** = 仅跳过或未完成。
在 macOS/Linux 上只输出原生验收 SKIP，退出 2，不运行伪 Windows 验收。
因前置环境失败而无法运行的后续检查明确记录 SKIP；FAIL 与 SKIP 共存时退出 1。

| 分类 | 如何处理 |
| --- | --- |
| environment | Python/R 版本、R 路径、可写包库、临时目录等前置条件失败；先修环境后重跑。 |
| dependency | Python 固定版本缺失/不匹配，或 smoke 所需 R 包不可加载；使用相同 Python/R 和包库安装后重跑。 |
| suspected_defect | 原生/回归测试断言失败；属于疑似缺陷，仍须核对日志、NTFS/ACL、杀毒软件和系统策略后定性。 |
| unclassified | 收集/导入错误、异常退出、报告缺失等；保留原始日志，不自动认定为程序缺陷。 |

临时目录失败时，可自行创建本地、可写、无空格目录（例如
`C:\Users\Public\DAATemp`，仅在权限合适时使用），设置
`$env:BAA_R_TEMP_ROOT = "实际绝对路径"` 后重跑。不要通过关闭隔离绕过失败。
AppContainer/ACL/Job Object 失败时，提供原生日志、R 路径、操作系统构建号和目录文件系统；
先检查本地 NTFS、权限、重解析点及终端安全策略。源码包编译失败再考虑对应 Rtools，
最小自检本身不要求安装全部科学依赖。

## 静态检查与未覆盖范围

可以跨平台检查脚本语法、报告解析、退出码、测试选择及发现机制的模拟分支；
不能静态证明真实 R 能启动、中文路径可用、Windows 权限隔离有效、进程树终止或 ACL 已回收。
报告逻辑回归可运行 `python tests/runtime/test_windows_selfcheck.py`（标准库 unittest）。

后端自检通过后，另行运行 `python -m desktop`，人工确认窗口、设置中的 R 状态、
合成 CSV 导入和结果查看；Qt/WebEngine 的真实显示不由依赖元数据证明。
全部 213 模板、外部工具、完整科学参考检查和真实模型端到端验证仍按开发文档单独执行。
报告可能包含本机用户名/路径；分享日志前审阅内容并去除个人信息和密钥。

# -*- coding: utf-8 -*-
"""Main-pipeline hardening tests against real R (2026-09-25).

Covers the upload→R-load and figure-rendering fixes:
  - GB18030-encoded Chinese CSVs load with intact text (SPSS/WPS/legacy
    Excel exports) instead of failing or mojibaking;
  - UTF-8 files keep the fast path with no encoding warning;
  - multi-sheet Excel workbooks load the first sheet and say so;
  - figures render through cairo devices so Chinese labels survive in
    PNG/PDF/TIFF companions.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.environment import HostREnvironment, REnvironmentError


def _local_runtime():
    environment = HostREnvironment.locate()
    try:
        verified = environment.prepare()
        return str(verified.rscript), environment.process_environment(verified=verified)
    except REnvironmentError:
        return None, {}


rscript_bin, runtime_env = _local_runtime()
rscript_required = pytest.mark.skipif(rscript_bin is None, reason="Local R is unavailable")

HELPER = Path("r_runtime/helpers/baa_helpers.R").resolve()


def _run_r(code: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    assert rscript_bin is not None
    # Write the script as a UTF-8 file: Chinese literals in `-e` arguments are
    # subject to the subprocess locale and can arrive as invalid UTF-8.
    script_path = tmp_path / "_hardening_test.R"
    script_path.write_text(code, encoding="utf-8")
    return subprocess.run(
        [rscript_bin, "--vanilla", str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
        env={**runtime_env, "BAA_OUTPUT_DIR": str(tmp_path / "out")},
    )


@rscript_required
def test_gb18030_chinese_csv_loads_with_intact_text(tmp_path):
    csv_path = tmp_path / "chinese.csv"
    rows = ["组别,年龄,结局", "对照组,52,0", "试验组,61,1"]
    csv_path.write_bytes("\r\n".join(rows).encode("gb18030"))

    code = f"""
source({json.dumps(str(HELPER))})
bundle <- baa_read_scientific_bundle(list(path={json.dumps(str(csv_path))}, format="csv"))
dataset <- bundle$dataset
stopifnot(is.data.frame(dataset))
stopifnot(identical(names(dataset), c("组别", "年龄", "结局")))
stopifnot(identical(as.character(dataset$组别[[2]]), "试验组"))
cat("WARNINGS:", paste(baa_artifacts$warnings, collapse = "|"))
"""
    completed = _run_r(code, tmp_path)
    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_utf8_csv_keeps_fast_path_without_encoding_warning(tmp_path):
    csv_path = tmp_path / "utf8.csv"
    csv_path.write_text("group,age\ncontrol,52\n", encoding="utf-8")

    code = f"""
source({json.dumps(str(HELPER))})
bundle <- baa_read_scientific_bundle(list(path={json.dumps(str(csv_path))}, format="csv"))
stopifnot(identical(names(bundle$dataset), c("group", "age")))
stopifnot(nrow(bundle$dataset) == 1)
stopifnot(length(baa_artifacts$warnings) == 0)
"""
    completed = _run_r(code, tmp_path)
    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_multisheet_excel_reads_first_sheet_and_warns(tmp_path):
    xlsx_path = tmp_path / "workbook.xlsx"
    code = f"""
source({json.dumps(str(HELPER))})
writexl::write_xlsx(list(主表 = data.frame(x = 1:2), 备用 = data.frame(y = 3:4)),
                    {json.dumps(str(xlsx_path))})
bundle <- baa_read_scientific_bundle(list(path={json.dumps(str(xlsx_path))}, format="xlsx"))
dataset <- bundle$dataset
stopifnot(identical(names(dataset), "x"))
cat("WARNINGS:", paste(baa_artifacts$warnings, collapse = "|"))
"""
    completed = _run_r(code, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "2 个工作表" in completed.stdout


@rscript_required
def test_chinese_labeled_figure_renders_all_three_formats(tmp_path):
    code = f"""
source({json.dumps(str(HELPER))})
p <- ggplot2::ggplot(data.frame(x = 1:3, y = c(2, 3, 5)), ggplot2::aes(x, y)) +
  ggplot2::geom_point() +
  ggplot2::labs(title = "生存曲线：试验组 vs 对照组", x = "随访时间（月）", y = "风险概率")
baa_plot("survival_zh", p)
stopifnot(file.exists(file.path(baa_output_dir, "survival_zh.png")))
stopifnot(file.exists(file.path(baa_output_dir, "survival_zh.pdf")))
stopifnot(file.exists(file.path(baa_output_dir, "survival_zh.tiff")))
stopifnot(length(baa_artifacts$figures) == 3)
"""
    completed = _run_r(code, tmp_path)
    assert completed.returncode == 0, completed.stderr
    # cairo renders the CJK labels; grDevices::pdf would emit conversion
    # failures instead of glyphs.
    assert "conversion failure" not in (completed.stderr or "")
    png_bytes = (tmp_path / "out" / "survival_zh.png").read_bytes()
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


@rscript_required
def test_haven_value_labels_become_factor_levels(tmp_path):
    code = f"""
source({json.dumps(str(HELPER))})
raw <- data.frame(性别 = c(1, 2, 2, 1), 年龄 = c(52, 61, 47, 55), 体重 = c(70, 62, 58, 75))
raw$性别 <- haven::labelled(raw$性别, labels = c("男" = 1, "女" = 2))
raw$体重 <- haven::labelled(raw$体重)
sav_path <- file.path(baa_output_dir, "labels.sav")
haven::write_sav(raw, sav_path)
bundle <- baa_read_scientific_bundle(list(path = sav_path, format = "sav"))
dataset <- bundle$dataset
stopifnot(is.factor(dataset$性别))
stopifnot(identical(levels(dataset$性别), c("男", "女")))
stopifnot(identical(as.character(dataset$性别[[1]]), "男"))
stopifnot(is.numeric(dataset$年龄))
stopifnot(is.numeric(dataset$体重))
"""
    completed = _run_r(code, tmp_path)
    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_gb18030_semicolon_csv_keeps_encoding_through_resniff(tmp_path):
    rows = ["组别;年龄", "对照组;52", "试验组;61"]
    csv_path = tmp_path / "semicolon.csv"
    csv_path.write_bytes("\r\n".join(rows).encode("gb18030"))

    code = f"""
source({json.dumps(str(HELPER))})
bundle <- baa_read_scientific_bundle(list(path={json.dumps(str(csv_path))}, format="csv"))
dataset <- bundle$dataset
stopifnot(identical(names(dataset), c("组别", "年龄")))
stopifnot(identical(as.character(dataset$组别[[2]]), "试验组"))
"""
    completed = _run_r(code, tmp_path)
    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_haven_partially_labelled_column_keeps_uncovered_values(tmp_path):
    code = f"""
source({json.dumps(str(HELPER))})
raw <- data.frame(性别 = c(1, 2, 9, 1))
raw$性别 <- haven::labelled(raw$性别, labels = c("男" = 1, "女" = 2))
sav_path <- file.path(baa_output_dir, "partial.sav")
haven::write_sav(raw, sav_path)
bundle <- baa_read_scientific_bundle(list(path = sav_path, format = "sav"))
dataset <- bundle$dataset
stopifnot(is.factor(dataset$性别))
stopifnot(identical(as.character(dataset$性别[[3]]), "9"))
stopifnot(!any(is.na(dataset$性别)))
"""
    completed = _run_r(code, tmp_path)
    assert completed.returncode == 0, completed.stderr

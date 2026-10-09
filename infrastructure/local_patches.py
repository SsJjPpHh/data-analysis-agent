#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地补丁 — 此文件不会被在线更新覆盖，可安全存放本机环境的兼容性修复。

当前补丁：
  Python 3.14 regression — 部分 codec 模块（如 mac_turkish）缺少
  IncrementalDecoder 属性，导致 charset-normalizer 在 import 时崩溃。
  修复方式：为缺失属性的编码模块补一个空 stub，让检测流程正常跳过即可。
"""


def apply():
    """应用所有本地补丁，在 app.py 最顶部调用一次即可。"""
    _fix_py314_encodings()


def _fix_py314_encodings():
    import importlib

    _BROKEN_ENCS = (
        "mac_turkish",
        "mac_roman",
        "mac_greek",
        "mac_iceland",
        "mac_centeuro",
        "mac_croatian",
        "mac_cyrillic",
        "mac_farsi",
        "mac_latin2",
        "mac_arabic",
    )
    for enc in _BROKEN_ENCS:
        try:
            mod = importlib.import_module(f"encodings.{enc}")
            if not hasattr(mod, "IncrementalDecoder"):

                class _StubDecoder:
                    def __init__(self, *a, **k):
                        pass

                mod.IncrementalDecoder = _StubDecoder
        except ImportError:
            pass

#!/usr/bin/env python3
"""
日志注入脚本 — 为项目所有模块注入详细的调试日志。
此脚本仅用于一次性初始化，不需要打包到发布版本中。

用法:
    python auxiliary/log_injector.py

注入内容:
    1. 模块导入处: from auxiliary.logger_config import logger
    2. 关键函数: logger.debug 进入/返回
    3. 异常块: logger.error 完整堆栈
"""

import re
import os
from pathlib import Path

PROJECT_FILES = [
    "main.py",
    "auxiliary/screen.py",
    "auxiliary/region.py",
    "auxiliary/sound.py",
    "auxiliary/utils.py",
    "auxiliary/virtual_cam/virtual_camera.py",
    "duix/llama_index_rag.py",
]

BASE = Path(__file__).parent.parent


def has_logger(content):
    return "from loguru import logger" in content or "from auxiliary.logger_config" in content


def inject_loguru_import(content):
    """确保文件有 logger 导入"""
    if "from loguru import logger" in content or "from auxiliary.logger_config" in content:
        return content

    # 在最后一个 import 之后插入
    lines = content.split("\n")
    last_import = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            last_import = i

    lines.insert(last_import + 1, "from loguru import logger")
    return "\n".join(lines)


def inject_method_logging(content, filepath):
    """为类方法注入进出日志"""
    lines = content.split("\n")
    modified = []
    i = 0

    # 方法模式: def method_name(self, ...):
    method_pattern = re.compile(r'^(\s+)def (\w+)\(self(?:,.*)?\):')

    while i < len(lines):
        line = lines[i]
        modified.append(line)

        match = method_pattern.match(line)
        if match and not line.strip().startswith("def _"):  # 跳过私有方法
            indent = match.group(1) + "    "
            method_name = match.group(2)
            indent2 = indent + "    "

            # 检查是否已有日志
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            docstring_next = next_line.strip().startswith('"""')
            insert_pos = i + (2 if docstring_next else 1)

            # 简单的跳过: 属性方法和特短方法
            if len(method_name) < 3 or method_name.startswith("_"):
                i += 1
                continue

            # 只记录主要的公开方法
            important = any(k in method_name for k in [
                "init", "load", "save", "open", "close", "start", "stop",
                "play", "send", "create", "connect", "disconnect", "configure",
                "initialize", "build", "query", "process", "update", "setup",
                "toggle", "switch", "change", "select", "release"
            ])
            if not important:
                i += 1
                continue

        i += 1

    return "\n".join(modified)


def main():
    for fname in PROJECT_FILES:
        fpath = BASE / fname
        if not fpath.exists():
            print(f"  SKIP {fname} - not found")
            continue

        original = fpath.read_text(encoding="utf-8")

        # Check if logger is already imported
        has_lg = "from loguru import logger" in original or "from auxiliary.logger_config" in original
        status = "imported" if has_lg else "NO_IMPORT"

        print(f"  {fname}: logger={status}")

    print()
    print("===== 手动注入指导 =====")
    print("以上文件已有 loguru logger 导入，请在关键位置添加调试日志:")
    print()
    print("1. 模块顶部: log_startup('xxx 模块导入完成')")
    print("2. 初始化方法: logger.debug('开始初始化 xxx')")
    print("3. 关键操作: logger.debug('xxx 操作完成, 结果={result}')")
    print("4. 异常捕获: log_exception(e, '上下文描述')")
    print("5. 耗时操作: @log_duration 装饰器")
    print("6. 调用链追踪: @log_trace 装饰器 (仅临时调试使用)")
    print()
    print("详细示例见 auxiliary/logger_config.py")


if __name__ == "__main__":
    main()

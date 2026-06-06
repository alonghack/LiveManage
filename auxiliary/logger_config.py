"""
统一日志配置模块

特性:
  - 彩色控制台输出 (INFO+ 实时查看)
  - 详细文件日志 (DEBUG 级别, 按大小轮转, 保留30天)
  - 错误日志单独存储 (ERROR+, 不轮转, 便于快速定位问题)
  - 性能计时装饰器 @log_duration
  - 函数进出追踪装饰器 @log_trace
  - 启动时间线标记
  - 结构化上下文 (模块名作为额外字段)

用法:
    from auxiliary.logger_config import logger, log_startup, log_duration, log_trace

    # 函数计时
    @log_duration
    def heavy_work(): ...

    # 函数进出追踪 (DEBUG 级别)
    @log_trace
    def important_method(): ...

    # 启动标记
    log_startup("UI 加载完成")
"""

import sys
import os
import time
import functools
from pathlib import Path
from loguru import logger

# ── 确保日志目录存在 ──
LOG_DIR = Path(__file__).parent.parent / "auxiliary" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── 移除默认 sink ──
logger.remove()

# ── Sink 1: 彩色控制台 (INFO+) ──
# 仅在非 Nuitka 打包环境下启用 (Nuitka 的 --windows-console-mode=disable 会隐藏控制台)
_IS_FROZEN = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")

if not _IS_FROZEN or os.environ.get("LIVEMANAGE_DEBUG"):
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        level="DEBUG",
        colorize=True,
        backtrace=True,
        diagnose=False,
    )

# ── Sink 2: 详细文件日志 (DEBUG, 按大小轮转) ──
logger.add(
    LOG_DIR / "debug.log",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {thread.name} | {message}",
    level="DEBUG",
    rotation="50 MB",
    retention="30 days",
    compression="zip",
    enqueue=True,
    backtrace=True,
    diagnose=True,
)

# ── Sink 3: 错误日志 (ERROR+, 单独文件, 便于快速排查) ──
logger.add(
    LOG_DIR / "error.log",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}",
    level="ERROR",
    rotation="10 MB",
    retention="60 days",
    enqueue=True,
    backtrace=True,
    diagnose=True,
)

# ── 启动时间线 ──
_startup_markers = []
_startup_start = time.perf_counter()


def log_startup(marker: str):
    """记录启动时间线标记 (DEBUG 级别)。"""
    elapsed = time.perf_counter() - _startup_start
    _startup_markers.append((elapsed, marker))
    logger.debug(f"[启动] +{elapsed:.3f}s {marker}")


def log_startup_summary():
    """打印启动时间线摘要。"""
    logger.info("===== 启动时间线 =====")
    for elapsed, marker in _startup_markers:
        logger.info(f"  +{elapsed:.3f}s  {marker}")
    logger.info(f"======================")


# ── 工具装饰器 ──

def log_duration(func=None, *, label: str = ""):
    """记录函数执行耗时。

    可以用作 @log_duration 或 @log_duration(label="加载模型")。
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            name = label or f"{fn.__qualname__}"
            start = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                elapsed = time.perf_counter() - start
                logger.debug(f"[计时] {name} 完成, 耗时 {elapsed:.3f}s")
                return result
            except Exception:
                elapsed = time.perf_counter() - start
                logger.error(f"[计时] {name} 失败, 子耗时 {elapsed:.3f}s")
                raise
        return wrapper
    if func is not None:
        return decorator(func)
    return decorator


def log_trace(func=None, *, level: str = "DEBUG"):
    """记录函数进出 (用于调试调用链)。

    可以用作 @log_trace 或 @log_trace(level="INFO")。
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # 截断过长参数
            args_repr = []
            for a in args[1:3] if len(args) > 1 else []:  # skip self
                s = repr(a)
                args_repr.append(s[:80] + "..." if len(s) > 80 else s)
            sig = ", ".join(args_repr)
            if kwargs:
                sig += f", **{tuple(kwargs.keys())}"

            msg = f"[调用] {fn.__qualname__}({sig})" if sig else f"[调用] {fn.__qualname__}()"
            logger.log(level, msg)

            start = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                elapsed = time.perf_counter() - start
                logger.log(level, f"[返回] {fn.__qualname__} -> {elapsed:.3f}s")
                return result
            except Exception:
                elapsed = time.perf_counter() - start
                logger.error(f"[异常] {fn.__qualname__} -> {elapsed:.3f}s 后异常退出")
                raise
        return wrapper
    if func is not None:
        return decorator(func)
    return decorator


def log_exception(exc: Exception, context: str = ""):
    """记录异常及完整堆栈。"""
    import traceback
    tb = traceback.format_exc()
    logger.opt(exception=True).error(f"{context}: {exc}\n{tb}")


# ── 模块级心跳日志 ──

def log_heartbeat(module: str):
    """记录模块导入完成 (DEBUG 级别)。
    在每个 *.py 顶部调用一次: log_heartbeat(__name__)
    """
    logger.debug(f"[模块] {module} 已导入")


def get_logger(name: str = None):
    """获取一个绑定了模块名的 logger (可选, 用于需要自定义 name 的场景)。"""
    if name:
        return logger.bind(name=name)
    return logger

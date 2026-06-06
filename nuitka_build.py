"""
Nuitka 构建脚本 — 将 LiveManage 打包为独立 Windows 可执行文件。

用法:
    python nuitka_build.py

输出:
    output/LiveManage/直播助理.exe
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

from loguru import logger

BASE_DIR = Path(__file__).parent.resolve()
VCAM_SRC = BASE_DIR / "virtual_cam" / "vcam_filter"
VCAM_DLL = VCAM_SRC / "vcam_filter.dll"

# GCC 搜索路径 (按优先级)
GCC_SEARCH_PATHS = [
    r"D:\CommonTools\MinGW\bin\gcc.exe",
    r"C:\MinGW\bin\gcc.exe",
    r"C:\MinGW-w64\mingw64\bin\gcc.exe",
    r"C:\msys64\mingw64\bin\gcc.exe",
    r"C:\msys64\ucrt64\bin\gcc.exe",
    r"C:\tools\mingw64\bin\gcc.exe",
]


def _find_gcc() -> str | None:
    """在 PATH 和常见位置查找 MinGW GCC。"""
    gcc = shutil.which("gcc")
    if gcc:
        return gcc
    for path in GCC_SEARCH_PATHS:
        if os.path.exists(path):
            return path
    return None


def compile_vcam_dll() -> bool:
    """编译 vcam_filter.dll (如果尚不存在)。"""
    if VCAM_DLL.exists():
        logger.info(f"vcam_filter.dll 已存在: {VCAM_DLL} ({VCAM_DLL.stat().st_size} bytes)")
        return True

    if not VCAM_SRC.exists():
        logger.error(f"vcam_filter 源码目录不存在: {VCAM_SRC}")
        return False

    gcc = _find_gcc()
    if not gcc:
        logger.error(
            "未找到 GCC 编译器，无法编译 vcam_filter.dll。\n"
            "请安装 MinGW-w64: https://www.mingw-w64.org/"
        )
        return False

    src_file = VCAM_SRC / "vcam_filter.c"
    cmd = [
        gcc, "-shared", "-O2", "-std=c17", "-m64",
        "-o", str(VCAM_DLL), str(src_file),
        "-lole32", "-loleaut32", "-luuid",
        "-lkernel32", "-luser32", "-lstrmiids",
        "-Wl,--kill-at",
    ]

    logger.info(f"编译 vcam_filter.dll: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"编译失败:\n{result.stderr[:800]}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("编译超时 (120s)")
        return False
    except Exception as e:
        logger.error(f"编译异常: {e}")
        return False

    logger.info(f"编译成功: {VCAM_DLL} ({VCAM_DLL.stat().st_size} bytes)")
    return True


def build_with_nuitka() -> int:
    """使用 Nuitka 执行独立构建。"""
    icon_path = BASE_DIR / "icon" / "0nbum-0m212-001.ico"

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--remove-output",
        "--output-dir=output",
        "--enable-plugin=pyqt6",
        "--windows-console-mode=disable",
        "--module-parameter=torch-disable-jit=yes",
        "--experimental=debug-report-traceback",
        f"--include-distribution-metadata=spacy",
        f"--include-distribution-metadata=torchaudio",
    ]

    if icon_path.exists():
        cmd.append(f"--windows-icon-from-ico={icon_path}")

    # 嵌入数据目录
    data_dirs = ["icon", "sqlite_db", "example", "auxiliary/logs", "auxiliary/models"]
    for dd in data_dirs:
        src = BASE_DIR / dd
        if src.exists():
            cmd.append(f"--include-data-dir={src}={dd}")

    # 嵌入 vcam_filter.dll
    if VCAM_DLL.exists():
        cmd.append(f"--include-data-files={VCAM_DLL}=virtual_cam/vcam_filter/vcam_filter.dll")

    cmd.append("main.py")

    logger.info(f"开始 Nuitka 构建...")
    logger.debug(f"命令: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, check=False, text=True)
        if result.returncode != 0:
            logger.error(f"Nuitka 构建失败 (退出码 {result.returncode})")
        else:
            logger.info("Nuitka 打包成功!")
        return result.returncode
    except Exception as e:
        logger.error(f"Nuitka 构建异常: {e}")
        return 1


def post_build_copy(output_dir: Path) -> Path | None:
    """构建后处理: 重命名可执行文件、复制 TTS 模型和 README。"""
    try:
        dist_dir = output_dir / "main.dist"
        if not dist_dir.exists():
            logger.warning(f"构建输出目录不存在: {dist_dir}")
            return None

        # 重命名 main.exe → 直播助理.exe
        exe_src = dist_dir / "main.exe"
        exe_dst = dist_dir / "直播助理.exe"
        if exe_src.exists():
            exe_src.rename(exe_dst)

        # 重命名输出目录为 LiveManage
        final_dir = output_dir / "LiveManage"
        if final_dir.exists():
            shutil.rmtree(final_dir)
        dist_dir.rename(final_dir)

        # 复制 TTS 模型
        tts_src = BASE_DIR / "models" / "SparkTTS"
        tts_dst = final_dir / "models" / "SparkTTS"
        if tts_src.exists():
            shutil.copytree(tts_src, tts_dst, dirs_exist_ok=True)

        # 复制 README
        readme_src = BASE_DIR / "README.md"
        if readme_src.exists():
            shutil.copy2(readme_src, final_dir / "README.md")

        result_exe = final_dir / "直播助理.exe"
        if result_exe.exists():
            return result_exe
        return None

    except Exception as e:
        logger.error(f"构建后处理失败: {e}")
        return None


def main() -> int:
    """主入口: 清理 → 编译 DLL → 构建 → 后处理。"""
    output_dir = BASE_DIR / "output"

    # 清理旧构建
    if output_dir.exists():
        shutil.rmtree(output_dir)

    # 编译 vcam_filter.dll
    if not compile_vcam_dll():
        logger.warning("vcam_filter.dll 编译失败，虚拟摄像头功能将不可用")

    # Nuitka 构建
    rc = build_with_nuitka()
    if rc != 0:
        logger.error("打包失败!")
        return 1

    # 后处理
    result = post_build_copy(output_dir)
    if result:
        logger.info(f"打包完成! 输出: {result}")
    else:
        logger.warning("打包完成但后处理可能不完整")

    return 0


if __name__ == "__main__":
    sys.exit(main())

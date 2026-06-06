"""
ManageCamera 虚拟摄像头模块 — 基于 obs-virtual-cam 队列协议

架构:
  Python 进程 → 共享内存队列 (circular buffer) → vcam_filter.dll (DirectShow) → 消费者 (OBS/浏览器/会议软件)

使用 obs-virtual-cam 的队列协议替代旧的 Seqlock 协议：
  [queue_header]  — 状态、格式、尺寸、write_index、队列长度
  [element 0]     — frame_header + RGB24 像素数据
  [element N-1]   — frame_header + RGB24 像素数据

不需要管理员权限，注册到 HKCU。
"""

import os
import sys
import ctypes
import struct
import subprocess
import atexit
from threading import Lock
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass

from loguru import logger

logger.debug("[模块] virtual_cam.virtual_camera 已导入")

# ── 常量 ──
SHARED_MEM_NAME = "ManageCameraVideo"

# 队列配置
QUEUE_LENGTH = 10          # 循环缓冲区帧数
MAX_WIDTH = 4096
MAX_HEIGHT = 3072

# 像素格式 (与 C 端 vcam_filter.h 中的 PixFmt 枚举保持一致)
PIXFMT_RGB24 = 0
PIXFMT_YUY2  = 1
PIXFMT_NV12  = 2
PIXFMT_RGB32 = 3
PIXFMT_I420  = 4

# 队列状态 (与 C 端一致)
OUTPUT_STOP   = 0
OUTPUT_START  = 1
OUTPUT_READY  = 2

# CLSID (与 C 头文件一致)
MANAGE_CAMERA_CLSID = "{5C2CD55C-92AD-4999-8666-912BD3E70020}"

# frame_header 结构体大小 (与 C 端一致)
#   uint64_t timestamp      (8 bytes)
#   uint32_t linesize[4]    (16 bytes)
#   int frame_width         (4 bytes)
#   int frame_height        (4 bytes)
FRAME_HEADER_SIZE = 32

# queue_header 结构体大小 (与 C 端一致)
#   int state                       (4)
#   int format                      (4)
#   int queue_length                (4)
#   int write_index                 (4)
#   int header_size                 (4)
#   int element_size                (4)
#   int element_header_size         (4)
#   int delay_frame                 (4)
#   int recommended_width           (4)
#   int recommended_height          (4)
#   int aspect_ratio_type           (4)
#   uint64_t last_ts                (8)
#   uint64_t frame_time             (8)
QUEUE_HEADER_SIZE = 60


@dataclass
class VirtualCameraInfo:
    """虚拟摄像头环境信息"""
    available: bool = False
    backend: Optional[str] = None
    device_name: Optional[str] = None
    error: Optional[str] = None


# ── DLL 路径 ──

def _get_dll_path() -> str:
    """获取 vcam_filter.dll 的路径"""
    dll_dir = Path(__file__).parent / "vcam_filter"
    dll_path = dll_dir / "vcam_filter.dll"
    if not dll_path.exists():
        # 后备: 检查旧版本
        for name in ("vcam_filter_v3.dll", "vcam_filter_v2.dll"):
            alt = dll_dir / name
            if alt.exists():
                return str(alt.resolve())
    return str(dll_path.resolve())


# ── GCC 查找 ──

def _find_gcc() -> Optional[str]:
    """在 PATH 和常见位置查找 MinGW GCC"""
    try:
        result = subprocess.run(
            ["gcc", "--version"],
            capture_output=True, timeout=5, check=False
        )
        if result.returncode == 0:
            return "gcc"
    except FileNotFoundError:
        pass

    common_paths = [
        r"D:\CommonTools\MinGW\bin\gcc.exe",
        r"C:\MinGW\bin\gcc.exe",
        r"C:\MinGW-w64\mingw64\bin\gcc.exe",
        r"C:\msys64\mingw64\bin\gcc.exe",
        r"C:\msys64\ucrt64\bin\gcc.exe",
        r"C:\tools\mingw64\bin\gcc.exe",
    ]
    for path in common_paths:
        if os.path.exists(path):
            return path

    return None


def _compile_dll() -> Tuple[bool, str]:
    """使用 MinGW GCC 编译 vcam_filter.dll"""
    gcc = _find_gcc()
    if not gcc:
        return False, "未找到 GCC 编译器"

    src_dir = Path(__file__).parent / "vcam_filter"
    src_file = src_dir / "vcam_filter.c"
    if not src_file.exists():
        return False, f"源文件不存在: {src_file}"

    out_file = src_dir / "vcam_filter.dll"

    # 构建 include 路径
    include_dirs = []
    for inc in [
        r"D:\CommonTools\MinGW\x86_64-w64-mingw32\include",
        r"C:\MinGW\include",
        r"C:\MinGW-w64\mingw64\x86_64-w64-mingw32\include",
        r"C:\msys64\mingw64\x86_64-w64-mingw32\include",
    ]:
        if os.path.isdir(inc):
            include_dirs.append(f"-I{inc}")

    cmd = [
        gcc, "-shared", "-O2", "-std=c17", "-m64",
        *include_dirs,
        "-o", str(out_file),
        str(src_file),
        "-lole32", "-loleaut32", "-luuid",
        "-lkernel32", "-luser32", "-lstrmiids",
        "-Wl,--kill-at"
    ]

    logger.info(f"编译 vcam_filter.dll: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=60
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            return False, f"编译失败:\n{stderr[:500]}"

        if not out_file.exists():
            return False, "编译完成后未找到输出文件"

        logger.info(f"编译成功: {out_file} ({out_file.stat().st_size} bytes)")
        return True, str(out_file)

    except subprocess.TimeoutExpired:
        return False, "编译超时 (60s)"
    except Exception as e:
        return False, f"编译异常: {e}"


# ── COM 注册 ──

def _register_dll(dll_path: str) -> Tuple[bool, str]:
    r"""加载 DLL 调用 DllRegisterServer 完成 COM 自注册。

    DLL 内部已处理 HKLM + HKCU 两套注册:
      - CLSID\{...}                      (FriendlyName + InprocServer32)
      - VideoInputDevice\Instance\{...}  (CLSID + FriendlyName + Merit)
      - Legacy AM Filter\Instance\{...}  (CLSID + FriendlyName)
      - KSCATEGORY_CAPTURE\Instance\{...} (CLSID + FriendlyName)

    Python 侧不再重复写入注册表，避免与 C 端不一致。
    """
    try:
        dll = ctypes.WinDLL(dll_path)
        dll.DllRegisterServer.argtypes = []
        dll.DllRegisterServer.restype = ctypes.HRESULT
        hr = dll.DllRegisterServer()
        if hr == 0:
            # 验证关键注册表项
            verify_ok, verify_msg = _verify_registration()
            return True, f"DllRegisterServer 成功 | 验证: {verify_msg}"
        else:
            return False, f"DllRegisterServer 返回 0x{hr:08X}"
    except Exception as e:
        return False, f"DLL 自注册失败: {e}"


def _verify_registration() -> Tuple[bool, str]:
    """验证 ManageCamera 在注册表中的关键项存在且正确。"""
    import winreg

    cls_str = "{5C2CD55C-92AD-4999-8666-912BD3E70020}"
    checks = {
        "HKCU CLSID Default": (
            winreg.HKEY_CURRENT_USER,
            f"Software\\Classes\\CLSID\\{cls_str}",
            "",
            winreg.REG_SZ,
        ),
        "HKCU InprocServer32": (
            winreg.HKEY_CURRENT_USER,
            f"Software\\Classes\\CLSID\\{cls_str}\\InprocServer32",
            "",
            winreg.REG_SZ,
        ),
        "HKCU VideoInputDevice": (
            winreg.HKEY_CURRENT_USER,
            f"Software\\Classes\\CLSID\\{{860BB310-5D01-11D0-BD3B-00A0C911CE86}}\\Instance\\{cls_str}",
            "CLSID",
            winreg.REG_SZ,
        ),
        "HKCU VideoInputDevice Merit": (
            winreg.HKEY_CURRENT_USER,
            f"Software\\Classes\\CLSID\\{{860BB310-5D01-11D0-BD3B-00A0C911CE86}}\\Instance\\{cls_str}",
            "Merit",
            winreg.REG_DWORD,
        ),
        "HKCU KSCATEGORY_CAPTURE": (
            winreg.HKEY_CURRENT_USER,
            f"Software\\Classes\\CLSID\\{{65E8773D-8F56-11D0-A3B9-00A0C9223196}}\\Instance\\{cls_str}",
            "CLSID",
            winreg.REG_SZ,
        ),
    }

    ok_count = 0
    missing = []
    for label, (root, path, value_name, reg_type) in checks.items():
        try:
            key = winreg.OpenKey(root, path)
            val, actual_type = winreg.QueryValueEx(key, value_name)
            winreg.CloseKey(key)
            if actual_type == reg_type:
                ok_count += 1
            else:
                missing.append(f"{label}: 类型不匹配 (期望 {reg_type}, 实际 {actual_type})")
        except FileNotFoundError:
            missing.append(label)
        except Exception as e:
            missing.append(f"{label}: {e}")

    if not missing:
        return True, f"全部 {ok_count} 项通过"
    else:
        logger.warning(f"注册验证缺失项: {missing}")
        return False, f"缺失 {len(missing)}/{len(checks)} 项: {', '.join(missing[:3])}"


def _unregister_dll(dll_path: str):
    """调用 DLL 的 DllUnregisterServer"""
    try:
        dll = ctypes.WinDLL(dll_path)
        dll.DllUnregisterServer.argtypes = []
        dll.DllUnregisterServer.restype = ctypes.HRESULT
        dll.DllUnregisterServer()
    except Exception as e:
        logger.debug(f"注销 DLL 失败: {e}")


# ── 共享内存队列写入器 ──

class SharedFrameQueue:
    """
    基于 obs-virtual-cam 队列协议的共享内存写入器。

    共享内存布局:
      [queue_header]  60 bytes — 队列元数据
      [element 0]     FRAME_HEADER_SIZE + frame_bytes  — 第一帧
      [element 1]     FRAME_HEADER_SIZE + frame_bytes  — 第二帧
      ...
      [element N-1]   FRAME_HEADER_SIZE + frame_bytes  — 第 N 帧
    """

    # 类级别的 kernel32 函数原型缓存
    _k32_initialized = False

    @classmethod
    def _init_kernel32(cls):
        """设置 kernel32 函数签名（64位安全）"""
        if cls._k32_initialized:
            return
        k32 = ctypes.windll.kernel32

        k32.OpenFileMappingW.restype = ctypes.c_void_p
        k32.OpenFileMappingW.argtypes = [
            ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p
        ]

        k32.CreateFileMappingW.restype = ctypes.c_void_p
        k32.CreateFileMappingW.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_wchar_p
        ]

        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_size_t
        ]

        k32.UnmapViewOfFile.restype = ctypes.c_bool
        k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]

        k32.CloseHandle.restype = ctypes.c_bool
        k32.CloseHandle.argtypes = [ctypes.c_void_p]

        cls._k32_initialized = True

    def __init__(self):
        self._handle = None
        self._ptr = None
        self._size = 0
        self._lock = Lock()
        self._write_index = 0
        self._initialized = False
        SharedFrameQueue._init_kernel32()

    def create(self, width: int, height: int, fps: int = 30) -> bool:
        """创建或重新初始化共享内存队列。

        重要: 共享内存始终以 MAX_WIDTH×MAX_HEIGHT 分配，确保后续分辨率
        切换 (如 1920×1080 → 2560×1440) 不会因缓冲区不足而越界写入。
        DLL 端同样使用此最大尺寸创建。"""
        try:
            FILE_MAP_ALL_ACCESS = 0xF001F
            PAGE_READWRITE = 0x04

            # 始终使用最大尺寸计算，与 DLL 端一致
            max_frame_bytes = MAX_WIDTH * MAX_HEIGHT * 3  # RGB24
            max_element_size = FRAME_HEADER_SIZE + max_frame_bytes
            total_size = QUEUE_HEADER_SIZE + QUEUE_LENGTH * max_element_size

            k32 = ctypes.windll.kernel32

            # Try OPENING first: the C DLL may have already created a maximum-size
            # shared memory (360MB for MAX_WIDTH×MAX_HEIGHT). If we destroy and
            # recreate at a smaller size while the DLL still has it mapped, we get
            # a stale handle and MapViewOfFile fails.
            self._handle = k32.OpenFileMappingW(
                FILE_MAP_ALL_ACCESS, False, SHARED_MEM_NAME
            )

            if not self._handle:
                # First-time creation: DLL hasn't loaded yet, we're the creator.
                self._handle = k32.CreateFileMappingW(
                    ctypes.c_void_p(-1), None, PAGE_READWRITE,
                    0, total_size, SHARED_MEM_NAME
                )

            if not self._handle:
                logger.error("创建/打开共享内存失败")
                return False

            # Map the ENTIRE file mapping object (0 = full size).
            # When DLL pre-creates at MAX_WIDTH×MAX_HEIGHT (~360MB), Python's
            # total_size for 1920x1080 (~59MB) would fail with "not enough quota".
            self._ptr = k32.MapViewOfFile(
                self._handle, FILE_MAP_ALL_ACCESS, 0, 0, 0
            )

            if not self._ptr:
                logger.error("映射共享内存失败")
                self.close()
                return False

            self._size = total_size

            # 初始化 queue_header
            frame_time_100ns = int(10000000 / fps) * 100  # 100ns 单位

            # 当前分辨率的实际帧大小
            actual_frame_bytes = width * height * 3

            header_data = struct.pack(
                "<i"   # state = OUTPUT_START
                "i"    # format = PIXFMT_RGB24
                "i"    # queue_length
                "i"    # write_index = 0
                "i"    # header_size = QUEUE_HEADER_SIZE
                "i"    # element_size (max)
                "i"    # element_header_size = FRAME_HEADER_SIZE
                "i"    # delay_frame = 5
                "i"    # recommended_width
                "i"    # recommended_height
                "i"    # aspect_ratio_type = 1 (keep ratio)
                "q"    # last_ts = 0
                "q",   # frame_time
                OUTPUT_START,
                PIXFMT_RGB24,
                QUEUE_LENGTH,
                0,
                QUEUE_HEADER_SIZE,
                max_element_size,
                FRAME_HEADER_SIZE,
                5,
                width,
                height,
                1,
                0,
                frame_time_100ns,
            )

            ctypes.memmove(ctypes.c_void_p(self._ptr), header_data, QUEUE_HEADER_SIZE)
            self._write_index = 0
            self._initialized = True

            logger.info(f"共享内存队列就绪: {width}x{height}@{fps} "
                        f"(名称={SHARED_MEM_NAME}, 队列长度={QUEUE_LENGTH}, "
                        f"总大小={total_size / 1024 / 1024:.1f}MB)")
            return True

        except Exception as e:
            logger.error(f"创建共享内存队列异常: {e}")
            return False

    def write_frame(self, frame_data: bytes, width: int, height: int) -> bool:
        """将 RGB24 帧写入队列

        Args:
            frame_data: RGB24 像素数据
            width: 帧宽度
            height: 帧高度
        """
        with self._lock:
            if not self._ptr or not self._initialized:
                return False

            expected = width * height * 3
            if len(frame_data) != expected:
                logger.warning(f"帧数据大小不匹配: {len(frame_data)} != {expected}")
                return False

            # 读取 queue_header 获取参数
            buf = ctypes.c_void_p(self._ptr)
            header_bytes = ctypes.string_at(self._ptr, QUEUE_HEADER_SIZE)

            state, fmt, qlen, write_idx, hdr_size, elem_size, elem_hdr_size, \
                delay, rec_w, rec_h, aspect, last_ts, frame_time = \
                struct.unpack("<iiiiiiiiiiiqq", header_bytes)

            element_size = elem_size
            element_header_size = elem_hdr_size
            queue_length = qlen

            # 检查 queue_header 是否被正确初始化
            if queue_length <= 0 or element_size <= 0:
                logger.warning("共享内存未正确初始化，重新初始化")
                self.create(width, height, 30)
                return False

            # 写入位置
            offset = hdr_size + element_size * self._write_index

            # 构建 frame_header
            import time
            ts = int(time.time() * 1_000_000)  # 微秒时间戳

            frame_hdr = struct.pack(
                "<Q"          # timestamp
                "IIII"        # linesize[4]
                "i"           # frame_width
                "i",          # frame_height
                ts,
                width * 3, 0, 0, 0,  # linesize[0] = row stride for RGB24
                width,
                height,
            )

            # 写入 frame_header + 像素数据
            target = ctypes.c_void_p(self._ptr + offset)
            ctypes.memmove(target, frame_hdr, element_header_size)
            pixel_target = ctypes.c_void_p(self._ptr + offset + element_header_size)
            ctypes.memmove(pixel_target, frame_data, expected)

            # 更新 write_index
            self._write_index = (self._write_index + 1) % queue_length

            # 更新 queue_header
            new_state = OUTPUT_READY if self._write_index == 0 else state
            new_header = struct.pack(
                "<iiiiiiiiiiiqq",
                new_state,
                fmt,
                queue_length,
                self._write_index,
                hdr_size,
                elem_size,
                elem_hdr_size,
                delay,
                rec_w,
                rec_h,
                aspect,
                ts,
                frame_time,
            )
            ctypes.memmove(buf, new_header, QUEUE_HEADER_SIZE)

            return True

    def close(self):
        """关闭共享内存 (设置状态为停止)"""
        with self._lock:
            k32 = ctypes.windll.kernel32
            if self._ptr and self._initialized:
                try:
                    # 设置状态为 OutputStop，通知 C 读取端停止
                    stop_header = struct.pack(
                        "<iiiiiiiiiiiqq",
                        OUTPUT_STOP,
                        PIXFMT_RGB24,
                        QUEUE_LENGTH,
                        self._write_index,
                        QUEUE_HEADER_SIZE,
                        0,
                        FRAME_HEADER_SIZE,
                        5,
                        1920,
                        1080,
                        1,
                        0,
                        0,
                    )
                    ctypes.memmove(self._ptr, stop_header, QUEUE_HEADER_SIZE)
                except Exception:
                    pass

            if self._ptr:
                k32.UnmapViewOfFile(self._ptr)
                self._ptr = None
            if self._handle:
                k32.CloseHandle(self._handle)
                self._handle = None
            self._initialized = False

    def __del__(self):
        self.close()


# ── Native 后端 ──

class NativeVCamBackend:
    """ManageCamera DirectShow 虚拟摄像头后端。

    使用纯 C 的 vcam_filter.dll，通过 obs-virtual-cam 队列协议共享内存传递帧。
    无需管理员权限，注册到 HKCU\\Software\\Classes。
    """

    def __init__(self):
        self._queue = None
        self._dll_path = None
        self._compiled = False
        self.width = 0
        self.height = 0
        self.fps = 0
        self.device_name = "ManageCamera"

    @staticmethod
    def is_available() -> Tuple[bool, str]:
        """检查原生后端是否可用"""
        dll_path = _get_dll_path()
        if os.path.exists(dll_path):
            return True, f"vcam_filter.dll 已就绪"
        gcc = _find_gcc()
        if gcc:
            return True, f"可用 GCC ({gcc}) 编译"
        return False, "未找到 vcam_filter.dll 且 GCC 不可用"

    def open(self, width: int, height: int, fps: int = 30) -> bool:
        """打开虚拟摄像头

        1. 编译 DLL（如果需要）
        2. 注册 COM 组件 (DllRegisterServer)
        3. 创建共享内存队列
        """
        logger.debug(f"NativeVCamBackend.open(width={width}, height={height}, fps={fps})")
        try:
            self.width = width
            self.height = height
            self.fps = fps

            # 1. 获取/编译 DLL
            dll_path = _get_dll_path()
            logger.debug(f"DLL 路径: {dll_path}, 存在={os.path.exists(dll_path)}")
            if not os.path.exists(dll_path):
                ok, msg = _compile_dll()
                if not ok:
                    raise RuntimeError(f"编译 vcam_filter.dll 失败: {msg}")

            self._dll_path = dll_path

            # 2. 注册 DLL（仅在注册表缺失时重新注册）
            verify_ok, _ = _verify_registration()
            if not verify_ok:
                logger.debug(f"注册 DLL: {dll_path}")
                ok, msg = _register_dll(dll_path)
                if not ok:
                    raise RuntimeError(f"注册 DLL 失败: {msg}")
            else:
                logger.debug(f"DLL 已注册，跳过 DllRegisterServer")

            # 3. 创建共享内存队列
            logger.debug(f"创建共享内存队列: {SHARED_MEM_NAME}")
            self._queue = SharedFrameQueue()
            if not self._queue.create(width, height, fps):
                raise RuntimeError(
                    f"创建共享内存队列失败 (名称={SHARED_MEM_NAME})"
                )
            logger.debug(f"共享内存队列创建成功, 句柄={self._queue._handle}")

            # 4. 写入初始帧 (黑色帧)
            black = b"\x00" * (width * height * 3)
            logger.debug(f"写入初始帧 ({width}x{height} RGB 黑帧, {len(black)} bytes)")
            self._queue.write_frame(black, width, height)

            logger.info(f"ManageCamera 虚拟摄像头就绪: {width}x{height}@{fps}")
            return True

        except Exception as e:
            import traceback
            logger.error(f"ManageCamera 初始化失败: {e}\n{traceback.format_exc()}")
            self.close()
            return False

    def send(self, frame) -> bool:
        """发送帧到虚拟摄像头

        Args:
            frame: OpenCV BGR 格式的 numpy 数组
        """
        if self._queue is None:
            logger.debug("send: 共享内存未初始化")
            return False
        if frame is None or frame.size == 0:
            logger.debug("send: 帧为空")
            return False

        try:
            import numpy as np
            import cv2

            # 转换为 RGB24
            if len(frame.shape) == 2:
                rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
            elif frame.shape[2] == 4:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
            elif frame.shape[2] == 3:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            else:
                logger.error(f"send: 不支持的帧格式, shape={frame.shape}")
                return False

            # 调整尺寸
            if rgb.shape[:2] != (self.height, self.width):
                logger.debug(f"send: 调整帧大小 {rgb.shape[:2]} -> ({self.height}, {self.width})")
                rgb = cv2.resize(rgb, (self.width, self.height))

            ok = self._queue.write_frame(rgb.tobytes(), self.width, self.height)
            if not ok:
                logger.warning("send: write_frame 返回 False")
            return ok

        except Exception as e:
            logger.error(f"发送帧失败: {e}")
            return False

    def close(self):
        """关闭后端（释放共享内存，COM 注册保持有效）"""
        if self._queue is not None:
            self._queue.close()
            self._queue = None
        self.width = 0
        self.height = 0

    def _unregister(self):
        """注销 DLL"""
        if self._dll_path and os.path.exists(self._dll_path):
            _unregister_dll(self._dll_path)
            self._dll_path = None

    @property
    def is_open(self) -> bool:
        return self._queue is not None and self._queue._initialized


# ── 虚拟摄像头管理器 ──

class VirtualCameraManager:
    """
    ManageCamera 虚拟摄像头管理器

    使用原生 DirectShow 滤镜 (vcam_filter.dll) 作为唯一后端。
    API 保持向后兼容。
    """

    def __init__(self):
        self._backend = None
        self._is_initialized = False
        self.width = 0
        self.height = 0
        self.fps = 30
        self._reinitializing = False

    @staticmethod
    def check_environment() -> VirtualCameraInfo:
        """检查虚拟摄像头环境"""
        ok, msg = NativeVCamBackend.is_available()
        if ok:
            return VirtualCameraInfo(
                available=True,
                backend="native",
                device_name="ManageCamera"
            )
        return VirtualCameraInfo(
            available=False,
            error=msg
        )

    def initialize(self, width: int, height: int, fps: int = 30) -> Tuple[bool, str]:
        """初始化虚拟摄像头"""
        if self._reinitializing:
            return False, "正在重新初始化中"

        self.close()
        self._reinitializing = True

        try:
            ok, msg = NativeVCamBackend.is_available()
            if not ok:
                return False, msg

            backend = NativeVCamBackend()
            if not backend.open(width, height, fps):
                return False, "ManageCamera 虚拟摄像头初始化失败"

            self._backend = backend
            self.width = width
            self.height = height
            self.fps = fps
            self._is_initialized = True
            logger.info(f"ManageCamera 就绪: {width}x{height}@{fps}")
            return True, f"Native DirectShow: ManageCamera"

        except Exception as e:
            logger.error(f"虚拟摄像头初始化失败: {e}")
            return False, str(e)
        finally:
            self._reinitializing = False

    def send_frame(self, frame) -> bool:
        """发送帧到虚拟摄像头"""
        if not self._is_initialized or self._backend is None:
            return False
        if frame is None or frame.size == 0:
            return False
        return self._backend.send(frame)

    def close(self):
        """关闭虚拟摄像头"""
        self._is_initialized = False
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized and self._backend is not None and self._backend.is_open

    @property
    def device_name(self) -> Optional[str]:
        if self._backend:
            return getattr(self._backend, 'device_name', None)
        return None


# ── 进程退出时清理 ──
@atexit.register
def _global_cleanup():
    """清理共享内存"""
    try:
        queue = SharedFrameQueue()
        queue.close()
    except Exception:
        pass

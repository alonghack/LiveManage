"""
ManageCamera 虚拟摄像头模块

基于 obs-virtual-cam 队列协议的 DirectShow 虚拟摄像头，无需管理员权限，
注册到 HKCU。支持 OBS、Chrome/Edge (getUserMedia)、快手/抖音直播伴侣、
Teams/Skype/Zoom 等所有 DirectShow 兼容应用。

架构:
    Python 进程 → 共享内存队列 (circular buffer)
    → vcam_filter.dll (DirectShow source filter)
    → 消费者应用 (OBS/浏览器/会议软件/直播伴侣)

共享内存协议 (adaptated from obs-virtual-cam):
    [queue_header]  — 60 bytes: 状态、格式、尺寸、write_index、队列长度
    [element 0]     — 32 bytes frame_header + RGB24 像素数据
    [element N-1]   — 32 bytes frame_header + RGB24 像素数据

独立使用示例::

    from virtual_cam import VirtualCamera

    vcam = VirtualCamera(width=1920, height=1080, fps=30)
    vcam.start()

    # 在你的帧循环中:
    while running:
        frame_bgr = get_your_frame()  # OpenCV BGR numpy array
        vcam.send(frame_bgr)

    vcam.stop()

集成到 LiveManage::

    from virtual_cam import VirtualCameraManager, AntiDetectionFilter

    manager = VirtualCameraManager()
    manager.initialize(1920, 1080, 30)

    anti_filter = AntiDetectionFilter({'add_noise': False, ...})

    # 发送帧:
    frame_processed = anti_filter.process(bgr_frame)
    manager.send_frame(frame_processed)
"""

from virtual_cam.virtual_camera import (
    VirtualCameraManager,
    VirtualCameraInfo,
    NativeVCamBackend,
    SharedFrameQueue,
)

from virtual_cam.anti_detection import AntiDetectionFilter

# ── 独立可用的简化接口 ──
from typing import Optional, Tuple
import numpy as np


class VirtualCamera:
    """
    虚拟摄像头的简化独立接口。

    供外部工具直接调用，无需依赖 LiveManage 的 GUI 框架。
    内部使用 VirtualCameraManager + NativeVCamBackend。

    用法::

        vcam = VirtualCamera(width=1920, height=1080, fps=30)
        vcam.start()

        while running:
            frame = get_your_bgr_frame()  # numpy array, BGR format
            vcam.send(frame)

        vcam.stop()

    参数:
        width: 输出宽度 (默认 1920)
        height: 输出高度 (默认 1080)
        fps: 帧率 (默认 30)
    """

    def __init__(self, width: int = 1920, height: int = 1080, fps: int = 30):
        self._width = width
        self._height = height
        self._fps = fps
        self._manager: Optional[VirtualCameraManager] = None
        self._started = False

    def start(self) -> Tuple[bool, str]:
        """
        启动虚拟摄像头。

        Returns:
            (success, message): 是否成功及状态消息
        """
        if self._started:
            return True, "虚拟摄像头已在运行"

        self._manager = VirtualCameraManager()
        ok, msg = self._manager.initialize(self._width, self._height, self._fps)
        if ok:
            self._started = True
        return ok, msg

    def send(self, frame: np.ndarray) -> bool:
        """
        发送一帧到虚拟摄像头。

        Args:
            frame: OpenCV BGR 格式的 numpy 数组 (uint8, HxWx3)

        Returns:
            是否发送成功
        """
        if not self._started or self._manager is None:
            return False
        if frame is None or frame.size == 0:
            return False
        return self._manager.send_frame(frame)

    def stop(self):
        """停止虚拟摄像头并释放资源。"""
        if self._manager is not None:
            self._manager.close()
            self._manager = None
        self._started = False

    @property
    def is_running(self) -> bool:
        """虚拟摄像头是否正在运行。"""
        return self._started and self._manager is not None and self._manager.is_initialized

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def fps(self) -> int:
        return self._fps

    @staticmethod
    def check_available() -> Tuple[bool, str]:
        """检查虚拟摄像头环境是否可用（不启动）。"""
        return NativeVCamBackend.is_available()


__all__ = [
    # 简化接口 (推荐外部工具使用)
    "VirtualCamera",
    # 完整接口 (LiveManage 内部使用)
    "VirtualCameraManager",
    "VirtualCameraInfo",
    "NativeVCamBackend",
    "SharedFrameQueue",
    # 防检测滤镜
    "AntiDetectionFilter",
]

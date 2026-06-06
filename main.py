
import os
import sys

# ── 统一日志配置 ──
from auxiliary.logger_config import (
    logger, log_startup, log_startup_summary
)

log_startup("日志系统初始化")





import asyncio
import collections
import random
import threading
import time
import shutil
import traceback
from pathlib import Path

log_startup("标准库导入完成")
from auxiliary.region import RegionSelector
from auxiliary.screen import VideoWidget, ClickableSlider, ChatWidget
from auxiliary.sound import PlaybackType, TsVoice
from auxiliary.utils import SQLiteManager, UIUpdater

log_startup("辅助模块导入完成 (RegionSelector, VideoWidget, ChatWidget, PlaybackType, TsVoice, SQLiteManager, UIUpdater)")


import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QComboBox, QTextEdit, QLineEdit,
    QStatusBar, QMessageBox, QFileDialog, QGroupBox, QSplitter,
    QStyle, QCheckBox, QRadioButton, QButtonGroup, QTabWidget, QListWidget,
    QListWidgetItem, QMenu, QInputDialog, QTreeWidget, QDialog, QDialogButtonBox, QTreeWidgetItem, QScrollArea,
    QGridLayout, QStyledItemDelegate, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QElapsedTimer
from PyQt6.QtGui import QPixmap, QImage, QPainter, QFont, QColor, QIcon, QPalette, QIntValidator

log_startup("PyQt6 导入完成")

from virtual_cam import VirtualCameraManager
from virtual_cam.anti_detection import AntiDetectionFilter

log_startup("虚拟摄像头模块导入完成")



# 检查可选依赖
try:
    import mss

    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


# ── HKLM 注册状态检查 ──

_hklm_warning_shown = False
STALE_CLSID_KWAI = "{FDE06CAC-5D0D-24BC-36E1-BEF87DEFF885}"
STALE_CLSID_WEBCAST = "{0C36C4D6-8672-4C6E-A446-CDEC9D0CB1A7}"
OUR_CLSID = "{5C2CD55C-92AD-4999-8666-912BD3E70020}"
VIDEO_INPUT_CAT = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"


def check_hklm_registration():
    """检查 HKLM 注册状态。缺失或存在第三方残留时提示用户运行管理员注册。"""
    global _hklm_warning_shown
    if _hklm_warning_shown:
        return
    _hklm_warning_shown = True

    import winreg
    import ctypes
    from PyQt6.QtWidgets import QMessageBox
    from pathlib import Path

    hklm_base = r"SOFTWARE\Classes\CLSID"

    # 检查我们的 CLSID 是否在 HKLM
    has_hklm = False
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             f"{hklm_base}\\{OUR_CLSID}")
        winreg.CloseKey(key)
        has_hklm = True
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # 检查是否有 Kwai 等第三方残留
    stale_found = []
    for stale_clsid, stale_name in [
        (STALE_CLSID_KWAI, "Kwai Virtual Camera"),
        (STALE_CLSID_WEBCAST, "WebcastMate"),
    ]:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 f"{hklm_base}\\{VIDEO_INPUT_CAT}\\Instance\\{stale_clsid}")
            winreg.CloseKey(key)
            stale_found.append(stale_name)
        except FileNotFoundError:
            pass
        except Exception:
            pass

    if has_hklm and not stale_found:
        logger.debug("HKLM 注册检查: 正常")
        return

    # 构建提示消息
    msg_parts = []
    if not has_hklm:
        msg_parts.append(
            "虚拟摄像头未在系统全局注册 (HKLM)\n"
            "→ OBS 等应用可能找不到 'ManageCamera' 摄像头\n"
        )
    if stale_found:
        msg_parts.append(
            f"发现第三方残留摄像头: {', '.join(stale_found)}\n"
            f"→ 这会导致摄像头列表中出现已卸载软件的旧项\n"
        )
    msg_parts.append("\n是否立即以管理员身份修复? (推荐)")

    msg = "\n".join(msg_parts)
    reply = QMessageBox.question(
        None,
        "虚拟摄像头注册",
        msg,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes
    )

    if reply == QMessageBox.StandardButton.Yes:
        script = Path(__file__).parent / "virtual_cam" / "register_admin.py"
        try:
            # 使用 ShellExecute "runas" 以管理员身份运行
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable,
                f'"{script}"', None, 1  # SW_SHOWNORMAL
            )
            logger.info("已启动管理员注册脚本")
        except Exception as e:
            logger.error(f"启动管理员脚本失败: {e}")
            QMessageBox.warning(
                None, "操作失败",
                f"无法以管理员身份启动:\n{e}\n\n"
                f"请手动右键终端 → 以管理员身份运行:\n"
                f"  python {script}"
            )


class VideoPlayer(QWidget):
    """视频播放器窗口 - 修复优化版本"""

    def __init__(self, main_window, tts, parent=None):
        super().__init__(parent)
        self.tts = tts
        self.main_window = main_window

        # 模式定义
        self.modes = {
            "视频文件": "video_file",
            "摄像头": "camera",
            "屏幕捕获": "screen_capture",
            "区域捕获": "region_capture",
            "模板文件": "template_file"
        }
        self.current_mode = "video_file"

        # 初始化视频捕获相关变量
        self.cap = None  # OpenCV视频捕获对象
        self.camera_index = 0  # 摄像头索引
        self.sct = None  # 屏幕捕获对象
        self.selected_region = None  # 区域捕获坐标
        self.template_path = ""  # 模板文件路径
        self.music_path = ""  # 音乐文件路径

        # 模板和音乐路径
        self.template_dirs = {
            "图片模板": Path("example/templates/img"),
            "视频模板": Path("example/templates/video")
        }
        self.music_dir = Path("example/templates/music")

        # 初始化播放器变量
        self.video_cap = None  # OpenCV VideoCapture 对象（视频文件播放）
        self.video_path = ""
        self.duration = 0
        self.is_running = False
        self.is_muted = False
        self.volume = 50
        self.last_position = 0
        self.is_seeking = False
        self.video_loaded = False
        self.loop_playback = False  # 添加循环播放标志
        self.looping_restart = False  # 标记是否刚刚触发过循环

        # 音乐播放状态变量
        self.music_playing = False
        self.music_paused = False

        # 虚拟摄像头相关变量
        self.virtual_cam = VirtualCameraManager()
        self.virtual_cam_fps = 30
        self.virtual_cam_width = 1920
        self.virtual_cam_height = 1080
        # 每个模式独立的输出分辨率: {mode_key: (width, height)}
        self.mode_resolutions = {
            "video_file": (1920, 1080),
            "camera": (1920, 1080),
            "screen_capture": (1920, 1080),
            "region_capture": (1920, 1080),
            "template_file": (1920, 1080),
        }
        self.virtual_cam_initialized = False
        self.virtual_cam_reinitializing = False  # 防止重复初始化

        # 性能优化相关变量
        self.frame_timer = QElapsedTimer()
        self.frame_count = 0
        self.fps = 0
        self.last_fps_update = 0
        self.frame_interval = 33  # 目标帧间隔(ms)，约30fps
        self.last_frame_time = 0

        # 图片模板相关
        self.image = None
        self.image_w = 0
        self.image_h = 0

        # 创建UI
        self.init_ui()

        # 创建定时器用于更新视频帧和状态
        self.timer = QTimer(self)
        self.timer.setInterval(self.frame_interval)
        self.timer.timeout.connect(self.update_frame)
        self.frame_timer.start()
        self.last_fps_update = time.time()

    def init_ui(self):
        """初始化用户界面"""
        # 创建主布局
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # 左侧：摄像头预览和控制
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)

        # 视频显示区域
        camera_group = QGroupBox("摄像头预览")
        camera_layout = QVBoxLayout(camera_group)
        camera_layout.setContentsMargins(3, 10, 3, 3)

        self.video_widget = VideoWidget()
        camera_layout.addWidget(self.video_widget)

        left_layout.addWidget(camera_group, 4)

        # 控制面板
        control_panel = QGroupBox("播放控制")
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(5, 8, 5, 5)
        control_layout.setSpacing(5)

        # 第一行控制按钮
        first_row_layout = QHBoxLayout()
        first_row_layout.setContentsMargins(0, 0, 0, 0)
        first_row_layout.setSpacing(8)

        # 播放控制按钮组
        control_btn_layout = QHBoxLayout()
        control_btn_layout.setSpacing(3)

        self.play_btn = QPushButton()
        self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_btn.setToolTip("播放")
        self.play_btn.setFixedWidth(35)
        self.play_btn.clicked.connect(self.switch_is_working)

        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_btn.setToolTip("停止")
        self.stop_btn.setFixedWidth(35)
        self.stop_btn.clicked.connect(self.stop)

        self.mute_btn = QPushButton()
        self.mute_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.mute_btn.setToolTip("静音")
        self.mute_btn.setFixedWidth(35)
        self.mute_btn.clicked.connect(self.toggle_mute)

        control_btn_layout.addWidget(self.play_btn)
        control_btn_layout.addWidget(self.stop_btn)
        control_btn_layout.addWidget(self.mute_btn)

        # 音量控制
        volume_layout = QHBoxLayout()
        volume_layout.setSpacing(3)
        volume_label = QLabel("音量:")
        volume_label.setFixedWidth(25)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.volume)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.valueChanged.connect(self.set_volume)
        self.volume_value_label = QLabel(f"{self.volume}%")
        self.volume_value_label.setFixedWidth(30)

        volume_layout.addWidget(volume_label)
        volume_layout.addWidget(self.volume_slider)
        volume_layout.addWidget(self.volume_value_label)

        # 模式选择
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(3)
        mode_label = QLabel("模式:")
        mode_label.setFixedWidth(30)
        self.mode_combo = QComboBox()
        self.mode_combo.setFixedWidth(80)
        self.mode_combo.addItems(list(self.modes.keys()))
        self.mode_combo.currentTextChanged.connect(self.change_mode)

        mode_layout.addWidget(mode_label)
        mode_layout.addWidget(self.mode_combo)

        # 输出分辨率选择（每个模式独立，始终可见）
        self.resolution_combo = QComboBox()
        self.resolution_combo.setFixedWidth(100)
        self.resolution_combo.setStyleSheet("font-size: 10px;")
        self.resolution_combo.setToolTip("选择输出到虚拟摄像头的分辨率")
        self.resolution_combo.currentTextChanged.connect(self.on_resolution_changed)
        self.populate_resolution_combo("video_file")  # 初始默认模式

        # 动态控件容器
        self.dynamic_widget_container = QWidget()
        self.dynamic_layout = QHBoxLayout(self.dynamic_widget_container)
        self.dynamic_layout.setContentsMargins(0, 0, 0, 0)
        self.dynamic_layout.setSpacing(5)

        # 区域选择按钮
        self.region_btn = QPushButton("选择区域")
        self.region_btn.setFixedSize(80, 30)
        self.region_btn.clicked.connect(self.select_region)
        self.region_btn.setVisible(False)

        # 循环播放复选框
        self.loop_checkbox = QCheckBox("循环播放")
        self.loop_checkbox.setFixedSize(80, 30)
        self.loop_checkbox.setChecked(self.loop_playback)
        self.loop_checkbox.stateChanged.connect(self.toggle_loop_playback)

        # 将动态控件添加到动态容器
        self.dynamic_layout.addWidget(self.region_btn)
        self.dynamic_layout.addWidget(self.loop_checkbox)

        # FPS显示
        self.fps_label = QLabel("FPS: 0")
        self.fps_label.setFixedSize(60, 30)
        self.fps_label.setStyleSheet("color: #666; font-size: 11px;")

        # 将组件添加到第一行布局
        first_row_layout.addLayout(control_btn_layout)
        first_row_layout.addLayout(volume_layout)
        first_row_layout.addLayout(mode_layout)
        first_row_layout.addWidget(self.resolution_combo)
        first_row_layout.addWidget(self.dynamic_widget_container)
        first_row_layout.addWidget(self.fps_label)
        first_row_layout.addStretch(1)

        # 第二行控制按钮（视频文件模式）
        self.second_row_widget = QWidget()
        self.second_row_layout = QHBoxLayout(self.second_row_widget)
        self.second_row_layout.setContentsMargins(0, 0, 0, 0)
        self.second_row_layout.setSpacing(5)

        # 打开文件按钮
        self.open_btn = QPushButton("打开视频")
        self.open_btn.setFixedSize(80, 25)
        self.open_btn.clicked.connect(self.open_file)

        # 进度条
        self.progress_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.sliderPressed.connect(self.progress_pressed)
        self.progress_slider.sliderReleased.connect(self.progress_released)
        self.progress_slider.sliderMoved.connect(self.progress_moved)

        # 时间显示
        time_layout = QHBoxLayout()
        time_layout.setSpacing(2)
        self.current_time_label = QLabel("00:00:00")
        self.current_time_label.setFixedWidth(50)
        self.current_time_label.setStyleSheet("color: #666; font-size: 10px;")

        self.delimiter = QLabel("/")
        self.delimiter.setFixedWidth(10)
        self.delimiter.setStyleSheet("color: #666; font-size: 10px;")

        self.total_time_label = QLabel("00:00:00")
        self.total_time_label.setFixedWidth(50)
        self.total_time_label.setStyleSheet("color: #666; font-size: 10px;")

        time_layout.addWidget(self.current_time_label)
        time_layout.addWidget(self.delimiter)
        time_layout.addWidget(self.total_time_label)

        # 添加到第二行布局
        self.second_row_layout.addWidget(self.open_btn)
        self.second_row_layout.addWidget(self.progress_slider, 1)
        self.second_row_layout.addLayout(time_layout)

        # 模板文件模式专用控制行
        self.template_row_widget = QWidget()
        template_row_layout = QHBoxLayout(self.template_row_widget)
        template_row_layout.setContentsMargins(0, 0, 0, 0)
        template_row_layout.setSpacing(5)

        # 模板选择
        template_select_layout = QHBoxLayout()
        template_select_layout.setSpacing(3)
        template_label = QLabel("模板:")
        template_label.setFixedWidth(30)
        self.template_combo = QComboBox()
        self.template_combo.setFixedWidth(90)
        self.template_combo.addItems(["请选择...", "图片模板", "视频模板"])
        self.template_combo.currentTextChanged.connect(self.switch_template_type)

        self.template_file_combo = QComboBox()
        self.template_file_combo.addItem("请选择...")
        self.template_file_combo.setFixedWidth(120)
        self.template_file_combo.currentTextChanged.connect(self.select_template_file)

        template_select_layout.addWidget(template_label)
        template_select_layout.addWidget(self.template_combo)
        template_select_layout.addWidget(self.template_file_combo)

        # 音乐选择
        music_select_layout = QHBoxLayout()
        music_select_layout.setSpacing(3)
        music_label = QLabel("音乐:")
        music_label.setFixedWidth(30)
        self.music_combo = QComboBox()
        self.music_combo.setFixedWidth(120)
        self.music_combo.addItem("请选择...")
        self.music_combo.currentTextChanged.connect(self.select_music)

        music_select_layout.addWidget(music_label)
        music_select_layout.addWidget(self.music_combo)

        template_row_layout.addLayout(template_select_layout)
        template_row_layout.addLayout(music_select_layout)
        template_row_layout.addStretch(1)

        self.template_row_widget.setVisible(False)

        # 添加到控制布局
        control_layout.addLayout(first_row_layout)
        control_layout.addWidget(self.second_row_widget)
        control_layout.addWidget(self.template_row_widget)

        # 添加到左侧布局
        left_layout.addWidget(control_panel, 1)

        # 添加到主布局
        main_layout.addWidget(left_widget)

        # 设置初始状态
        self.update_ui_state()

    def initialize_virtual_camera(self, width=None, height=None, fps=30):
        """初始化虚拟摄像头（使用原生 DirectShow 滤镜）"""
        try:
            # 如果正在重新初始化，直接返回
            if self.virtual_cam_reinitializing:
                logger.info("虚拟摄像头正在重新初始化，跳过重复操作")
                return False

            # 如果已经初始化且参数相同，直接返回
            if (self.virtual_cam_initialized and self.virtual_cam.is_initialized and
                    width == self.virtual_cam_width and
                    height == self.virtual_cam_height and
                    fps == self.virtual_cam_fps):
                logger.info("虚拟摄像头已经初始化，跳过重复初始化")
                return True

            self.virtual_cam_reinitializing = True

            # 先关闭现有的虚拟摄像头
            self.close_virtual_camera()

            # 如果没有指定大小，使用默认值
            if width is None:
                width = self.virtual_cam_width
            if height is None:
                height = self.virtual_cam_height

            # 检查虚拟摄像头是否可用
            env_info = VirtualCameraManager.check_environment()
            if not env_info.available:
                logger.info(f"虚拟摄像头环境未就绪: {env_info.error}")
                self.virtual_cam_reinitializing = False

                # 提供安装提示
                error_msg = (
                    "虚拟摄像头不可用\n\n"
                    "需要编译原生 DirectShow 滤镜驱动:\n"
                    "请确保 MinGW GCC (gcc) 已安装并在 PATH 中\n\n"
                    "或手动编译 vcam_filter.dll:\n"
                    "  cd virtual_cam/vcam_filter\n"
                    "  gcc -shared -O2 -m64 -o vcam_filter.dll vcam_filter.c "
                    "-lole32 -loleaut32 -luuid -lkernel32 -luser32 -lstrmiids -Wl,--kill-at"
                )
                self.show_error("虚拟摄像头不可用", error_msg)
                return False

            # 检查 HKLM 注册状态（OBS 等应用需要 HKLM 才能发现摄像头）
            check_hklm_registration()

            # 初始化虚拟摄像头
            success, msg = self.virtual_cam.initialize(width, height, fps)
            if success:
                self.virtual_cam_width = width
                self.virtual_cam_height = height
                self.virtual_cam_fps = fps
                self.virtual_cam_initialized = True
                self.virtual_cam_reinitializing = False
                logger.info(f"虚拟摄像头初始化成功: {msg}")
                return True
            else:
                raise Exception(msg)

        except Exception as e:
            error_msg = f"初始化虚拟摄像头失败: {str(e)}"
            logger.info(error_msg)
            self.show_error("虚拟摄像头错误", error_msg)
            self.virtual_cam_initialized = False
            self.virtual_cam_reinitializing = False
            return False

    def close_virtual_camera(self):
        """关闭虚拟摄像头"""
        try:
            self.virtual_cam.close()
            self.virtual_cam_initialized = False
            logger.info("虚拟摄像头已关闭")
        except Exception as e:
            logger.info(f"关闭虚拟摄像头时发生错误: {str(e)}")
            self.virtual_cam_initialized = False

    def send_frame_to_virtual_camera(self, frame):
        """发送帧到虚拟摄像头。
        帧必须是 BGR 格式（OpenCV 原生格式）。"""
        if not self.virtual_cam_initialized or frame is None:
            return

        try:
            if frame.size == 0:
                return

            # 调整帧大小以匹配虚拟摄像头分辨率
            if frame.shape[:2] != (self.virtual_cam_height, self.virtual_cam_width):
                try:
                    frame = cv2.resize(frame, (self.virtual_cam_width, self.virtual_cam_height))
                except Exception:
                    return

            # 确保帧是3通道BGR（只做最小转换）
            try:
                if len(frame.shape) == 2:  # 灰度图
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                elif frame.shape[2] == 4:  # BGRA
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                # 3通道: 假设已经是BGR — 不做转换
            except Exception:
                return

            # 应用防检测效果 (仅影响虚拟摄像头输出)
            try:
                frame = self.anti_detection_filter.process(frame)
            except Exception:
                pass  # 滤镜失败时静默继续，使用原始帧

            # 发送到虚拟摄像头
            if not self.virtual_cam.send_frame(frame):
                self.close_virtual_camera()

        except Exception as e:
            logger.info(f"发送帧到虚拟摄像头时出错: {str(e)}")
            self.close_virtual_camera()

    def update_ui_for_mode(self):
        """根据当前模式更新UI"""
        # 隐藏所有第二行控件
        self.second_row_widget.setVisible(False)
        self.template_row_widget.setVisible(False)

        # 重置动态容器中的控件状态
        self.region_btn.setVisible(False)
        self.loop_checkbox.setVisible(True)
        self.loop_checkbox.setEnabled(True)

        if self.current_mode == "video_file":
            self.second_row_widget.setVisible(True)
            self.loop_playback = False
            self.loop_checkbox.setChecked(False)
            self.main_window.status_bar.showMessage("视频文件模式: 可选择本地视频文件播放")
            self.populate_resolution_combo("video_file")

        elif self.current_mode == "camera":
            self.second_row_widget.setVisible(False)
            self.main_window.status_bar.showMessage("摄像头模式: 使用摄像头实时捕获")
            self.populate_resolution_combo("camera")

        elif self.current_mode == "screen_capture":
            self.second_row_widget.setVisible(False)
            self.main_window.status_bar.showMessage("屏幕捕获模式: 捕获整个屏幕")
            self.populate_resolution_combo("screen_capture")

        elif self.current_mode == "region_capture":
            self.second_row_widget.setVisible(False)
            self.region_btn.setVisible(True)
            self.loop_checkbox.setVisible(False)  # 完全隐藏而非禁用

            # 修复：强制更新动态容器布局
            self.dynamic_widget_container.updateGeometry()
            self.dynamic_widget_container.adjustSize()

            self.main_window.status_bar.showMessage("区域捕获模式: 捕获指定屏幕区域")
            self.populate_resolution_combo("region_capture")

        elif self.current_mode == "template_file":
            self.template_row_widget.setVisible(True)
            self.loop_playback = True
            self.loop_checkbox.setChecked(True)
            self.loop_checkbox.setEnabled(False)
            self.main_window.status_bar.showMessage("模板文件模式: 使用预定义模板和音乐（自动循环）")
            self.populate_resolution_combo("template_file")

        # 强制布局更新
        self.updateGeometry()
        QApplication.processEvents()

        # 延迟优化布局
        QTimer.singleShot(50, self.optimize_layout)

    def optimize_layout(self):
        """优化布局以防止间距问题"""
        try:
            # 强制重新计算布局
            self.updateGeometry()
            if hasattr(self, 'layout'):
                self.layout().activate()

            # 在区域捕获模式下特别处理布局
            if self.current_mode == "region_capture":
                # 确保动态容器正确调整大小
                self.dynamic_widget_container.updateGeometry()
                self.dynamic_widget_container.adjustSize()

                # 强制视频控件保持正确比例
                if hasattr(self, 'video_widget'):
                    self.video_widget.updateGeometry()

                # 添加小的延迟确保布局稳定
                QTimer.singleShot(10, lambda: self.video_widget.update() if hasattr(self, 'video_widget') else None)

        except Exception as e:
            logger.info(f"布局优化时出错: {e}")

    def change_mode(self, mode_text):
        """切换模式 - 优化版本"""
        old_mode = self.current_mode
        self.current_mode = self.modes[mode_text]

        # 停止当前播放
        if old_mode != self.current_mode:
            self.stop()
            self.safe_close_player()

        # 如果进入模板模式，清空并重置下拉框
        if self.current_mode == "template_file":
            self.template_combo.setCurrentIndex(0)  # 回到“请选择模板类型”
            self.template_file_combo.clear()
            self.template_file_combo.addItem("待选择")
            self.template_file_combo.setEnabled(True)

            self.music_combo.clear()
            self.music_combo.addItem("待选择")
            self.music_combo.setEnabled(False)

            # 清空音乐，但保留模板路径，等用户选择
            self.music_path = ""
        else:
            # 非模板模式，彻底清空路径
            self.template_path = ""
            self.music_path = ""

        # 更新UI
        self.update_ui_for_mode()

        # 强制重新计算布局
        self.adjustSize()
        self.updateGeometry()

        self.main_window.status_bar.showMessage(f"模式已切换: {mode_text}")

    def select_template_file(self, template_file):
        """选择模板文件"""
        if self.is_running:
            return

        # 过滤无效选择
        if not template_file or template_file in ["待选择", "目录不存在", "加载失败"]:
            self.template_path = ""
            self.video_loaded = False
            self.music_combo.clear()
            self.music_combo.addItem("待选择")
            self.music_combo.setEnabled(False)
            return

        template_type = self.template_combo.currentText()
        template_dir = self.template_dirs.get(template_type, "")
        if not template_dir or not os.path.exists(template_dir):
            self.template_path = ""
            self.video_loaded = False
            return

        # 设置模板文件路径
        self.template_path = os.path.join(template_dir, template_file)

        # 重置音乐播放状态
        self.music_playing = False
        self.music_paused = False

        # 启用音乐选择
        self.music_combo.clear()
        self.music_combo.addItem("待选择")
        if os.path.exists(self.music_dir):
            music_files = [f for f in os.listdir(self.music_dir)
                           if f.lower().endswith(('.mp3', '.wav', '.ogg'))]
            for f in music_files:
                self.music_combo.addItem(f)
        self.music_combo.setEnabled(True)

        # 显示模板
        if template_type == "图片模板":
            image = cv2.imread(self.template_path)
            if image is not None:
                self.image = image  # 保持 BGR（虚拟摄像头需要 BGR）
                self.image_h, self.image_w, ch = image.shape
                bytes_per_line = ch * self.image_w
                # 显示用 RGB（QImage 需要 RGB888）
                rgb_display = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                qt_image = QImage(rgb_display.data, self.image_w, self.image_h, bytes_per_line,
                                  QImage.Format.Format_RGB888)
                self.video_widget.setImage(qt_image)
                self.video_loaded = True
                self.main_window.status_bar.showMessage("图片模板已加载")
                self.update_ui_state()
        else:  # 视频模板
            if self.load_video_file(self.template_path):
                self.main_window.status_bar.showMessage("视频模板已加载，点击播放按钮开始播放")

        self.main_window.status_bar.showMessage(f"已选择模板: {template_file}")

    def initialize_template_file(self):
        """初始化模板文件"""
        if not self.template_path or not os.path.exists(self.template_path):
            self.show_error("模板错误", "请先选择有效的模板文件")
            return False

        try:
            # 图片模板
            if self.template_path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                image = cv2.imread(self.template_path)
                if image is not None:
                    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                    h, w, ch = image.shape
                    bytes_per_line = ch * w
                    qt_image = QImage(image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
                    self.video_widget.setImage(qt_image)
                    self.video_loaded = True
                    self.main_window.status_bar.showMessage("图片模板已加载")
                    return True
                else:
                    self.show_error("模板错误", "无法加载图片模板")
                    return False
            else:
                # 视频模板用 OpenCV 播放
                return self.load_video_file(self.template_path)
        except Exception as e:
            self.show_error("模板初始化错误", f"初始化模板时出错: {str(e)}")
            return False


    def toggle_loop_playback(self, state):
        """切换循环播放状态"""
        self.loop_playback = (state == Qt.CheckState.Checked.value)
        # OpenCV 循环播放由 update_video_file_frame 中的读取逻辑自动处理

        if self.music_path and self.tts.get_playback_status(PlaybackType.FILE):
            self.tts.stop()

        status = "开启" if self.loop_playback else "关闭"
        self.main_window.status_bar.showMessage(f"循环播放已{status}")
        logger.info(f"循环播放: {status}")

    def open_file(self):
        """根据当前模式打开不同的源"""
        try:
            if self.is_running:
                return
            if self.current_mode == "video_file":
                self.open_video_file()
            elif self.current_mode == "camera":
                self.initialize_camera()
            elif self.current_mode == "screen_capture":
                self.initialize_screen_capture()
            elif self.current_mode == "region_capture":
                self.initialize_region_capture()
        except Exception as e:
            self.show_error("打开源错误", f"打开源时发生错误: {str(e)}")

    def open_video_file(self):
        """打开视频文件"""
        if not CV2_AVAILABLE:
            self.show_error("OpenCV不可用", "请安装opencv-python包: pip install opencv-python")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "打开视频文件", "",
            "视频文件 (*.mp4 *.avi *.mkv *.mov *.flv *.wmv *.mpg *.mpeg *.webm *.ts *.mts *.m2ts)"
        )

        if file_path:
            self.load_video_file(file_path)

    def initialize_camera(self):
        """初始化摄像头"""
        if not CV2_AVAILABLE:
            self.show_error("OpenCV不可用", "请安装OpenCV包: pip install opencv-python")
            return False

        try:
            if self.cap is not None:
                self.cap.release()

            # 尝试不同的摄像头索引
            for i in range(5):
                self.cap = cv2.VideoCapture(i)
                if self.cap.isOpened():
                    self.camera_index = i
                    self.video_loaded = True
                    self.main_window.status_bar.showMessage(f"摄像头 {i} 已就绪")
                    self.update_ui_state()
                    return True

            self.show_error("摄像头错误", "无法打开任何摄像头")
            return False
        except Exception as e:
            self.show_error("摄像头初始化错误", f"初始化摄像头时出错: {str(e)}")
            return False

    def initialize_screen_capture(self):
        """初始化屏幕捕获"""
        if not MSS_AVAILABLE:
            self.show_error("屏幕捕获不可用", "请安装mss包: pip install mss")
            return False

        try:
            if self.sct is None:
                self.sct = mss.mss()
            self.video_loaded = True
            self.main_window.status_bar.showMessage("屏幕捕获已就绪")
            self.update_ui_state()
            return True
        except Exception as e:
            self.show_error("屏幕捕获错误", f"初始化屏幕捕获时出错: {str(e)}")
            return False

    def select_region(self):
        """选择捕获区域"""
        try:
            self.region_selector = RegionSelector()
            self.region_selector.region_selected.connect(self.on_region_selected)
            self.region_selector.selection_cancelled.connect(self.on_selection_cancelled)

            # 先显示选择器再隐藏主窗口
            self.region_selector.show()

            # 短暂延迟后隐藏主窗口
            QTimer.singleShot(100, self.main_window.hide)

        except Exception as e:
            logger.info(f"区域选择器创建失败: {str(e)}")
            self.main_window.show()

    def on_region_selected(self, region):
        """区域选择完成"""
        try:
            # 验证区域数据
            if not region or 'left' not in region or 'top' not in region:
                raise ValueError("无效的区域数据")

            self.selected_region = region
            logger.info(f"验证后的区域: {self.selected_region}")

            # 显示主窗口
            self.main_window.show()
            self.main_window.raise_()
            self.main_window.activateWindow()

            self.main_window.status_bar.showMessage(
                f"区域选择完成: {self.selected_region['width']}x{self.selected_region['height']}")

        except Exception as e:
            logger.info(f"处理区域选择结果失败: {str(e)}")
            self.main_window.show()
            QMessageBox.warning(self, "错误", f"区域选择无效: {str(e)}")

    def on_selection_cancelled(self):
        """区域选择取消"""
        logger.info("区域选择取消")
        self.main_window.show()
        self.main_window.status_bar.showMessage("区域选择已取消")

    def initialize_region_capture(self):
        """初始化区域捕获"""
        if not MSS_AVAILABLE:
            self.show_error("区域捕获不可用", "请安装mss包: pip install mss")
            return False

        try:
            if self.selected_region is None:
                QMessageBox.warning(self, "警告", "请先选择有效区域")
                return False

            if self.sct is None:
                self.sct = mss.mss()

            self.video_loaded = True
            self.main_window.status_bar.showMessage("区域捕获已就绪")
            self.update_ui_state()
            return True
        except Exception as e:
            self.show_error("区域捕获错误", f"初始化区域捕获时出错: {str(e)}")
            return False

    def switch_template_type(self, template_type):
        logger.info("加载模板文件列表")
        self.template_file_combo.clear()
        self.template_file_combo.addItem("待选择")
        self.music_combo.clear()
        self.music_combo.addItem("待选择")
        self.music_combo.setEnabled(False)

        if template_type in ["图片模板", "视频模板"]:
            template_dir = self.template_dirs.get(template_type, "")
            if not os.path.exists(template_dir):
                self.template_file_combo.clear()
                self.template_file_combo.addItem("目录不存在")
                return

            files = os.listdir(template_dir)
            if template_type == "图片模板":
                files = [f for f in files if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))]
            else:
                files = [f for f in files if f.lower().endswith(('.mp4', '.avi', '.mkv', '.mov'))]

            for f in files:
                self.template_file_combo.addItem(f)




    def select_music(self, music_file):
        """选择音乐文件"""
        # 如果正在播放音乐，先停止
        if self.music_playing or self.music_paused:
            self.tts.stop()
            self.music_playing = False
            self.music_paused = False
            self.is_running = False

        if music_file and music_file != "请选择...":
            self.music_path = os.path.join(self.music_dir, music_file)
            self.main_window.status_bar.showMessage(f"已选择音乐: {music_file}，点击播放按钮开始播放")

            # 更新UI状态
            self.update_ui_state()
        else:
            self.music_path = ""
            self.main_window.status_bar.showMessage("已取消音乐选择")
            self.update_ui_state()

    def load_video_file(self, file_path):
        """加载视频文件（使用 OpenCV VideoCapture）"""
        if not CV2_AVAILABLE:
            self.show_error("OpenCV不可用", "请安装opencv-python包: pip install opencv-python")
            return False

        logger.info(f"[load_video_file] file_path = {file_path}")

        try:
            # 关闭当前播放器
            self.safe_close_player()

            logger.info(f"正在加载视频文件: {file_path}")

            # 使用 OpenCV VideoCapture 打开视频文件
            self.video_cap = cv2.VideoCapture(file_path)
            if not self.video_cap.isOpened():
                self.show_error("视频错误", f"无法打开视频文件: {file_path}")
                self.video_cap = None
                return False

            # 获取视频属性
            fps = self.video_cap.get(cv2.CAP_PROP_FPS)
            frame_count = self.video_cap.get(cv2.CAP_PROP_FRAME_COUNT)

            # 兜底处理：某些视频可能无法获取正确属性
            if fps <= 0:
                fps = 30.0
            if frame_count <= 0:
                self.duration = 9999  # 无法获取时长时的占位符
            else:
                self.duration = frame_count / fps

            self.video_fps = fps
            logger.info(f"视频信息: fps={fps:.1f}, 帧数={frame_count:.0f}, 时长={self.duration:.1f}秒")

            # 标记视频已加载
            self.video_path = file_path
            self.video_loaded = True
            self.is_running = False

            # 更新UI
            self.setWindowTitle(f"视频播放器 - {os.path.basename(file_path)}")
            self.update_time_display(0)
            self.progress_slider.setValue(0)
            self.last_position = 0

            # 更新按钮状态
            self.update_ui_state()

            logger.info(f"视频加载完成 (mode={self.current_mode})")
            return True

        except Exception as e:
            error_msg = f"加载视频时发生错误: {str(e)}\n\n{traceback.format_exc()}"
            logger.info(error_msg)
            self.show_error("加载视频错误", error_msg)
            self.safe_close_player()
            return False

    def switch_is_working(self):
        """切换播放/暂停状态 - 统一控制所有模式"""
        try:
            # 防止重复操作
            if hasattr(self, 'processing_play_request') and self.processing_play_request:
                return
            self.processing_play_request = True

            # 如果当前没有加载视频，尝试根据模式初始化
            if not self.video_loaded:
                success = self.initialize_for_current_mode()
                if not success:
                    self.processing_play_request = False
                    return

            # 模板文件模式特殊处理 - 图片模板 + 音乐
            if (self.current_mode == "template_file" and
                    self.template_path and
                    self.template_path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))):

                if self.music_path:
                    # 音乐播放控制
                    if not self.music_playing and not self.music_paused:
                        # 开始播放音乐
                        self.play_music_with_loop(self.music_path)
                        self.music_playing = True
                        self.music_paused = False
                        self.is_running = True
                        self.main_window.status_bar.showMessage("音乐播放中")
                        logger.info("开始播放音乐")

                    elif self.music_paused:
                        # 恢复播放音乐
                        if self.tts.resume_audio_file():
                            self.music_paused = False
                            self.is_running = True
                            self.main_window.status_bar.showMessage("音乐播放中")
                            logger.info("恢复播放音乐")
                        else:
                            # 如果恢复失败，重新开始播放
                            self.play_music_with_loop(self.music_path)
                            self.music_paused = False
                            self.is_running = True
                            self.main_window.status_bar.showMessage("音乐播放中")
                            logger.info("重新开始播放音乐")

                    elif self.music_playing and not self.music_paused:
                        # 暂停播放音乐
                        if self.tts.pause_audio_file():
                            self.music_paused = True
                            self.is_running = False
                            self.main_window.status_bar.showMessage("音乐已暂停")
                            logger.info("暂停播放音乐")
                        else:
                            logger.info("暂停音乐失败")
                else:
                    # 只有图片模板，没有音乐
                    self.is_running = not self.is_running
                    if self.is_running:
                        self.main_window.status_bar.showMessage("图片模板显示中")
                    else:
                        self.main_window.status_bar.showMessage("图片模板已暂停")

                # 初始化虚拟摄像头（如果还没初始化）
                if not self.virtual_cam_initialized:
                    self.initialize_virtual_camera_for_current_mode()

                # 输出到虚拟摄像头
                if self.virtual_cam_initialized and hasattr(self, 'image'):
                    self.send_frame_to_virtual_camera(self.image)

                self.update_ui_state()
                self.processing_play_request = False
                return

            # 其他模式的播放控制
            self.is_running = not self.is_running

            # 处理视频播放器控制（视频文件和视频模板）
            if self.video_cap:
                if self.is_running:
                    # OpenCV VideoCapture 不需要显式 "play"，timer 驱动帧读取
                    self.timer.start()
                    self.main_window.status_bar.showMessage("视频播放中")
                    logger.info("开始播放视频")

                    # 初始化虚拟摄像头（如果还没初始化）
                    if not self.virtual_cam_initialized:
                        self.initialize_virtual_camera_for_current_mode()
                else:
                    self.timer.stop()
                    self.main_window.status_bar.showMessage("视频已暂停")
                    logger.info("暂停播放视频")

            # 捕获模式的控制
            elif self.current_mode in ["camera", "screen_capture", "region_capture"]:
                if self.is_running:
                    self.timer.start()
                    self.main_window.status_bar.showMessage("捕获中")
                    logger.info("开始捕获")

                    # 初始化虚拟摄像头（如果还没初始化）
                    if not self.virtual_cam_initialized:
                        self.initialize_virtual_camera_for_current_mode()
                else:
                    self.timer.stop()
                    self.main_window.status_bar.showMessage("已暂停")
                    logger.info("暂停捕获")

            self.update_ui_state()
            logger.info(f"播放状态: {'播放' if self.is_running else '暂停'}")

        except Exception as e:
            self.show_error("播放控制错误", f"切换播放状态时出错: {str(e)}")
            self.is_running = False
            self.update_ui_state()
        finally:
            self.processing_play_request = False

    def initialize_virtual_camera_for_current_mode(self):
        """根据当前模式初始化虚拟摄像头"""
        try:
            width, height = self.get_current_mode_resolution()
            if width and height:
                return self.initialize_virtual_camera(width, height)
            else:
                # 如果没有特定分辨率，使用默认值
                return self.initialize_virtual_camera()
        except Exception as e:
            logger.info(f"初始化虚拟摄像头失败: {str(e)}")
            return False

    def get_current_mode_resolution(self):
        """获取当前模式的分辨率。使用该模式用户选择的输出分辨率。"""
        try:
            # 返回当前模式保存的分辨率
            res = self.mode_resolutions.get(self.current_mode)
            if res is not None:
                return res

            # 回退：从视频源自动检测
            if self.current_mode == "camera" and self.cap is not None:
                width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                return width, height
            elif self.current_mode == "screen_capture" and self.sct is not None:
                monitor = self.sct.monitors[1]
                return monitor["width"], monitor["height"]
            elif self.current_mode == "region_capture" and self.selected_region is not None:
                return self.selected_region['width'], self.selected_region['height']
            elif (self.current_mode == "template_file" and
                  self.template_path and
                  self.template_path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))):
                return self.image_w, self.image_h
            elif self.current_mode in ["video_file", "template_file"] and self.video_loaded:
                return self.virtual_cam_width, self.virtual_cam_height
        except Exception as e:
            logger.info(f"获取分辨率失败: {str(e)}")

        return None, None

    def play_music_with_loop(self, music_path):
        """循环播放音乐"""
        try:
            if not os.path.exists(music_path):
                self.show_error("音乐文件错误", f"找不到音乐文件: {music_path}")
                return False

            # 停止当前可能正在播放的音乐
            self.tts.stop_file_playback()

            # 播放音乐文件
            success = self.tts.play_audio_file(music_path, block=False)
            if success:
                # 启动音乐结束检测线程
                threading.Thread(target=self._monitor_music_completion, args=(music_path,), daemon=True).start()
                return True
            else:
                self.show_error("播放错误", "无法播放音乐文件")
                return False

        except Exception as e:
            self.show_error("音乐播放错误", f"播放音乐时出错: {str(e)}")
            return False

    def _monitor_music_completion(self, music_path):
        """监控音乐播放完成，实现循环播放"""
        try:
            # 等待一段时间后检查播放状态
            time.sleep(2)  # 给播放器一些启动时间

            max_wait_time = 300  # 最大等待时间5分钟
            check_interval = 0.5  # 检查间隔

            start_time = time.time()

            while (time.time() - start_time) < max_wait_time:
                # 检查播放状态
                is_file_playing = self.tts.is_file_playing()
                is_paused = self.tts.is_playback_paused(PlaybackType.FILE)

                # 如果文件没有在播放且没有被暂停，说明播放完成了
                if not is_file_playing and not is_paused:
                    logger.info("音乐播放完成，重新开始播放")
                    # 重新播放音乐
                    if self.music_playing and not self.music_paused:
                        self.play_music_with_loop(music_path)
                    break

                # 如果播放被停止了，退出循环
                if not self.music_playing:
                    break

                time.sleep(check_interval)

        except Exception as e:
            logger.info(f"音乐监控线程出错: {str(e)}")

    def initialize_for_current_mode(self):
        """根据当前模式初始化视频源"""
        try:
            # 先停止所有可能的播放
            self.stop_all_playback()

            if self.current_mode == "video_file":
                # 视频文件模式需要用户选择文件
                if not self.video_path:
                    self.open_video_file()
                    return self.video_loaded

                # 初始化 OpenCV 视频播放器
                success = self.initialize_video_player()
                if success:
                    # 设置视频音量
                    # OpenCV 不处理音频，音量设置仅记录: audio_set_volume(int(self.volume))
                    logger.info(f"视频音量设置为: {self.volume}%")
                return success

            elif self.current_mode == "camera":
                return self.initialize_camera()

            elif self.current_mode == "screen_capture":
                return self.initialize_screen_capture()

            elif self.current_mode == "region_capture":
                return self.initialize_region_capture()

            elif self.current_mode == "template_file":
                return self.initialize_template_file()

            return False

        except Exception as e:
            self.show_error("初始化错误", f"初始化视频源时出错: {str(e)}")
            return False



    def initialize_video_player(self):
        """初始化视频播放器（使用 OpenCV VideoCapture）"""
        if self.video_path and os.path.exists(self.video_path):
            return self.load_video_file(self.video_path)
        return False

    def stop_all_playback(self):
        """停止所有播放"""
        try:
            # 停止音乐播放
            if hasattr(self, 'tts') and self.tts:
                self.tts.stop_file_playback()

            # 停止视频播放
            if self.video_cap is not None:
                pass  # OpenCV 无需显式 stop

            # 停止捕获
            if hasattr(self, 'timer') and self.timer.isActive():
                self.timer.stop()

            # 重置状态
            self.is_running = False
            self.music_playing = False
            self.music_paused = False

            logger.info("所有播放已停止")

        except Exception as e:
            logger.info(f"停止播放时出错: {str(e)}")

    def set_volume(self, value):
        """设置音量 - 修复异常退出问题"""
        try:
            self.volume = value
            self.volume_value_label.setText(f"{value}%")

            # 视频文件模式设置音量
            if self.video_cap:
                try:
                    # 确保音量值在有效范围内
                    volume_int = max(0, min(100, value))
                    # OpenCV 不处理音频，音量设置仅记录: audio_set_volume(volume_int)
                    logger.info(f"视频音量设置为: {volume_int}%")
                except Exception as e:
                    logger.info(f"设置音量时出错: {e}")
                    # 如果视频播放器出现问题，尝试重新初始化
                    if self.video_cap is not None and not self.video_cap.isOpened():
                        logger.info("视频播放器状态异常，尝试恢复...")

            # 设置TTS音乐音量
            if hasattr(self, 'tts') and self.tts and self.music_path:
                try:
                    # 确保音量值在有效范围内
                    tts_volume = max(0, min(100, value))
                    self.tts.set_video_volume(tts_volume)
                    logger.info(f"TTS音乐音量设置为: {tts_volume}%")
                except Exception as e:
                    logger.info(f"设置TTS音量时出错: {e}")

            # 修复：移除自动静音切换逻辑，避免循环触发
            # 静音状态应由用户明确控制，而不是自动切换
            self.update_ui_state()
            logger.info(f"音量设置为: {value}%")

        except Exception as e:
            logger.info(f"设置音量时发生严重错误: {e}")
            logger.info(traceback.format_exc())

    def toggle_mute(self):
        """切换静音状态 - 修复版本"""
        try:
            self.is_muted = not self.is_muted

            # 视频文件模式设置静音
            if self.video_cap:
                logger.info(f"静音状态: {'静音' if self.is_muted else '取消静音'} (OpenCV 不处理音频)")

            # TTS音乐静音处理
            if hasattr(self, 'tts') and self.tts and self.music_path:
                # 对于TTS，我们通过设置音量来实现静音效果
                if self.is_muted:
                    # 保存当前音量以便恢复
                    if not hasattr(self, '_previous_volume'):
                        self._previous_volume = self.volume
                    self.tts.set_video_volume(0)
                    logger.info("TTS音乐已静音")
                else:
                    # 恢复之前的音量
                    if hasattr(self, '_previous_volume'):
                        self.tts.set_video_volume(self._previous_volume)
                        logger.info(f"TTS音乐取消静音，恢复音量: {self._previous_volume}%")
                    else:
                        self.tts.set_video_volume(self.volume)
                        logger.info(f"TTS音乐取消静音，设置音量: {self.volume}%")

            self.update_ui_state()

        except Exception as e:
            logger.info(f"切换静音状态时出错: {e}")
            logger.info(traceback.format_exc())

    def capture_frame(self):
        """根据当前模式捕获一帧。返回 BGR 格式 (OpenCV 原生)。"""
        try:
            if self.current_mode == "camera" and self.cap is not None:
                ret, frame = self.cap.read()
                if ret:
                    return frame  # OpenCV 默认 BGR

            elif self.current_mode == "screen_capture" and self.sct is not None:
                screenshot = self.sct.grab(self.sct.monitors[1])  # 主显示器
                frame = np.array(screenshot)
                return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            elif self.current_mode == "region_capture" and self.sct is not None and self.selected_region is not None:
                screenshot = self.sct.grab(self.selected_region)
                frame = np.array(screenshot)
                return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

        except Exception as e:
            logger.info(f"捕获帧时出错: {e}")

        return None

    def update_frame(self):
        """更新视频帧和状态"""
        current_time = time.time()

        # 计算FPS
        self.frame_count += 1
        if current_time - self.last_fps_update >= 1.0:
            self.fps = self.frame_count / (current_time - self.last_fps_update)
            self.last_fps_update = current_time
            self.frame_count = 0
            self.fps_label.setText(f"FPS: {self.fps:.1f}")

        # 处理不同模式的帧更新
        if self.video_loaded and self.is_running:
            if self.current_mode in ["video_file", "template_file"] and self.video_cap:
                self.update_video_file_frame()
            else:
                self.update_capture_frame()

    def capture_video_widget_frame(self):
        """捕获视频显示区域的帧"""
        try:
            # 确保视频小部件可见且有效
            if not self.video_widget or not self.video_widget.isVisible():
                return None

            # 获取视频小部件的位置和大小
            widget_rect = self.video_widget.rect()
            if widget_rect.width() <= 0 or widget_rect.height() <= 0:
                return None

            # 创建QPixmap来捕获小部件的内容
            pixmap = QPixmap(widget_rect.size())
            painter = QPainter(pixmap)
            self.video_widget.render(painter)
            painter.end()

            # 将QPixmap转换为QImage
            qimage = pixmap.toImage()

            # 将QImage转换为OpenCV格式
            qimage = qimage.convertToFormat(QImage.Format.Format_RGB888)
            width = qimage.width()
            height = qimage.height()
            ptr = qimage.bits()
            ptr.setsize(qimage.sizeInBytes())

            # 转换为numpy数组
            frame = np.array(ptr).reshape((height, width, 3))

            return frame

        except Exception as e:
            logger.info(f"捕获视频小部件帧时出错: {str(e)}")
            return None

    def update_video_file_frame(self):
        """更新视频文件帧（OpenCV VideoCapture）"""
        if self.video_cap is None or not self.video_cap.isOpened():
            return

        try:
            # 从 OpenCV 读取一帧
            ret, frame = self.video_cap.read()

            if not ret:
                # 视频播放完毕
                if self.loop_playback:
                    logger.info("循环播放 -> 回到开头")
                    self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = self.video_cap.read()
                    if not ret:
                        self.stop()
                        return
                else:
                    logger.info("播放结束")
                    self.stop()
                    return

            # 获取当前播放位置（毫秒 → 秒）
            pos_msec = self.video_cap.get(cv2.CAP_PROP_POS_MSEC)
            if pos_msec > 0:
                self.last_position = pos_msec / 1000.0

                # 更新进度条
                if self.duration > 0:
                    progress = int((self.last_position / self.duration) * 1000)
                    self.progress_slider.setValue(progress)

                # 更新时间显示
                self.update_time_display(self.last_position)

            # BGR -> RGB（OpenCV 默认输出 BGR）
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # 显示到 Qt
            h, w, ch = frame_rgb.shape
            bytes_per_line = ch * w
            qt_image = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
            self.video_widget.setImage(qt_image)

            # 推送到虚拟摄像头（传 BGR 原始帧，NativeVCamBackend 内部做 BGR→RGB）
            if self.virtual_cam_initialized:
                self.send_frame_to_virtual_camera(frame)

        except Exception as e:
            logger.info(f"处理帧时出错: {e}")

    def update_capture_frame(self):
        """更新捕获模式的帧"""
        try:
            frame = self.capture_frame()  # BGR 格式
            if frame is not None:
                # 转换为QImage并显示（QImage 需要 RGB888）
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_frame.shape
                bytes_per_line = ch * w
                qt_image = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
                self.video_widget.setImage(qt_image)

                # 发送到虚拟摄像头 (BGR 格式)
                if self.virtual_cam_initialized:
                    self.send_frame_to_virtual_camera(frame)
        except Exception as e:
            logger.info(f"更新捕获帧时出错: {e}")

    def stop(self):
        """停止播放"""
        # 停止音乐播放（模板文件模式 - 图片模板 + 音乐）
        if (self.current_mode == "template_file" and
                self.template_path and
                self.template_path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')) and
                self.music_path and
                hasattr(self, 'tts') and self.tts and self.tts.is_file_playing()):
            self.tts.stop()
            self.close_virtual_camera()
            self.music_playing = False
            self.music_paused = False
            self.is_running = False
            self.main_window.status_bar.showMessage("音乐已停止")
            self.update_ui_state()
            logger.info("停止播放音乐")
            return

        # 其他模式的停止逻辑
        if not self.video_loaded:
            return

        try:
            self.is_running = False
            self.timer.stop()

            # 视频文件模式停止播放
            if self.video_cap:
                pass  # Timer 已停止，无需额外操作

            # 关闭虚拟摄像头
            self.close_virtual_camera()

            # 重置摄像头位置（如果是摄像头模式）
            if self.current_mode == "camera" and self.cap is not None:
                self.safe_close_player()

            self.update_time_display(0)
            self.progress_slider.setValue(0)
            self.last_position = 0
            self.main_window.status_bar.showMessage("已停止")
            self.update_ui_state()
            logger.info("播放已停止")
        except Exception as e:
            self.show_error("停止播放错误", f"停止播放时出错: {str(e)}")

    def safe_close_player(self):
        """安全关闭播放器 - 增强版本"""
        try:
            # 停止定时器
            if hasattr(self, 'timer') and self.timer.isActive():
                self.timer.stop()

            # 关闭虚拟摄像头
            self.close_virtual_camera()
            self.virtual_cam_width = 1920
            self.virtual_cam_height = 1080

            # 关闭视频捕获
            if hasattr(self, 'cap') and self.cap is not None:
                try:
                    self.cap.release()
                except:
                    pass
                self.cap = None

            # 关闭屏幕捕获
            if hasattr(self, 'sct') and self.sct is not None:
                try:
                    self.sct.close()
                except:
                    pass
                self.sct = None

            # 安全关闭视频播放器（OpenCV VideoCapture）
            if self.video_cap is not None:
                try:
                    self.video_cap.release()
                except Exception as e:
                    logger.info(f"释放视频播放器时出错: {e}")
                self.video_cap = None

            # 清除视频显示
            if hasattr(self, 'video_widget'):
                try:
                    self.video_widget.clear()
                except:
                    pass

            # 处理Qt事件队列
            QApplication.processEvents()

            # 重置状态变量
            self.duration = 0
            self.last_position = 0
            self.is_running = False
            self.video_loaded = False
            self.video_path = ""
            self.template_path = ""
            self.selected_region = None
            self.music_playing = False
            self.music_paused = False
            self.looping_restart = False

            # 清除图像数据
            if hasattr(self, 'image'):
                self.image = None

            # 清除之前的音量缓存
            if hasattr(self, '_previous_volume'):
                del self._previous_volume

            # 更新时间显示
            self.update_time_display(0)
            if hasattr(self, 'main_window') and hasattr(self.main_window, 'status_bar'):
                self.main_window.status_bar.showMessage("已停止")

            logger.info("播放器已安全关闭")

        except Exception as e:
            logger.info(f"安全关闭播放器时出错: {e}")
            logger.info(traceback.format_exc())

    def set_video_volume(self, value):
        """设置视频和音乐音量 - 统一管理"""
        try:
            # 更新音量值
            self.volume = max(0.0, min(1.0, value / 100.0))

            # 设置视频播放器音量
            if self.video_cap is not None:
                try:
                    # OpenCV 不处理音频，仅记录音量
                    volume_int = int(self.volume * 100)
                    # OpenCV 不处理音频，音量设置仅记录: audio_set_volume(volume_int)
                    logger.info(f"视频音量设置为: {volume_int}%")
                except Exception as e:
                    logger.info(f"设置音量时出错: {e}")

            # 设置TTS播放器的音乐音量（如果不是静音状态）
            if hasattr(self, 'tts') and self.tts and not self.is_muted:
                try:
                    self.tts.set_video_volume(value)
                    logger.info(f"音乐音量设置为: {value}%")
                except Exception as e:
                    logger.info(f"设置TTS音量时出错: {e}")

            # 更新UI显示
            if hasattr(self, 'volume_value_label'):
                self.volume_value_label.setText(f"{value}%")

        except Exception as e:
            logger.info(f"设置视频音量时出错: {str(e)}")

    def progress_pressed(self):
        """进度条按下事件"""
        self.is_seeking = True

    def progress_released(self):
        """进度条释放事件"""
        if not self.video_loaded or not self.video_cap:
            return

        try:
            # 计算目标时间位置（秒）
            position = self.progress_slider.value() / 1000.0 * self.duration

            # 跳转到指定位置（OpenCV 使用毫秒）
            self.video_cap.set(cv2.CAP_PROP_POS_MSEC, position * 1000)

            self.last_position = position
            self.update_time_display(position)
            self.is_seeking = False
            logger.info(f"跳转到位置: {position:.2f}秒")
        except Exception as e:
            self.show_error("跳转错误", f"跳转到指定位置时出错: {str(e)}")

    def progress_moved(self, value):
        """进度条移动事件"""
        try:
            # 更新时间显示
            position = value / 1000.0 * self.duration
            self.update_time_display(position)
        except Exception as e:
            logger.info(f"更新进度条时出错: {e}")

    def populate_resolution_combo(self, mode_key):
        """根据当前模式填充分辨率下拉框并恢复到该模式保存的选择"""
        # 所有模式共用的分辨率选项
        resolutions = [
            "1920×1080",
            "2048×1080",
            "2560×1080",
            "2560×1440",
            "3008×1440",
            "3200×1440",
            "3440×1440",
            "3840×2160",
        ]
        # 阻断信号避免触发 on_resolution_changed
        self.resolution_combo.blockSignals(True)
        self.resolution_combo.clear()
        self.resolution_combo.addItems(resolutions)

        # 恢复到该模式保存的分辨率
        saved = self.mode_resolutions.get(mode_key, (1920, 1080))
        target_text = f"{saved[0]}×{saved[1]}"
        idx = self.resolution_combo.findText(target_text)
        if idx >= 0:
            self.resolution_combo.setCurrentIndex(idx)
        else:
            self.resolution_combo.setCurrentText("1920×1080")

        self.resolution_combo.blockSignals(False)

    def on_resolution_changed(self, text):
        """输出分辨率选择变更 — 保存到当前模式并重新初始化虚拟摄像头"""
        try:
            w, h = map(int, text.replace("×", "x").split("x"))
            self.mode_resolutions[self.current_mode] = (w, h)
            logger.info(f"[{self.current_mode}] 输出分辨率切换: {w}×{h}")
            if self.virtual_cam_initialized:
                self.initialize_virtual_camera(w, h)
        except Exception as e:
            logger.info(f"分辨率切换失败: {e}")

    def update_time_display(self, position):
        """更新时间显示"""
        try:
            # 确保position和duration都是有效数值
            position = position or 0
            duration = self.duration or 0

            current_time = self.format_time(position)
            total_time = self.format_time(duration)

            self.current_time_label.setText(current_time)
            self.total_time_label.setText(total_time)
        except Exception as e:
            logger.info(f"更新时间显示时出错: {e}")
            self.current_time_label.setText("00:00:00")
            self.total_time_label.setText("00:00:00")

    def format_time(self, seconds):
        """格式化时间（秒 -> HH:MM:SS）"""
        try:
            # 确保seconds是有效数值
            seconds = seconds or 0
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            seconds = int(seconds % 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        except:
            return "00:00:00"

    def update_ui_state(self):
        """更新UI状态 - 修复版本"""
        try:
            # 模板文件模式 - 图片模板 + 音乐的特殊处理
            if (self.current_mode == "template_file" and
                    self.template_path and
                    self.template_path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')) and
                    self.music_path):

                # 根据音乐播放状态更新按钮
                if hasattr(self, 'tts') and self.tts:
                    try:
                        if self.tts.is_file_playing():
                            self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
                            self.play_btn.setToolTip("暂停音乐")
                            self.is_running = True
                            self.music_playing = True
                            self.music_paused = False
                        elif self.music_paused:
                            self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
                            self.play_btn.setToolTip("播放音乐")
                            self.is_running = False
                            self.music_playing = True
                        else:
                            self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
                            self.play_btn.setToolTip("播放音乐")
                            self.is_running = False
                            self.music_playing = False
                            self.music_paused = False
                    except Exception as e:
                        logger.info(f"检查TTS播放状态时出错: {e}")
                        # 如果检查状态失败，重置为默认状态
                        self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
                        self.play_btn.setToolTip("播放音乐")
                        self.is_running = False
                        self.music_playing = False
                        self.music_paused = False

                # 停止按钮状态
                self.stop_btn.setEnabled(self.music_playing or self.music_paused)

            else:
                # 其他模式的UI状态更新逻辑
                # 播放/暂停按钮
                if self.is_running:
                    self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
                    self.play_btn.setToolTip("暂停")
                else:
                    self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
                    self.play_btn.setToolTip("播放")

                # 停止按钮状态
                self.stop_btn.setEnabled(self.video_loaded and (self.is_running or self.last_position > 0))

            # 静音按钮
            if self.is_muted:
                self.mute_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolumeMuted))
                self.mute_btn.setToolTip("取消静音")
            else:
                self.mute_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
                self.mute_btn.setToolTip("静音")

        except Exception as e:
            logger.info(f"更新UI状态时出错: {e}")
            logger.info(traceback.format_exc())

    def show_error(self, title, message):
        """显示错误消息框"""
        QMessageBox.critical(self, title, message)

    def closeEvent(self, a0):
        """窗口关闭事件"""
        try:
            self.safe_close_player()
        except Exception as e:
            logger.info(f"关闭窗口时出错: {e}")
        finally:
            a0.accept()


class KeywordTab(QWidget):
    def __init__(self, main_window, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.main_window = main_window

        self.reply_settings = main_window.reply_settings
        self.keyword_responses = main_window.keyword_responses

        self.init_ui()


    def init_ui(self):
        layout = QVBoxLayout(self)

        # 标题
        title = QLabel("关键词管理")
        title_font = QFont("Arial", 14, QFont.Weight.Bold)
        title.setFont(title_font)
        layout.addWidget(title)

        # 搜索和添加区域
        search_layout = QHBoxLayout()

        self.keyword_search = QLineEdit()
        self.keyword_search.setPlaceholderText("搜索或添加关键词")
        self.keyword_search.setToolTip("输入关键词后按Enter键添加")
        self.keyword_search.textChanged.connect(self.filter_keywords)
        self.keyword_search.returnPressed.connect(self.add_keyword)
        search_layout.addWidget(self.keyword_search)

        self.add_btn = QPushButton("添加")
        self.add_btn.clicked.connect(self.add_keyword)
        self.add_btn.setShortcut("Return")  # 为按钮设置回车键快捷方式
        search_layout.addWidget(self.add_btn)

        layout.addLayout(search_layout)

        # 分割左右区域
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # 左侧关键词列表
        keyword_widget = QWidget()
        keyword_layout = QVBoxLayout(keyword_widget)

        keyword_title = QLabel("关键词列表:")
        keyword_layout.addWidget(keyword_title)

        self.keyword_list = QListWidget()
        self.keyword_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.keyword_list.customContextMenuRequested.connect(self.show_keyword_context_menu)
        self.keyword_list.itemClicked.connect(self.select_keyword)
        keyword_layout.addWidget(self.keyword_list, 1)

        splitter.addWidget(keyword_widget)

        # 右侧回复管理
        reply_widget = QWidget()
        reply_layout = QVBoxLayout(reply_widget)

        # 当前关键词显示
        reply_title_layout = QHBoxLayout()

        current_label = QLabel("当前关键词:")
        reply_title_layout.addWidget(current_label)

        self.current_keyword_label = QLabel("")
        self.current_keyword_label.setStyleSheet("color: #FF9900; font-weight: bold;")
        reply_title_layout.addWidget(self.current_keyword_label)

        self.edit_keyword_btn = QPushButton("编辑关键词")
        self.edit_keyword_btn.clicked.connect(self.edit_current_keyword)
        reply_title_layout.addWidget(self.edit_keyword_btn)

        self.delete_keyword_btn = QPushButton("删除关键词")
        self.delete_keyword_btn.clicked.connect(self.delete_current_keyword)
        reply_title_layout.addWidget(self.delete_keyword_btn)

        reply_title_layout.addStretch(1)
        reply_layout.addLayout(reply_title_layout)

        # 回复模式选择
        self.reply_mode_group = QButtonGroup()

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("回复模式:"))

        self.keyword_random_radio = QRadioButton("随机回复")
        self.keyword_random_radio.setChecked(True)
        self.reply_mode_group.addButton(self.keyword_random_radio)
        mode_layout.addWidget(self.keyword_random_radio)

        self.keyword_specific_radio = QRadioButton("指定回复")
        self.reply_mode_group.addButton(self.keyword_specific_radio)
        self.keyword_specific_radio.toggled.connect(self.toggle_specific_reply)
        mode_layout.addWidget(self.keyword_specific_radio)

        self.specific_dropdown = QComboBox()
        self.specific_dropdown.currentIndexChanged.connect(self.select_specific_reply)
        mode_layout.addWidget(self.specific_dropdown)

        reply_layout.addLayout(mode_layout)

        # 回复列表
        response_title = QLabel("关联的回复:")
        reply_layout.addWidget(response_title)

        self.response_list = QListWidget()
        self.response_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.response_list.customContextMenuRequested.connect(self.show_response_context_menu)
        self.response_list.itemDoubleClicked.connect(self.edit_response)
        reply_layout.addWidget(self.response_list, 1)

        # 添加回复区域
        add_res_layout = QHBoxLayout()

        self.new_response_input = QLineEdit()
        self.new_response_input.setPlaceholderText("输入回复内容...")
        self.new_response_input.returnPressed.connect(self.add_response)  # 添加回车键支持
        add_res_layout.addWidget(self.new_response_input, 1)

        add_response_btn = QPushButton("添加回复")
        add_response_btn.clicked.connect(self.add_response)
        add_response_btn.setShortcut("Return")  # 为按钮设置回车键快捷方式
        add_res_layout.addWidget(add_response_btn)

        reply_layout.addLayout(add_res_layout)

        splitter.addWidget(reply_widget)

        # 填充关键词列表
        self.refresh_keyword_list()

        # 初始禁用相关控件
        self.update_keyword_ui_state()




    def toggle_specific_reply(self, checked):
        """切换指定回复模式"""
        if not self.current_keyword:
            return

        # 更新回复设置
        self.reply_settings[self.current_keyword]["recover"] = not checked

        # 更新数据库
        key_id = self.keyword_responses[self.current_keyword]["keyId"]
        self.db.cursor.execute(
            "UPDATE keywords SET recover = ? WHERE id = ?",
            (not checked, key_id)
        )
        self.db.conn.commit()

    def select_specific_reply(self, index):
        """选择指定回复"""
        if not self.current_keyword:
            return

        keyword = self.current_keyword
        self.reply_settings[keyword]["specific_index"] = index

        # 更新数据库
        key_id = self.keyword_responses[keyword]["keyId"]
        self.db.cursor.execute(
            "UPDATE keywords SET specific_index = ? WHERE id = ?",
            (index, key_id)
        )
        self.db.conn.commit()

    def add_response(self):
        """为当前关键词添加回复"""
        try:
            if not self.current_keyword:
                QMessageBox.warning(self, "错误", "请先选择关键词")
                return

            response_text = self.new_response_input.text().strip()
            if not response_text:
                QMessageBox.warning(self, "错误", "请输入回复内容")
                return

            keyword = self.current_keyword

            # 确保数据结构完整
            if keyword not in self.keyword_responses:
                logger.error(f"关键词 '{keyword}' 不在缓存中，尝试重新加载")
                self.keyword_responses = self.load_keyword_responses()

            if keyword not in self.keyword_responses:
                logger.error(f"重新加载后关键词 '{keyword}' 仍然不在缓存中")
                QMessageBox.warning(self, "错误", "无法找到关键词数据")
                return

            # 确保存在responses列表
            keyword_data = self.keyword_responses[keyword]
            if "responses" not in keyword_data:
                logger.warning(f"关键词 '{keyword}' 缺少responses键，创建新列表")
                keyword_data["responses"] = []

            # 添加到数据库
            if not self.db.add_response(keyword, response_text):
                logger.error(f"数据库添加回复失败: {keyword} - {response_text}")
                QMessageBox.warning(self, "错误", "添加回复失败")
                return

            # 添加到应用数据结构
            keyword_data["responses"].append(response_text)

            # 刷新UI
            self.update_ui_for_current_keyword()

            # 清空输入框
            self.new_response_input.clear()

            logger.info(f"为关键词 {keyword} 添加回复: {response_text}")
            return True
        except Exception as e:
            logger.exception(f"添加回复时发生严重错误: {e}")
            QMessageBox.critical(self, "严重错误", f"添加回复时发生错误: {str(e)}")
            return False

    def show_keyword_context_menu(self, pos):
        """显示关键词右键菜单"""
        item = self.keyword_list.itemAt(pos)
        if not item:
            return

        menu = QMenu()
        edit_action = menu.addAction("编辑关键词")
        delete_action = menu.addAction("删除关键词")

        action = menu.exec(self.keyword_list.mapToGlobal(pos))

        if action == edit_action:
            self.edit_keyword(item)
        elif action == delete_action:
            self.delete_keyword(item)

    def show_response_context_menu(self, pos):
        """显示回复右键菜单 - 修复版"""
        item = self.response_list.itemAt(pos)
        if not item or not self.current_keyword:
            return

        menu = QMenu()
        edit_action = menu.addAction("编辑回复")
        delete_action = menu.addAction("删除回复")

        action = menu.exec(self.response_list.mapToGlobal(pos))

        if action == edit_action:
            self.edit_response(item)
        elif action == delete_action:
            self.delete_response(item)

    def edit_response(self, item):
        """编辑回复 - 修复版"""
        if not self.current_keyword:
            return

        # 获取回复索引和内容
        row = self.response_list.row(item)
        responses = self.keyword_responses[self.current_keyword]["responses"]
        old_response = responses[row]

        # 弹出编辑对话框
        new_response, ok = QInputDialog.getText(
            self,
            "编辑回复",
            "编辑回复内容:",
            QLineEdit.EchoMode.Normal,
            old_response
        )

        if ok and new_response:
            new_response = new_response.strip()
            if not new_response:
                QMessageBox.warning(self, "错误", "回复内容不能为空")
                return

            # 检查是否与现有回复重复
            if new_response in responses and new_response != old_response:
                QMessageBox.warning(self, "错误", "该回复内容已存在")
                return

            # 更新数据库
            if not self.db.update_response(self.current_keyword, old_response, new_response):
                QMessageBox.warning(self, "错误", "更新回复失败")
                return

            # 更新应用数据
            responses[row] = new_response

            # 更新列表显示
            item.setText(f"{row + 1}. {new_response}")

            # 更新指定回复下拉框
            self.specific_dropdown.setItemText(row, new_response)

            # 如果当前是指定回复模式且正在编辑的是选中的回复，更新设置
            if (self.keyword_specific_radio.isChecked() and
                    self.reply_settings[self.current_keyword]["specific_index"] == row):
                self.select_specific_reply(row)

            QMessageBox.information(self, "成功", "回复内容已更新")

    def delete_response(self, item):
        """删除回复 - 修复版"""
        if not self.current_keyword:
            return

        # 获取回复索引
        row = self.response_list.row(item)
        responses = self.keyword_responses[self.current_keyword]["responses"]
        response_text = responses[row]

        # 确认对话框
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除回复 '{response_text}' 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            # 从数据库删除
            if not self.db.delete_response(self.current_keyword, response_text):
                QMessageBox.warning(self, "错误", "删除回复失败")
                return

            # 从应用数据结构删除
            responses.pop(row)

            # 从回复列表中删除
            self.response_list.takeItem(row)

            # 更新后续项目编号
            for i in range(row, self.response_list.count()):
                item_text = self.response_list.item(i).text()
                # 提取纯文本内容（去掉编号）
                pure_text = item_text.split('. ', 1)[1] if '. ' in item_text else item_text
                self.response_list.item(i).setText(f"{i + 1}. {pure_text}")

            # 更新指定回复下拉框
            self.specific_dropdown.removeItem(row)

            # 更新回复设置索引
            current_index = self.reply_settings[self.current_keyword]["specific_index"]
            if current_index == row:
                # 删除的是当前选中的回复，重置为0
                new_index = 0
                self.reply_settings[self.current_keyword]["specific_index"] = new_index
                if self.specific_dropdown.count() > 0:
                    self.specific_dropdown.setCurrentIndex(new_index)
            elif current_index > row:
                # 删除的是前面的回复，索引减1
                new_index = current_index - 1
                self.reply_settings[self.current_keyword]["specific_index"] = new_index
                if self.specific_dropdown.count() > new_index:
                    self.specific_dropdown.setCurrentIndex(new_index)

            QMessageBox.information(self, "成功", "回复已删除")

    def update_keyword_ui_state(self):
        """根据当前状态更新UI元素可用性"""
        is_keyword_selected = bool(self.current_keyword)

        self.edit_keyword_btn.setEnabled(is_keyword_selected)
        self.delete_keyword_btn.setEnabled(is_keyword_selected)
        self.keyword_specific_radio.setEnabled(is_keyword_selected)
        self.specific_dropdown.setEnabled(is_keyword_selected)
        self.keyword_random_radio.setEnabled(is_keyword_selected)
        self.new_response_input.setEnabled(is_keyword_selected)
        self.response_list.setEnabled(is_keyword_selected)

        if not is_keyword_selected:
            self.response_list.clear()
            self.current_keyword_label.setText("")
            self.specific_dropdown.clear()

    def refresh_keyword_list(self):
        """刷新关键词列表"""
        self.keyword_list.clear()
        keywords = sorted(self.keyword_responses.keys())

        for keyword in keywords:
            item = QListWidgetItem(keyword)
            item.setData(Qt.ItemDataRole.UserRole, keyword)
            self.keyword_list.addItem(item)

        # 默认选中第一个关键词
        if self.keyword_list.count() > 0:
            self.keyword_list.setCurrentRow(0)
            self.select_keyword(self.keyword_list.item(0))
        else:
            self.current_keyword = None
            self.current_keyword_label.setText("")
            self.update_keyword_ui_state()

    def filter_keywords(self):
        """根据输入过滤关键词"""
        search_term = self.keyword_search.text().lower().strip()

        # 暂时断开信号，避免触发选择事件
        try:
            self.keyword_list.itemClicked.disconnect()
        except:
            pass

        self.keyword_list.clear()

        # 显示所有匹配关键词
        for keyword in sorted(self.keyword_responses.keys()):
            if search_term in keyword.lower():
                item = QListWidgetItem(keyword)
                item.setData(Qt.ItemDataRole.UserRole, keyword)
                self.keyword_list.addItem(item)

        # 重新连接信号
        self.keyword_list.itemClicked.connect(self.select_keyword)

        # 如果有关键词，默认选中第一个
        if self.keyword_list.count() > 0:
            self.keyword_list.setCurrentRow(0)
            self.select_keyword(self.keyword_list.item(0))
        else:
            self.current_keyword = None
            self.current_keyword_label.setText("")
            self.update_keyword_ui_state()

    def add_keyword(self):
        """添加新关键词"""
        keyword = self.keyword_search.text().strip()
        if not keyword:
            QMessageBox.warning(self, "输入错误", "请输入关键词")
            return

        # 检查关键词是否已存在
        if keyword in self.keyword_responses:
            QMessageBox.information(self, "提示", "关键词已存在")
            return

        # 添加到数据库
        key_id = self.db.add_keyword(keyword)
        if key_id is None:
            QMessageBox.warning(self, "错误", "添加关键词失败")
            return

        # 添加到应用
        self.keyword_responses[keyword] = {"keyId": key_id, "responses": []}
        self.reply_settings[keyword] = {"recover": True, "specific_index": 0}

        # 刷新显示
        self.refresh_keyword_list()

        # 清空搜索框
        self.keyword_search.clear()

        QMessageBox.information(self, "成功", f"已添加关键词: {keyword}")

    def edit_keyword(self, item):
        """编辑关键词（右键菜单触发）"""
        keyword = item.data(Qt.ItemDataRole.UserRole)
        self.edit_keyword_name(keyword)

    def edit_current_keyword(self):
        """编辑当前关键词"""
        if not self.current_keyword:
            QMessageBox.warning(self, "错误", "请先选择关键词")
            return

        self.edit_keyword_name(self.current_keyword)

    def edit_keyword_name(self, keyword):
        """编辑关键词名称"""
        new_keyword, ok = QInputDialog.getText(
            self,
            "编辑关键词",
            "请输入新的关键词名称:",
            QLineEdit.EchoMode.Normal,
            keyword
        )
        if ok and new_keyword:
            new_keyword = new_keyword.strip()
            if not new_keyword or new_keyword == keyword:
                return

            # 检查新关键词是否已存在
            if new_keyword in self.keyword_responses:
                QMessageBox.warning(self, "错误", "关键词已存在")
                return

            # 更新数据库
            if not self.db.update_keyword(keyword, new_keyword):
                QMessageBox.warning(self, "错误", "更新关键词失败")
                return

            # 更新应用数据结构
            self.keyword_responses[new_keyword] = self.keyword_responses.pop(keyword)
            self.reply_settings[new_keyword] = self.reply_settings.pop(keyword)

            # 更新当前关键词
            if self.current_keyword == keyword:
                self.current_keyword = new_keyword
                self.current_keyword_label.setText(new_keyword)

            # 刷新关键词列表
            self.refresh_keyword_list()

            QMessageBox.information(self, "成功", f"已更新关键词: {keyword} → {new_keyword}")

    def delete_keyword(self, item):
        """删除关键词（右键菜单触发）"""
        keyword = item.data(Qt.ItemDataRole.UserRole)
        self.delete_keyword_by_name(keyword)

    def delete_current_keyword(self):
        """删除当前关键词"""
        if not self.current_keyword:
            QMessageBox.warning(self, "错误", "请先选择关键词")
            return

        self.delete_keyword_by_name(self.current_keyword)

    def delete_keyword_by_name(self, keyword):
        """根据名称删除关键词"""
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除关键词 '{keyword}' 及其所有关联回复吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            # 从数据库删除
            if not self.db.delete_keyword(keyword):
                QMessageBox.warning(self, "错误", "删除关键词失败")
                return

            # 从应用数据结构中删除
            if keyword in self.keyword_responses:
                del self.keyword_responses[keyword]
            if keyword in self.reply_settings:
                del self.reply_settings[keyword]

            # 更新界面状态
            if self.current_keyword == keyword:
                self.current_keyword = None
                self.current_keyword_label.setText("")
                self.response_list.clear()
                self.update_keyword_ui_state()

            # 刷新关键词列表
            self.refresh_keyword_list()

            QMessageBox.information(self, "成功", f"已删除关键词: {keyword}")

    def select_keyword(self, item):
        """选择关键词并显示关联回复"""
        keyword = item.data(Qt.ItemDataRole.UserRole)
        self.current_keyword = keyword
        self.current_keyword_label.setText(keyword)
        self.update_keyword_ui_state()

        # 刷新回复列表
        self.response_list.clear()
        responses = self.keyword_responses[keyword].get("responses", [])
        for i, response in enumerate(responses):
            self.response_list.addItem(f"{i + 1}. {response}")

        # 设置回复模式
        mode = self.reply_settings[keyword]["recover"]
        self.keyword_random_radio.setChecked(mode)
        self.keyword_specific_radio.setChecked(not mode)

        # 更新指定回复下拉框
        self.specific_dropdown.clear()
        for response in responses:
            self.specific_dropdown.addItem(response)

        # 设置当前选择的回复索引
        specific_index = self.reply_settings[keyword]["specific_index"]
        if 0 <= specific_index < self.specific_dropdown.count():
            self.specific_dropdown.setCurrentIndex(specific_index)

    def update_ui_for_current_keyword(self):
        """更新当前关键词的UI"""
        if not self.current_keyword:
            return

        keyword = self.current_keyword

        # 刷新回复列表
        self.refresh_response_list()

        # 更新指定回复下拉框
        self.update_specific_dropdown()

        # 设置回复模式
        if keyword in self.reply_settings:
            mode = self.reply_settings[keyword]["recover"]
            self.keyword_random_radio.setChecked(mode)
            self.keyword_specific_radio.setChecked(not mode)

    def refresh_response_list(self):
        """刷新回复列表"""
        if not self.current_keyword:
            return

        self.response_list.clear()
        keyword = self.current_keyword

        # 安全访问responses
        responses = self.keyword_responses.get(keyword, {}).get("responses", [])
        for i, response in enumerate(responses):
            self.response_list.addItem(f"{i + 1}. {response}")

    def update_specific_dropdown(self):
        """更新指定回复下拉框"""
        if not self.current_keyword:
            return

        keyword = self.current_keyword
        self.specific_dropdown.clear()

        # 安全访问responses
        responses = self.keyword_responses.get(keyword, {}).get("responses", [])
        for response in responses:
            self.specific_dropdown.addItem(response)

        # 设置当前选择的回复索引
        if keyword in self.reply_settings:
            specific_index = self.reply_settings[keyword].get("specific_index", 0)
            if 0 <= specific_index < self.specific_dropdown.count():
                self.specific_dropdown.setCurrentIndex(specific_index)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.danmu_client = None
        self.voice_list = None
        self.setWindowTitle("直播助理")
        self.setWindowIcon(QIcon("icon/0nbum-0m212-001.ico"))
        self.resize(800, 600)

        # 初始化数据库管理器
        self.db = SQLiteManager()
        # 弹幕队列，最大100
        self.log_messages = collections.deque(maxlen=100)

        # 应用状态
        logger.info("初始化应用状态")
        self.current_keyword = None
        self.keyword_responses = self.load_keyword_responses()
        self.reply_settings = self.load_reply_settings()
        self.test_voice_in_progress = False  # 添加测试语音状态标志
        logger.info("初始化应用状态完成")

        # 确保初始状态同步
        self.idle_time = 60  # 默认60秒
        self.enable_idle_commentary = False  # 默认关闭
        if hasattr(self, 'idle_enable_check'):
            self.idle_enable_check.setChecked(self.enable_idle_commentary)

        # 游戏解说相关状态
        self.last_danmu_time = time.time()  # 初始化为当前时间
        self.idle_timer = QTimer()  # 冷场检测定时器
        self.idle_timer.timeout.connect(self.check_idle_time)
        self.idle_timer.start(5000)  # 每5秒检查一次
        self.commentary_queue = []  # 解说内容队列
        self.is_playing_commentary = False  # 标记是否正在播放解说



        # 摄像头相关状态
        self.capture_thread = None
        self.camera_settings = {
            'add_noise': False,
            'random_artifacts': False,
            'vary_brightness': False,
            'add_focus_changes': False,
            'output_virtual_cam': True,
            'random_zoom': False
        }

        # 防检测滤镜 (仅作用于虚拟摄像头输出)
        self.anti_detection_filter = AntiDetectionFilter(self.camera_settings)

        # 区域捕获相关状态
        self.region_selector = None
        self.selected_region = None

        # 创建UI更新器
        logger.info("初始化UI更新器")
        self.ui_updater = UIUpdater(self)
        logger.info("UI更新器初始化完成")

        # 初始化语音管理器
        logger.info("初始化语音管理器")
        self.tts = TsVoice()
        logger.info("语音管理器初始化完成")

        self.tts.playback_finished.connect(self.on_audio_playback_finished)

        # 知识库相关（延迟初始化）
        self.kb_rag = None
        self.kb_initialized = False
        self.kb_lock = threading.Lock()

        self.init_ui()
        self.start_async_initialization()


    def init_ui(self):
        # 创建主窗口布局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # 创建主应用界面
        self.main_widget = QWidget()
        main_app_layout = QVBoxLayout(self.main_widget)
        main_app_layout.setSpacing(5)
        main_app_layout.setContentsMargins(5, 5, 5, 5)

        # 创建标签页
        self.tabs = QTabWidget()
        main_app_layout.addWidget(self.tabs, 1)

        # 添加标签页（保持不变）
        self.tabs.addTab(self.create_main_tab(), "主控制台")
        self.tabs.addTab(self.create_keyword_tab(), "关键词管理")
        self.tabs.addTab(self.create_awkward_tab(), "游戏解说话术")
        self.tabs.addTab(self.create_voice_tab(), "语音设置")
        self.tabs.addTab(self.create_template_tab(), "模板")
        self.tabs.addTab(self.create_knowledge_base_tab(), "知识库")

        main_layout.addWidget(self.main_widget, 1)
        logger.info("主窗口UI初始化完成")

        # 创建状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # 左侧状态标签
        self.status_label = QLabel("就绪")
        self.status_bar.addWidget(self.status_label)

        # 语音初始化
        self.asyn_voice_initialization()

    def add_log(self, message, color="white"):
        """添加日志并限制最多显示100条记录"""
        # 颜色映射
        color_map = {
            "white": "#FFFFFF",
            "green": "#90EE90",
            "cyan": "#00FFFF",
            "yellow": "#FFFF00",
            "magenta": "#FF00FF",
            "orange": "#FFA500",
            "red": "#FF6B6B"
        }

        # 获取当前滚动条位置
        scrollbar = self.chat_widget.chat_display.verticalScrollBar()
        is_at_bottom = scrollbar.value() == scrollbar.maximum()

        # 添加新消息到队列
        formatted_message = f'<span style="color:{color_map.get(color, "#FFFFFF")}">{message}</span>'
        self.log_messages.append(formatted_message)

        # 更新QTextEdit内容
        self.chat_widget.chat_display.setHtml("<br>".join(self.log_messages))

        # 如果之前滚动条在底部，保持滚动到底部
        if is_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def load_reply_settings(self):
        """从数据库加载回复设置"""
        try:
            keywords = self.db.get_keywords()
            if not keywords:
                return {}

            settings = {}
            for keyword in keywords:
                settings[keyword["keyword"]] = {
                    "recover": keyword["recover"],
                    "specific_index": keyword["specific_index"]
                }

            return settings
        except Exception as e:
            logger.error(f"加载回复设置失败: {e}")
            return {}

    def load_keyword_responses(self):
        """从数据库加载关键词回复数据"""
        try:
            keywords = self.db.get_keywords()
            if not keywords:
                return {}

            keyword_responses = {}
            for keyword in keywords:
                key_id = keyword["id"]
                responses = self.db.get_keyword_responses(key_id)
                resp_list = [r["response"] for r in responses]

                keyword_responses[keyword["keyword"]] = {
                    "keyId": key_id,
                    "responses": resp_list
                }

            return keyword_responses
        except Exception as e:
            logger.error(f"加载关键词回复失败: {e}")
            return {}

    def start_async_initialization(self):

        # 启动弹幕接收定时器
        self.danmu_receive_timer = QTimer()
        self.danmu_receive_timer.timeout.connect(self.chat_widget.receive_danmu)
        self.danmu_receive_timer.start(2000)  # 每2秒模拟收到一条弹幕




    def create_main_tab(self):
        # 主布局
        tab = QWidget()
        layout = QHBoxLayout(tab)

        # 左侧播放器
        self.video_widget = VideoPlayer(self, self.tts)
        layout.addWidget(self.video_widget, 2)

        # 右侧聊天窗口
        self.chat_widget = ChatWidget(self, self.tts)
        layout.addWidget(self.chat_widget, 1)

        return tab

    def create_keyword_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)
        keyword_widget = KeywordTab(self, self.db)
        layout.addWidget(keyword_widget)
        # 这里添加关键词管理的UI组件
        return tab

    def asyn_voice_initialization(self):
        """异步语音初始化"""
        self.tts.configure(
            model_dir="models\\SparkTTS",
            prompt_dir="example\\prompt"
        )

        example_file = self.voice_combo.currentText()
        if example_file:
            logger.info(f"语音初始化示例文件: {example_file}")
            self.tts.set_prompt(example_file)

        self.voice_signal_conn()

    def voice_signal_conn(self):
        """语音信号连接"""
        # 信号连接到状态栏更新
        self.tts.initialization_started.connect(lambda: self.status_bar.showMessage("语音初始化中..."))
        self.tts.initialization_finished.connect(self.on_tts_initialized)
        self.tts.synthesis_started.connect(lambda text: self.status_bar.showMessage(f"开始合成: {text[:20]}..."))
        self.tts.synthesis_completed.connect(self.on_synthesis_completed)
        self.tts.playback_started.connect(lambda text, _: self.status_bar.showMessage(f"开始播放: {text[:20]}..."))
        self.tts.playback_finished.connect(lambda text, _: self.status_bar.showMessage(f"播放完成: {text[:20]}..."))
        self.tts.error_occurred.connect(lambda msg: self.status_bar.showMessage(f"错误: {msg}"))
        self.tts.queue_updated.connect(self.on_queue_updated)

    def on_tts_initialized(self, success: bool, message: str):
        """TTS初始化完成回调"""
        if success:
            self.status_bar.showMessage("语音初始化完成")
        else:
            self.status_bar.showMessage(f"语音初始化失败: {message}")

    def on_synthesis_completed(self, text: str, success: bool, message: str):
        """合成完成回调"""
        if success:
            self.status_bar.showMessage(f"合成完成: {text[:20]}...")
        else:
            self.status_bar.showMessage(f"合成失败: {message}")

    def on_queue_updated(self, synth_count: int, playback_count: int):
        """队列更新回调"""
        self.status_bar.showMessage(f"TTS 队列 - 待合成: {synth_count}, 待播放: {playback_count}")


    def create_template_tab(self):
        """创建模板展示标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # 顶部操作栏
        top_bar = QHBoxLayout()

        add_btn = QPushButton("添加模板")
        add_btn.setStyleSheet("background-color: #1e90ff; color: white;")
        top_bar.addWidget(add_btn)

        del_btn = QPushButton("删除模板")
        del_btn.setStyleSheet("background-color: #ff4500; color: white;")
        top_bar.addWidget(del_btn)

        layout.addLayout(top_bar)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 内容容器
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(20)

        # 模板网格布局（每行5个）
        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setHorizontalSpacing(15)

        self.template_items = []
        self.template_view = {}

        # 图片模板
        img_dir = "example\\templates\\img"
        if os.path.exists(img_dir):
            for img_file in os.listdir(img_dir):
                if img_file.lower().endswith((".png", ".jpg", ".jpeg")):
                    self.template_view[os.path.join(img_dir, img_file)] = "image"
                    self._add_template_item(grid, os.path.join(img_dir, img_file), "image")

        # 视频模板
        video_dir = "example\\templates\\video"
        if os.path.exists(video_dir):
            for video_file in os.listdir(video_dir):
                if video_file.lower().endswith((".mp4", ".avi", ".mov")):
                    self.template_view[os.path.join(video_dir, video_file)] = "video"
                    self._add_template_item(grid, os.path.join(video_dir, video_file), "video")

        # 连接按钮信号
        add_btn.clicked.connect(self._on_add_template)
        del_btn.clicked.connect(self._on_delete_template)

        container_layout.addLayout(grid)
        container_layout.addStretch()   # ✅ 保持内部模板居顶，但不会把整个scroll往上顶
        scroll.setWidget(container)

        layout.addWidget(scroll)

        return tab


    # ==================== 知识库标签页 ====================

    def create_knowledge_base_tab(self):
        """创建知识库管理标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # ---------- 状态组 ----------
        status_group = QGroupBox("知识库状态")
        status_layout = QVBoxLayout(status_group)
        status_layout.setSpacing(5)

        self.kb_status_label = QLabel("状态: 未初始化")
        self.kb_doc_count_label = QLabel("文档数: -")
        self.kb_vector_count_label = QLabel("向量数: -")
        status_layout.addWidget(self.kb_status_label)
        status_layout.addWidget(self.kb_doc_count_label)
        status_layout.addWidget(self.kb_vector_count_label)

        layout.addWidget(status_group)

        # ---------- 操作组 ----------
        op_group = QGroupBox("知识库操作")
        op_layout = QHBoxLayout(op_group)
        op_layout.setSpacing(10)

        self.kb_build_btn = QPushButton("构建知识库")
        self.kb_build_btn.clicked.connect(self._on_kb_build)

        self.kb_clear_btn = QPushButton("清空知识库")
        self.kb_clear_btn.clicked.connect(self._on_kb_clear)

        self.kb_add_btn = QPushButton("添加文档")
        self.kb_add_btn.clicked.connect(self._on_kb_add_document)

        op_layout.addWidget(self.kb_build_btn)
        op_layout.addWidget(self.kb_clear_btn)
        op_layout.addWidget(self.kb_add_btn)

        layout.addWidget(op_group)

        # ---------- 查询组 ----------
        query_group = QGroupBox("知识库查询")
        query_layout = QVBoxLayout(query_group)
        query_layout.setSpacing(5)

        self.kb_query_input = QTextEdit()
        self.kb_query_input.setPlaceholderText("请输入您的问题...")
        self.kb_query_input.setMaximumHeight(80)
        query_layout.addWidget(self.kb_query_input)

        self.kb_query_btn = QPushButton("查询")
        self.kb_query_btn.clicked.connect(self._on_kb_query)
        query_layout.addWidget(self.kb_query_btn)

        layout.addWidget(query_group)

        # ---------- 结果组 ----------
        result_group = QGroupBox("查询结果")
        result_layout = QVBoxLayout(result_group)

        self.kb_response_display = QTextEdit()
        self.kb_response_display.setReadOnly(True)
        self.kb_response_display.setPlaceholderText("查询结果将显示在这里...")
        result_layout.addWidget(self.kb_response_display)

        layout.addWidget(result_group)

        # ---------- 文档列表组 ----------
        doc_group = QGroupBox("数据文件")
        doc_layout = QVBoxLayout(doc_group)

        self.kb_doc_list = QListWidget()
        doc_layout.addWidget(self.kb_doc_list)

        layout.addWidget(doc_group)

        return tab

    def _ensure_kb_initialized(self, callback=None):
        """延迟初始化 RAG 系统，在工作线程中加载模型"""
        if self.kb_initialized and self.kb_rag is not None:
            if callback:
                callback(True, "知识库已就绪")
            return

        def init_worker():
            with self.kb_lock:
                if self.kb_initialized and self.kb_rag is not None:
                    QTimer.singleShot(0, lambda: callback(True, "知识库已就绪") if callback else None)
                    return
                try:
                    # 延迟导入：仅在用户首次使用知识库时加载重型依赖
                    from duix.llama_index_rag import OptimizedRAGSystem
                    QTimer.singleShot(0, lambda: self._set_kb_status("正在初始化知识库（加载模型）..."))
                    self.kb_rag = OptimizedRAGSystem()
                    self.kb_initialized = True

                    # 尝试加载现有索引
                    if self.kb_rag.index is not None:
                        QTimer.singleShot(0, lambda: self._set_kb_status("就绪 - 已加载现有索引"))
                    else:
                        QTimer.singleShot(0, lambda: self._set_kb_status("就绪 - 等待构建索引"))

                    QTimer.singleShot(0, self._refresh_kb_stats)
                    if callback:
                        QTimer.singleShot(0, lambda: callback(True, "知识库初始化完成"))
                except Exception as e:
                    err_msg = str(e)
                    logger.error(f"知识库初始化失败: {err_msg}")
                    QTimer.singleShot(0, lambda: self._set_kb_status(f"初始化失败: {err_msg}"))
                    if callback:
                        QTimer.singleShot(0, lambda msg=err_msg: callback(False, msg))

        thread = threading.Thread(target=init_worker, daemon=True)
        thread.start()

    def _on_kb_build(self):
        """构建知识库"""
        self._ensure_kb_initialized(callback=lambda success, msg: self._do_kb_build() if success else self._set_kb_status(msg))

    def _do_kb_build(self):
        """实际执行构建（确保已初始化后）"""
        self.kb_build_btn.setEnabled(False)
        self._set_kb_status("正在构建索引...")

        def build_worker():
            try:
                with self.kb_lock:
                    success = self.kb_rag.build_index(force_rebuild=True, interactive=False)
                QTimer.singleShot(0, lambda: self._on_kb_build_finished(success))
            except Exception as e:
                err = str(e)
                logger.error(f"构建知识库失败: {err}")
                QTimer.singleShot(0, lambda: self._on_kb_build_finished(False, err))

        thread = threading.Thread(target=build_worker, daemon=True)
        thread.start()

    def _on_kb_build_finished(self, success, error_msg=None):
        """构建知识库完成回调"""
        self.kb_build_btn.setEnabled(True)
        if success:
            self._set_kb_status("索引构建完成")
            self._refresh_kb_stats()
        else:
            self._set_kb_status(f"构建失败: {error_msg or '未知错误'}")

    def _on_kb_clear(self):
        """清空知识库"""
        reply = QMessageBox.question(
            self, "确认清空", "确定要清空知识库吗？\n此操作将删除所有向量索引数据。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._ensure_kb_initialized(callback=lambda success, msg: self._do_kb_clear() if success else self._set_kb_status(msg))

    def _do_kb_clear(self):
        """实际执行清空"""
        self.kb_clear_btn.setEnabled(False)
        self._set_kb_status("正在清空知识库...")

        def clear_worker():
            try:
                with self.kb_lock:
                    result = self.kb_rag.clear_knowledge_base(delete_files=False)
                QTimer.singleShot(0, lambda: self._on_kb_clear_finished(result))
            except Exception as e:
                err = str(e)
                logger.error(f"清空知识库失败: {err}")
                QTimer.singleShot(0, lambda: self._on_kb_clear_finished({"success": False, "message": err}))

        thread = threading.Thread(target=clear_worker, daemon=True)
        thread.start()

    def _on_kb_clear_finished(self, result):
        """清空知识库完成回调"""
        self.kb_clear_btn.setEnabled(True)
        if result.get("success"):
            self._set_kb_status("知识库已清空")
            self._refresh_kb_stats()
        else:
            self._set_kb_status(f"清空失败: {result.get('message', '未知错误')}")

    def _on_kb_add_document(self):
        """添加文档到知识库"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择文档", "",
            "文档文件 (*.txt *.md *.pdf *.docx *.doc);;所有文件 (*)"
        )
        if not file_path:
            return

        self._ensure_kb_initialized(callback=lambda success, msg: self._do_kb_add(file_path) if success else self._set_kb_status(msg))

    def _do_kb_add(self, file_path):
        """实际执行添加文档"""
        self.kb_add_btn.setEnabled(False)
        self._set_kb_status("正在添加文档...")
        filename = os.path.basename(file_path)
        data_dir = self.kb_rag.data_dir if hasattr(self.kb_rag, 'data_dir') else "duix/data"
        dest = os.path.join(data_dir, filename)

        def add_worker():
            try:
                # 复制文件到数据目录
                shutil.copy2(file_path, dest)
                logger.info(f"文档已复制到: {dest}")

                # 添加到索引
                with self.kb_lock:
                    success = self.kb_rag.add_document(filename)
                QTimer.singleShot(0, lambda: self._on_kb_add_finished(success, filename))
            except Exception as e:
                err = str(e)
                logger.error(f"添加文档失败: {err}")
                QTimer.singleShot(0, lambda: self._on_kb_add_finished(False, filename, err))

        thread = threading.Thread(target=add_worker, daemon=True)
        thread.start()

    def _on_kb_add_finished(self, success, filename, error_msg=None):
        """添加文档完成回调"""
        self.kb_add_btn.setEnabled(True)
        if success:
            self._set_kb_status(f"文档 '{filename}' 添加成功")
            self._refresh_kb_stats()
        else:
            self._set_kb_status(f"添加失败: {error_msg or '未知错误'}")

    def _on_kb_query(self):
        """执行知识库查询"""
        question = self.kb_query_input.toPlainText().strip()
        if not question:
            self.kb_response_display.setText("请输入查询问题")
            return

        callback = lambda success, msg: (  # noqa: E731
            self._do_kb_query(question) if success
            else self._set_kb_response(f"知识库未就绪: {msg}")
        )
        self._ensure_kb_initialized(callback=callback)

    def _do_kb_query(self, question):
        """实际执行查询"""
        self.kb_query_btn.setEnabled(False)
        self.kb_response_display.setText("正在查询...")

        def query_worker():
            try:
                with self.kb_lock:
                    response = self.kb_rag.query(question, use_context=True)
                QTimer.singleShot(0, lambda: self._on_kb_query_finished(response))
            except Exception as e:
                err = str(e)
                logger.error(f"查询失败: {err}")
                QTimer.singleShot(0, lambda: self._on_kb_query_finished(f"查询失败: {err}"))

        thread = threading.Thread(target=query_worker, daemon=True)
        thread.start()

    def _on_kb_query_finished(self, response):
        """查询完成回调"""
        self.kb_query_btn.setEnabled(True)
        self.kb_response_display.setText(response)

    def _refresh_kb_stats(self):
        """刷新知识库统计信息"""
        if self.kb_rag is None:
            self.kb_status_label.setText("状态: 未初始化")
            self.kb_doc_count_label.setText("文档数: -")
            self.kb_vector_count_label.setText("向量数: -")
            self.kb_doc_list.clear()
            return

        try:
            stats = self.kb_rag.get_knowledge_base_stats()

            # 状态
            if stats.get("has_vector_index"):
                self.kb_status_label.setText("状态: 就绪（已索引）")
            else:
                self.kb_status_label.setText("状态: 就绪（未索引）")

            # 文档数
            doc_count = stats.get("data_files_count", 0)
            self.kb_doc_count_label.setText(f"文档数: {doc_count}")

            # 向量数
            vector_count = stats.get("vector_documents_count", "N/A")
            self.kb_vector_count_label.setText(f"向量数: {vector_count}")

            # 数据文件列表
            self.kb_doc_list.clear()
            data_files = stats.get("data_files", [])
            if data_files:
                for f in data_files:
                    self.kb_doc_list.addItem(f)
            else:
                self.kb_doc_list.addItem("（数据目录为空）")

        except Exception as e:
            logger.error(f"刷新知识库统计失败: {e}")

    def _set_kb_status(self, text):
        """设置知识库状态标签和状态栏"""
        self.kb_status_label.setText(f"状态: {text}")
        self.status_bar.showMessage(f"知识库: {text}")

    def _set_kb_response(self, text):
        """设置查询结果显示"""
        self.kb_response_display.setText(text)

    def _on_add_template(self):
        """添加模板"""
        # 文件选择对话框
        file_dialog = QFileDialog()
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        file_dialog.setNameFilter("媒体文件 (*.png *.jpg *.jpeg *.mp4 *.avi *.mov)")

        if file_dialog.exec():
            selected_files = file_dialog.selectedFiles()

            # 根据文件类型分类
            for file_path in selected_files:
                if file_path.lower().endswith((".png", ".jpg", ".jpeg")):
                    dest_dir = "example/templates/img"
                elif file_path.lower().endswith((".mp4", ".avi", ".mov")):
                    dest_dir = "example/templates/video"
                else:
                    continue

                # 创建目标目录（如果不存在）
                os.makedirs(dest_dir, exist_ok=True)

                # 复制文件
                file_name = os.path.basename(file_path)
                dest_path = os.path.join(dest_dir, file_name)

                try:
                    shutil.copy2(file_path, dest_path)

                    # 刷新模板列表
                    self.tabs.removeTab(5)  # 移除旧标签页
                    self.tabs.addTab(self.create_template_tab(), "模板")  # 添加新标签页

                    QMessageBox.information(self, "成功", f"模板 {file_name} 添加成功")
                except Exception as e:
                    QMessageBox.warning(self, "错误", f"添加模板失败: {str(e)}")

    def _on_delete_template(self):
        """删除模板"""
        # 获取当前选中的模板项
        selected_items = []
        for item in self.template_items:
            if hasattr(item, "isSelected") and item.isSelected():
                selected_items.append(item)

        if not selected_items:
            QMessageBox.warning(self, "提示", "请先选择要删除的模板")
            return

        # 确认删除
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除选中的 {len(selected_items)} 个模板吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            for item in selected_items:
                try:
                    # 从文件系统中删除
                    file_path = item.file_path  # 假设模板项保存了文件路径
                    os.remove(file_path)

                    # 从界面中移除
                    self.template_items.remove(item)
                    item.setParent(None)

                except Exception as e:
                    QMessageBox.warning(self, "错误", f"删除模板失败: {str(e)}")

            # 刷新模板列表
            self.tabs.removeTab(5)
            self.tabs.addTab(self.create_template_tab(), "模板")

    def _add_template_item(self, grid, file_path, template_type):
        """添加单个模板项到网格布局"""
        row = len(self.template_items) // 4
        col = len(self.template_items) % 4

        # 创建模板项控件
        item = QWidget()
        item_layout = QVBoxLayout(item)

        # 预览图(720*1280比例)
        preview = QLabel()
        preview.setFixedSize(150, 266)  # 720/1280=0.5625, 150/0.5625≈266
        preview.setStyleSheet("background-color: #333; border: 5px solid #555;")

        if template_type == "image":
            # 图片预览(填充满预览框)
            pixmap = QPixmap(file_path).scaled(preview.width(), preview.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
            preview.setPixmap(pixmap)
        else:
            # 视频第一帧预览(填充满预览框)
            cap = cv2.VideoCapture(file_path)
            ret, frame = cap.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                height, width, _ = frame.shape
                bytes_per_line = 3 * width
                q_img = QImage(frame.data, width, height, bytes_per_line, QImage.Format.Format_RGB888)
                pixmap = QPixmap.fromImage(q_img).scaled(preview.width(), preview.height(),
                                                         Qt.AspectRatioMode.IgnoreAspectRatio,
                                                         Qt.TransformationMode.SmoothTransformation)
                preview.setPixmap(pixmap)
            cap.release()

        # 模板名称（限制8个字符）
        name = os.path.splitext(os.path.basename(file_path))[0]
        if len(name) > 8:
            name = name[:8] + "..."
        name_label = QLabel(name)
        name_label.setStyleSheet("color: white; font-size: 12px;")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setToolTip(os.path.splitext(os.path.basename(file_path))[0])

        # 添加到布局
        item_layout.addWidget(preview)
        item_layout.addWidget(name_label)

        # 点击事件
        item.mousePressEvent = lambda e, path=file_path, type=template_type: self._on_template_clicked(path, type)

        # 添加到网格
        grid.addWidget(item, row, col)
        self.template_items.append(item)

    def _on_template_clicked(self, file_path, template_type):
        """模板点击事件"""
        # 创建详情对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("模板详情")
        dialog.resize(600, 500)

        layout = QVBoxLayout(dialog)

        # 预览区域
        preview = QLabel()
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if template_type == "image":
            # 图片预览
            pixmap = QPixmap(file_path).scaled(500, 300, Qt.AspectRatioMode.KeepAspectRatio)
            preview.setPixmap(pixmap)
        else:
            # 视频预览
            self.video_cap = QLabel()
            self.video_cap.setFixedSize(500, 300)

            cap = cv2.VideoCapture(file_path)
            ret, frame = cap.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                height, width, _ = frame.shape
                bytes_per_line = 3 * width
                q_img = QImage(frame.data, width, height, bytes_per_line, QImage.Format.Format_RGB888)
                pixmap = QPixmap.fromImage(q_img).scaled(500, 300, Qt.AspectRatioMode.KeepAspectRatio)
                preview.setPixmap(pixmap)
            cap.release()

        layout.addWidget(preview)

        # 添加预览原图按钮
        preview_btn = QPushButton("预览原图" if template_type == "image" else "预览原视频")
        preview_btn.clicked.connect(lambda: self._on_preview_original(file_path, template_type))
        layout.addWidget(preview_btn)

        # 模板信息
        name = os.path.splitext(os.path.basename(file_path))[0]
        info_label = QLabel(f"模板名称: {name}\n类型: {template_type}")
        info_label.setStyleSheet("font-size: 14px; color: white;")
        layout.addWidget(info_label)

        # 声音选择
        sound_layout = QHBoxLayout()
        sound_label = QLabel("背景音乐:")
        sound_label.setStyleSheet("color: white;")
        sound_layout.addWidget(sound_label)

        self.sound_combo = QComboBox()
        music_dir = "example/templates/music"
        if os.path.exists(music_dir):
            for music_file in os.listdir(music_dir):
                if music_file.lower().endswith((".mp3", ".wav")):
                    # 为每个音乐文件添加试听按钮
                    widget = QWidget()
                    hbox = QHBoxLayout(widget)
                    hbox.setContentsMargins(0, 0, 0, 0)

                    label = QLabel(music_file)
                    hbox.addWidget(label)

                    preview_btn = QPushButton("试听")
                    preview_btn.setFixedWidth(60)
                    # preview_btn.clicked.connect(
                    #     lambda _, path=os.path.join(music_dir, music_file): self._on_preview_music(path))
                    # hbox.addWidget(preview_btn)

                    self.sound_combo.addItem(music_file)
                    self.sound_combo.setItemData(self.sound_combo.count() - 1, widget, Qt.ItemDataRole.UserRole)
                    self.sound_combo.setItemDelegate(QStyledItemDelegate())

        sound_layout.addWidget(self.sound_combo, 1)

        layout.addLayout(sound_layout)

        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        dialog.exec()

    def _on_preview_original(self, file_path, template_type):
        """预览原图或原视频"""
        preview_dialog = QDialog(self)
        preview_dialog.setWindowTitle("预览原文件")
        preview_dialog.resize(300, 500)  # 限制高度为500像素

        layout = QVBoxLayout(preview_dialog)

        if template_type == "image":
            # 显示原图（等比例缩放，高度限制为500像素）
            pixmap = QPixmap(file_path)
            scaled_pixmap = pixmap.scaledToHeight(500, Qt.TransformationMode.SmoothTransformation)
            label = QLabel()
            label.setPixmap(scaled_pixmap)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        else:
            # 播放原视频
            self.video_cap = QLabel()
            self.video_cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.video_cap)

            # 使用OpenCV播放视频
            self.cap = cv2.VideoCapture(file_path)
            self.timer = QTimer()
            self.timer.timeout.connect(lambda: self._update_video_frame(self.video_cap))
            self.timer.start(30)  # 30ms更新一帧

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(preview_dialog.close)
        layout.addWidget(close_btn)

        preview_dialog.exec()

        # 清理视频资源
        if template_type != "image" and hasattr(self, 'cap'):
            self.cap.release()
            self.timer.stop()

    def _update_video_frame(self, label):
        """更新视频帧"""
        ret, frame = self.cap.read()
        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width, _ = frame.shape
            bytes_per_line = 3 * width
            q_img = QImage(frame.data, width, height, bytes_per_line, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(q_img)
            label.setPixmap(pixmap.scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio))
        else:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # 循环播放

    def on_playback_complete(self):
        """播放完成时触发"""
        # 重置播放标记
        self.is_playing_commentary = False

        # 更新最后活动时间，防止连续播报
        self.last_danmu_time = time.time()

    def toggle_idle_setting(self, enabled):
        """切换冷场设置状态 - 修复输入框启用状态"""
        try:
            # 更新状态变量
            self.enable_idle_commentary = enabled

            # 更新UI反馈
            # status = "开启" if enabled else "关闭"
            # self.add_log(f"[系统] 冷场播报功能已{status}", "white")

            # 确保UI同步
            self.idle_enable_check.setChecked(enabled)

            # 输入框始终保持可编辑状态，不受冷场功能开关影响
            self.idle_time_input.setEnabled(True)

            # 如果开启，立即检查一次冷场状态
            if enabled:
                QTimer.singleShot(1000, self.check_idle_time)
        except Exception as e:
            logger.error(f"切换冷场设置出错: {str(e)}")
            self.add_log(f"[错误] 冷场设置切换失败: {str(e)}", "red")


    def create_awkward_tab(self):
        """创建游戏解说话术管理标签页 - 优化版"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # 标题
        title = QLabel("游戏解说话术管理")
        title_font = QFont("Arial", 14, QFont.Weight.Bold)
        title.setFont(title_font)
        layout.addWidget(title)

        # +++ 冷场设置 +++
        idle_settings_layout = QHBoxLayout()

        # 冷场功能开关
        self.idle_enable_check = QCheckBox("开启冷场播报")
        self.idle_enable_check.setChecked(False)
        # self.idle_enable_check.stateChanged.connect(
        #     lambda state: self.toggle_idle_setting(state == Qt.CheckState.Checked.value)
        # )
        idle_settings_layout.addWidget(self.idle_enable_check)

        # 设置冷场时间
        idle_settings_layout.addWidget(QLabel("冷场时间(秒):"))

        # 冷场时间输入框
        self.idle_time_input = QLineEdit()
        self.idle_time_input.setPlaceholderText("输入5-300秒")
        self.idle_time_input.setFixedWidth(100)
        self.idle_time_input.setValidator(QIntValidator(2, 300))
        self.idle_time_input.setText("30")
        self.idle_time_input.returnPressed.connect(self.confirm_idle_time_and_blur)
        self.idle_time_input.setEnabled(True)

        # 添加焦点事件处理
        self.idle_time_input.mousePressEvent = self.on_idle_time_input_clicked
        idle_settings_layout.addWidget(self.idle_time_input)

        # 确认按钮
        self.idle_confirm_btn = QPushButton("确认")
        self.idle_confirm_btn.setFixedWidth(60)
        self.idle_confirm_btn.clicked.connect(self.confirm_idle_time_and_blur)
        idle_settings_layout.addWidget(self.idle_confirm_btn)

        # 状态标签
        self.idle_time_status = QLabel("当前: 60秒")
        self.idle_time_status.setStyleSheet("color: #4CAF50;")
        idle_settings_layout.addWidget(self.idle_time_status)

        idle_settings_layout.addStretch(1)
        layout.addLayout(idle_settings_layout)

        # 播报模式设置
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("播报模式:"))

        self.playback_mode_group = QButtonGroup()

        self.commentary_random_radio = QRadioButton("随机播报")
        self.commentary_random_radio.setChecked(True)
        self.playback_mode_group.addButton(self.commentary_random_radio)
        mode_layout.addWidget(self.commentary_random_radio)

        self.commentary_specific_radio = QRadioButton("指定播放")
        self.playback_mode_group.addButton(self.commentary_specific_radio)
        mode_layout.addWidget(self.commentary_specific_radio)

        self.event_combo = QComboBox()
        self.event_combo.setMinimumWidth(150)
        self.event_combo.setEnabled(False)
        mode_layout.addWidget(self.event_combo)

        layout.addLayout(mode_layout)

        # 添加区域
        add_layout = QHBoxLayout()

        self.event_input = QLineEdit()
        self.event_input.setPlaceholderText("事件名称")
        self.event_input.returnPressed.connect(self.add_commentary)
        add_layout.addWidget(self.event_input)

        self.phrase_input = QLineEdit()
        self.phrase_input.setPlaceholderText("解说内容")
        self.phrase_input.returnPressed.connect(self.add_commentary)
        add_layout.addWidget(self.phrase_input)

        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self.add_commentary)
        add_layout.addWidget(add_btn)

        layout.addLayout(add_layout)

        # 搜索区域
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索事件或解说内容...")
        self.search_input.returnPressed.connect(self.search_commentary)
        search_layout.addWidget(self.search_input)

        search_btn = QPushButton("搜索")
        search_btn.clicked.connect(self.search_commentary)
        search_layout.addWidget(search_btn)

        clear_btn = QPushButton("清除")
        clear_btn.clicked.connect(self.clear_search)
        search_layout.addWidget(clear_btn)

        layout.addLayout(search_layout)

        # 解说列表 - 使用表格样式
        self.commentary_tree = QTreeWidget()
        self.commentary_tree.setHeaderLabels(["ID", "事件", "解说内容"])

        # 设置列宽
        self.commentary_tree.setColumnWidth(0, 60)  # ID列
        self.commentary_tree.setColumnWidth(1, 150)  # 事件列
        self.commentary_tree.setColumnWidth(2, 400)  # 解说内容列

        # 设置表格样式
        self.commentary_tree.setStyleSheet("""
            QTreeWidget {
                background-color: #2D2D30;
                border: 1px solid #3F3F46;
                border-radius: 5px;
                outline: 0;
            }
            QTreeWidget::item {
                height: 30px;
                border-bottom: 1px solid #3F3F46;
                padding: 5px;
            }
            QTreeWidget::item:selected {
                background-color: #3E3E42;
                color: white;
            }
            QTreeWidget::item:hover {
                background-color: #3E3E42;
            }
            QHeaderView::section {
                background-color: #252526;
                color: #CCCCCC;
                padding: 5px;
                border: 1px solid #3F3F46;
                font-weight: bold;
            }
        """)

        # 设置自动换行
        self.commentary_tree.setWordWrap(True)
        self.commentary_tree.setAlternatingRowColors(True)

        self.commentary_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.commentary_tree.customContextMenuRequested.connect(self.show_context_menu)
        self.commentary_tree.itemDoubleClicked.connect(self.play_selected_commentary)

        layout.addWidget(self.commentary_tree, 1)

        # 状态标签
        self.commentary_status_label = QLabel("就绪")
        layout.addWidget(self.commentary_status_label)

        # 填充解说列表
        self.refresh_commentary_list()

        # 连接信号
        self.commentary_specific_radio.toggled.connect(self.toggle_event_selection)
        self.idle_enable_check.stateChanged.connect(
            lambda state: self.toggle_idle_setting(state == Qt.CheckState.Checked.value)
        )

        return tab

    def refresh_commentary_list(self):
        """刷新解说列表 - 优化显示"""
        self.commentary_tree.clear()
        self.commentary_status_label.setText("加载解说数据中...")
        QApplication.processEvents()

        try:
            # 从数据库获取数据
            commentary_data = self.db.get_commentary()

            # 使用自动递增序号
            sequence_number = 1

            # 按事件分组
            events = {}
            for item in commentary_data:
                event = item['event']
                phrase = item['phrase']
                item_id = item['id']

                if event not in events:
                    events[event] = []
                events[event].append((item_id, phrase))

            # 添加到表格视图
            for event, phrases in events.items():
                # 为每个解说内容创建单独的行
                for item_id, phrase in phrases:
                    item = QTreeWidgetItem([
                        str(sequence_number),  # 自动递增序号
                        event,
                        phrase
                    ])

                    # 存储原始数据
                    item.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": "phrase",
                        "id": item_id,
                        "event": event,
                        "phrase": phrase,
                        "sequence": sequence_number
                    })

                    # 设置工具提示
                    item.setToolTip(2, phrase)
                    item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)

                    self.commentary_tree.addTopLevelItem(item)
                    sequence_number += 1

            # 更新状态标签
            total_events = len(events)
            total_phrases = len(commentary_data)
            self.commentary_status_label.setText(f"已加载 {total_events} 个事件，共 {total_phrases} 条解说")

            # 填充事件选择下拉框
            self.populate_event_combo()

        except Exception as e:
            self.commentary_status_label.setText(f"加载数据失败: {str(e)}")
            logger.error(f"刷新解说列表失败: {str(e)}")

    def populate_event_combo(self):
        """填充事件选择下拉框"""
        self.event_combo.clear()
        self.event_combo.addItem("选择事件...")

        # 获取所有唯一的事件名称
        events = set()
        for i in range(self.commentary_tree.topLevelItemCount()):
            item = self.commentary_tree.topLevelItem(i)
            event_name = item.text(1)
            events.add(event_name)

        # 按字母顺序排序
        for event_name in sorted(events):
            self.event_combo.addItem(event_name)

    def toggle_event_selection(self, checked):
        """切换事件选择下拉框的状态"""
        self.event_combo.setEnabled(checked)

    def get_next_commentary_item(self):
        """获取下一个要播放的解说内容项 - 优化随机选择"""
        try:
            # 如果是指定播放模式且选择了特定事件
            if (self.commentary_specific_radio.isChecked() and
                    self.event_combo.currentIndex() > 0 and
                    self.event_combo.isEnabled()):

                selected_event = self.event_combo.currentText()
                event_phrases = []

                # 收集该事件下的所有解说内容
                for i in range(self.commentary_tree.topLevelItemCount()):
                    item = self.commentary_tree.topLevelItem(i)
                    if item.text(1) == selected_event:
                        event_phrases.append(item)

                if event_phrases:
                    # 从该事件中随机选择一条解说
                    return random.choice(event_phrases)

            # 随机播放模式：从所有解说中随机选择
            all_phrases = []
            for i in range(self.commentary_tree.topLevelItemCount()):
                all_phrases.append(self.commentary_tree.topLevelItem(i))

            if all_phrases:
                return random.choice(all_phrases)

            return None

        except Exception as e:
            logger.error(f"选择解说内容出错: {str(e)}")
            return None

    def check_idle_time(self):
        """检查冷场状态，触发空闲解说"""
        if not self.enable_idle_commentary:
            return
        if self.is_playing_commentary:
            return
        current_time = time.time()
        elapsed = current_time - self.last_danmu_time
        if elapsed > self.idle_time:
            logger.info(f"检测到冷场 {elapsed:.0f}秒，触发空闲解说")
            self.play_idle_commentary()

    def play_idle_commentary(self):
        """播放空闲解说内容 - 优化版"""
        try:
            # 标记开始播放解说
            self.is_playing_commentary = True

            # 选择要播放的内容
            selected_item = self.get_next_commentary_item()

            if selected_item:
                # 获取解说内容
                item_data = selected_item.data(0, Qt.ItemDataRole.UserRole)
                phrase_content = item_data["phrase"]

                # 获取播放模式信息
                mode = "随机" if self.commentary_random_radio.isChecked() else "指定"
                event_info = f" ({item_data['event']})" if self.commentary_specific_radio.isChecked() else ""

                # 直接播放
                prompt_path = self.tts.get_current_prompt()
                self.tts.add_text(phrase_content, prompt_speech_path=prompt_path)

                # 更新最后活动时间
                self.last_danmu_time = time.time()

                # 记录日志
                self.add_log(f"[解说] {mode}播放{event_info}: {phrase_content[:50]}...", "yellow")
                self.status_label.setText(f"{mode}播放{event_info}: {phrase_content[:50]}...")

                # 高亮显示当前播放的项
                self.highlight_playing_item(selected_item)

        except Exception as e:
            logger.error(f"冷场播报出错: {str(e)}")
            self.add_log(f"[错误] 冷场播报失败: {str(e)}", "red")
        finally:
            # 确保状态被重置
            QTimer.singleShot(1000, lambda: setattr(self, 'is_playing_commentary', False))

    def highlight_playing_item(self, item):
        """高亮显示正在播放的项"""
        # 清除之前的高亮
        for i in range(self.commentary_tree.topLevelItemCount()):
            top_item = self.commentary_tree.topLevelItem(i)
            top_item.setBackground(0, QColor(53, 53, 53))
            top_item.setBackground(1, QColor(53, 53, 53))
            top_item.setBackground(2, QColor(53, 53, 53))

        # 设置新的高亮
        item.setBackground(0, QColor(42, 130, 218, 100))
        item.setBackground(1, QColor(42, 130, 218, 100))
        item.setBackground(2, QColor(42, 130, 218, 100))

        # 滚动到该项
        self.commentary_tree.scrollToItem(item, QTreeWidget.ScrollHint.PositionAtCenter)

    def on_audio_playback_finished(self, text):
        """音频播放完成处理 - 优化版"""
        # 重置冷场播报状态

        self.update_status(f"播放完成: {text[:20]}...")
        self.is_playing_commentary = False

        # 清除高亮
        for i in range(self.commentary_tree.topLevelItemCount()):
            item = self.commentary_tree.topLevelItem(i)
            item.setBackground(0, QColor(53, 53, 53))
            item.setBackground(1, QColor(53, 53, 53))
            item.setBackground(2, QColor(53, 53, 53))

        # 检查冷场状态
        if self.enable_idle_commentary:
            current_time = time.time()
            elapsed = current_time - self.last_danmu_time
            if elapsed > self.idle_time:
                # 短暂延迟后再次检查，避免连续播放
                QTimer.singleShot(2000, self.check_idle_time)

    def show_context_menu(self, position):
        """显示右键菜单 - 优化版"""
        play_action, edit_action, delete_action = None, None, None,
        selected_items = self.commentary_tree.selectedItems()
        if not selected_items:
            return

        item = selected_items[0]
        item_data = item.data(0, Qt.ItemDataRole.UserRole)

        # 创建菜单
        menu = QMenu()

        if item_data["type"] == "phrase":
            # 解说内容菜单项
            play_action = menu.addAction("▶️ 播放解说")
            edit_action = menu.addAction("✏️ 编辑解说")
            delete_action = menu.addAction("🗑️ 删除解说")

        # 通用菜单项
        test_random_action = menu.addAction("🎲 测试随机播放")
        test_specific_action = menu.addAction("🎯 测试指定播放")

        # 执行菜单操作
        action = menu.exec(self.commentary_tree.viewport().mapToGlobal(position))

        if not action:
            return

        try:
            if action == test_random_action:
                self.test_random_playback()
            elif action == test_specific_action:
                self.test_specific_playback()
            elif item_data["type"] == "phrase":
                if action == play_action:
                    self.play_commentary(item)
                elif action == edit_action:
                    self.edit_commentary(item)
                elif action == delete_action:
                    self.delete_commentary(item)

        except Exception as e:
            logger.error(f"执行菜单操作失败: {str(e)}")

    def test_random_playback(self):
        """测试随机播放功能"""
        if not self.commentary_tree.topLevelItemCount():
            QMessageBox.information(self, "提示", "没有可用的解说内容")
            return

        # 切换到随机模式
        self.commentary_random_radio.setChecked(True)
        selected_item = self.get_next_commentary_item()
        if selected_item:
            item_data = selected_item.data(0, Qt.ItemDataRole.UserRole)
            self.add_log(f"[测试] 随机选择: {item_data['event']} - {item_data['phrase'][:50]}...", "cyan")
            self.play_commentary(selected_item)

    def test_specific_playback(self):
        """测试指定播放功能"""
        if not self.commentary_tree.topLevelItemCount():
            QMessageBox.information(self, "提示", "没有可用的解说内容")
            return

        if self.event_combo.currentIndex() <= 0:
            QMessageBox.information(self, "提示", "请先选择一个事件")
            return

        # 切换到指定模式
        self.commentary_specific_radio.setChecked(True)

        # 获取指定事件的随机解说
        selected_item = self.get_next_commentary_item()
        if selected_item:
            item_data = selected_item.data(0, Qt.ItemDataRole.UserRole)
            self.add_log(f"[测试] 指定播放: {item_data['event']} - {item_data['phrase'][:50]}...", "cyan")
            self.play_commentary(selected_item)


    def delete_commentary(self, phrase_item):
        """删除单个解说内容 - 修复删除逻辑"""
        try:
            item_data = phrase_item.data(0, Qt.ItemDataRole.UserRole)
            if not item_data:
                QMessageBox.warning(self, "错误", "无法获取解说数据")
                return

            commentary_id = item_data["id"]
            phrase_content = item_data["phrase"]
            event_name = item_data["event"]

            short_phrase = phrase_content[:50] + "..." if len(phrase_content) > 50 else phrase_content

            reply = QMessageBox.question(
                self,
                "确认删除",
                f"确定要删除解说内容吗？\n\n事件: {event_name}\n内容: {short_phrase}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                # 从数据库删除
                if self.db.delete_commentary(commentary_id):
                    # 直接从树中移除该项（现在是顶级项）
                    index = self.commentary_tree.indexOfTopLevelItem(phrase_item)
                    if index >= 0:
                        self.commentary_tree.takeTopLevelItem(index)

                    # # 更新状态
                    # self.status_label.setText(f"解说内容已删除: {short_phrase}")
                    # self.add_log(f"[系统] 已删除解说: {short_phrase}", "orange")

                    # 重新编号剩余项
                    self.renumber_commentary_items()
                else:
                    QMessageBox.warning(self, "错误", "删除解说内容失败")

        except Exception as e:
            logger.error(f"删除解说内容失败: {str(e)}")
            QMessageBox.critical(self, "错误", f"删除解说内容时发生错误: {str(e)}")

    def renumber_commentary_items(self):
        """重新为解说项编号"""
        for i in range(self.commentary_tree.topLevelItemCount()):
            item = self.commentary_tree.topLevelItem(i)
            # 更新显示序号
            item.setText(0, str(i + 1))
            # 更新存储的序号
            item_data = item.data(0, Qt.ItemDataRole.UserRole)
            if item_data:
                item_data["sequence"] = i + 1
                item.setData(0, Qt.ItemDataRole.UserRole, item_data)

    def resizeEvent(self, event):
        """窗口大小改变时调整列宽"""
        super().resizeEvent(event)
        # 保持列宽比例：ID占1/6，事件占1/6，内容占2/3
        if hasattr(self, 'commentary_tree') and self.commentary_tree:
            total_width = self.commentary_tree.width()
            header = self.commentary_tree.header()
            header.resizeSection(0, int(total_width * 1 / 6))  # ID列
            header.resizeSection(1, int(total_width * 1 / 6))  # 事件列
            # 内容列自动占用剩余空间

    def delete_event(self, event_item):
        """删除整个事件 - 修复删除逻辑"""
        event_name = event_item.text(1)

        # 确认对话框
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除整个事件 '{event_name}' 及其所有解说内容吗？\n\n"
            f"此操作将删除 {event_item.childCount()} 条解说内容，且无法恢复！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No  # 默认选择"No"
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                # 从数据库删除
                if self.db.delete_event(event_name):
                    # 从界面移除
                    index = self.commentary_tree.indexOfTopLevelItem(event_item)
                    if index >= 0:
                        self.commentary_tree.takeTopLevelItem(index)

                    # 更新状态
                    self.status_label.setText(f"事件 '{event_name}' 已删除")
                    self.add_log(f"[系统] 已删除事件: {event_name}", "orange")

                    # 刷新事件下拉框
                    self.populate_event_combo()
                else:
                    QMessageBox.warning(self, "错误", "删除事件失败，请检查数据库连接")

            except Exception as e:
                logger.error(f"删除事件失败: {str(e)}")
                QMessageBox.critical(self, "错误", f"删除事件时发生错误: {str(e)}")


    def on_idle_time_input_clicked(self, event):
        """冷场时间输入框点击事件 - 聚焦并选中文本"""
        # 调用父类方法确保正常行为
        super(QLineEdit, self.idle_time_input).mousePressEvent(event)
        # 选中所有文本
        QTimer.singleShot(0, lambda: self.idle_time_input.selectAll())


    def confirm_idle_time_and_blur(self):
        """确认冷场时间设置并失去焦点"""
        try:
            # 获取输入文本
            time_str = self.idle_time_input.text().strip()

            # 空值处理
            if not time_str:
                # 失去焦点
                self.idle_time_input.clearFocus()
                return

            # 转换为整数
            time_value = int(time_str)

            # 范围验证
            if 2 <= time_value <= 300:
                self.idle_time = time_value
                self.idle_time_status.setText(f"当前: {time_value}秒")
                self.idle_time_status.setStyleSheet("color: #4CAF50;")
                self.add_log(f"[系统] 冷场时间已设置为: {time_value}秒", "green")

                # 确认成功后失去焦点
                self.idle_time_input.clearFocus()

                # 如果冷场功能未开启，恢复禁用状态
                if not self.enable_idle_commentary:
                    QTimer.singleShot(100, lambda: self.idle_time_input.setEnabled(False))
            else:
                # 恢复之前的值
                self.idle_time_input.setText(str(self.idle_time))
                QMessageBox.warning(self, "输入错误", "请输入1-300之间的秒数")
                # 保持焦点以便用户继续编辑
                self.idle_time_input.setFocus()
                self.idle_time_input.selectAll()

        except ValueError:
            # 恢复之前的值
            self.idle_time_input.setText(str(self.idle_time))
            QMessageBox.warning(self, "输入错误", "请输入有效的数字")
            # 保持焦点以便用户继续编辑
            self.idle_time_input.setFocus()
            self.idle_time_input.selectAll()


    def confirm_idle_time(self):
        """原有的确认方法（供其他地方调用）"""
        self.confirm_idle_time_and_blur()


    def on_idle_time_input_focus_out(self, event):
        """冷场时间输入框失去焦点事件 - 确认设置"""
        super(QLineEdit, self.idle_time_input).focusOutEvent(event)
        self.confirm_idle_time()


    def set_initial_focus(self):
        """设置初始焦点到解说内容输入框"""
        self.phrase_input.setFocus()


    def play_selected_commentary(self, item, column):
        """双击播放解说 - 优化版"""
        try:
            item_data = item.data(0, Qt.ItemDataRole.UserRole)

            if item_data["type"] == "phrase":
                # 检查是否正在播放
                if self.tts and self.tts.is_tts_playing():
                    # 如果正在播放，添加到队列
                    self.add_to_play_queue(item)
                    logger.info(f"[系统] 已添加到播放队列: {item_data['phrase'][:50]}...")
                    self.add_log(f"[系统] 已添加到播放队列: {item_data['phrase'][:50]}...", "yellow")
                else:
                    # 直接播放
                    self.play_commentary(item)

            elif item_data["type"] == "event":
                # 事件双击显示/隐藏子项
                if item.isExpanded():
                    item.setExpanded(False)
                else:
                    item.setExpanded(True)

        except Exception as e:
            logger.error(f"双击播放失败: {str(e)}")

    def add_to_play_queue(self, phrase_item):
        """添加单个解说到播放队列"""
        item_data = phrase_item.data(0, Qt.ItemDataRole.UserRole)
        phrase_content = item_data["phrase"]

        self.commentary_queue.append(phrase_content)
        logger.info(f"[系统] 已添加到播放队列: {phrase_content[:50]}...")
        self.add_log(f"[系统] 已添加到播放队列: {phrase_content[:50]}...", "green")

    def add_all_to_play_queue(self, event_item):
        """添加事件下的所有解说到播放队列"""
        event_name = event_item.text(1)
        logger.info(f"event_name: {event_name}")
        phrases = []

        for i in range(event_item.childCount()):
            child = event_item.child(i)
            item_data = child.data(0, Qt.ItemDataRole.UserRole)
            phrases.append(item_data["phrase"])

        if not phrases:
            QMessageBox.information(self, "提示", "该事件下没有解说内容")
            return

        self.commentary_queue.extend(phrases)
        logger.info(f"[系统] 已添加 {len(phrases)} 条解说到播放队列")
        self.add_log(f"[系统] 已添加 {len(phrases)} 条解说到播放队列", "green")

    def play_commentary(self, phrase_item):
        """播放选中的解说词 - 优化版"""
        try:
            # 获取解说内容
            item_data = phrase_item.data(0, Qt.ItemDataRole.UserRole)
            phrase_content = item_data["phrase"]
            event_name = item_data["event"]

            # 检查TTS是否就绪
            if not self.tts or not self.tts.is_initialized:
                QMessageBox.warning(self, "警告", "语音系统未就绪，请检查TTS设置")
                return

            # 检查是否正在播放
            if self.tts.is_tts_playing():
                reply = QMessageBox.question(
                    self,
                    "正在播放",
                    "当前正在播放其他内容，是否添加到播放队列？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.add_to_play_queue(phrase_item)
                    return

            # 播放解说
            prompt_path = self.tts.get_current_prompt()
            self.tts.add_text(phrase_content, prompt_speech_path=prompt_path)

            # 更新状态和日志
            short_phrase = phrase_content[:50] + "..." if len(phrase_content) > 50 else phrase_content
            self.add_log(f"[解说] 播放: {event_name} - {short_phrase}", "yellow")
            self.status_label.setText(f"正在播放: {short_phrase}")

            # 高亮显示当前播放的项
            self.highlight_playing_item(phrase_item)

        except Exception as e:
            logger.error(f"播放解说失败: {str(e)}")
            QMessageBox.critical(self, "错误", f"播放失败: {str(e)}")


    def add_commentary(self):
        """添加解说词"""
        event = self.event_input.text().strip()
        phrase = self.phrase_input.text().strip()

        if not event or not phrase:
            QMessageBox.warning(self, "输入错误", "请填写事件名称和解说内容")
            return

        # 添加到数据库
        result = self.db.add_commentary(event, phrase)

        if result is None:
            QMessageBox.warning(self, "添加失败", "该事件和解说组合已存在")
            return
        elif result:
            # 刷新列表
            self.refresh_commentary_list()
            # self.status_label.setText(f"已添加解说: {event} - {phrase[:30]}...")

            # 清空输入
            self.event_input.clear()
            self.phrase_input.clear()

            # 自动聚焦到事件输入框
            self.event_input.setFocus()
        else:
            QMessageBox.warning(self, "错误", "添加解说词失败")

    def rename_event(self, event_item):
        """重命名事件"""
        current_name = event_item.text(1)

        # 创建编辑对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("重命名事件")
        dialog.setFixedSize(400, 150)

        layout = QVBoxLayout(dialog)

        # 事件名称输入框
        name_label = QLabel("新事件名称:")
        name_input = QLineEdit()
        name_input.setText(current_name)

        layout.addWidget(name_label)
        layout.addWidget(name_input)

        # 按钮区域
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        # 显示对话框
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_name = name_input.text().strip()
            if new_name and new_name != current_name:
                # 更新数据库
                if self.db.update_event_name(current_name, new_name):
                    # 刷新列表
                    self.refresh_commentary_list()
                    self.status_label.setText(f"事件 '{current_name}' 已重命名为 '{new_name}'")
                else:
                    QMessageBox.warning(self, "错误", "更新事件名称失败，可能已存在相同事件名")

    def edit_commentary(self, phrase_item):
        """编辑解说内容"""
        # 获取当前数据
        item_data = phrase_item.data(0, Qt.ItemDataRole.UserRole)
        commentary_id = item_data["id"]
        current_phrase = item_data["phrase"]

        # 创建编辑对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑解说内容")
        dialog.setFixedSize(500, 200)

        layout = QVBoxLayout(dialog)

        # 事件名称显示（只读）
        event_label = QLabel(f"事件: {item_data['event']}")
        layout.addWidget(event_label)

        # 解说内容输入框
        phrase_label = QLabel("解说内容:")
        phrase_input = QTextEdit()
        phrase_input.setPlainText(current_phrase)

        layout.addWidget(phrase_label)
        layout.addWidget(phrase_input)

        # 按钮区域
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        # 显示对话框
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_phrase = phrase_input.toPlainText().strip()
            if new_phrase and new_phrase != current_phrase:
                # 更新数据库
                if self.db.update_commentary(commentary_id, new_phrase=new_phrase):
                    # 刷新列表
                    self.refresh_commentary_list()
                    self.status_label.setText(f"解说内容已更新: {new_phrase[:30]}...")
                else:
                    QMessageBox.warning(self, "错误", "更新解说内容失败")


    def play_all_commentary(self, event_item):
        """播放事件下的所有解说"""
        # 获取事件下的所有解说
        event_name = event_item.text(1)
        phrases = []
        for i in range(event_item.childCount()):
            child = event_item.child(i)
            item_data = child.data(0, Qt.ItemDataRole.UserRole)
            phrases.append(item_data["phrase"])

        if not phrases:
            QMessageBox.information(self, "提示", "该事件下没有解说内容")
            return

        # 播放所有解说
        full_text = "。".join(phrases)
        self.tts.add_text(full_text)

        # 显示播放状态
        self.status_label.setText(f"正在播放 '{event_name}' 下的所有解说")

    def search_commentary(self):
        """搜索解说词"""
        search_text = self.search_input.text().strip()
        if not search_text:
            self.refresh_commentary_list()
            return

        try:
            # 搜索事件和解说内容
            self.commentary_tree.clear()
            self.status_label.setText(f"搜索中: {search_text}...")
            QApplication.processEvents()

            # 从数据库搜索
            event_results = self.db.search_commentary(search_text, search_type="event")
            phrase_results = self.db.search_commentary(search_text, search_type="phrase")

            # 合并结果并去重
            all_results = event_results + phrase_results
            unique_results = {item['id']: item for item in all_results}.values()

            # 按事件分组
            events = {}
            for item in unique_results:
                event = item['event']
                if event not in events:
                    events[event] = []
                events[event].append((item['id'], item['phrase']))

            # 添加到树形视图
            for event, phrases in events.items():
                # 创建顶级项（事件）
                event_item = QTreeWidgetItem([str(phrases[0][0]), event, ""])
                event_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "event", "event": event})
                self.commentary_tree.addTopLevelItem(event_item)

                # 添加子项（解说内容）
                for item_id, phrase in phrases:
                    phrase_item = QTreeWidgetItem([str(item_id), "", phrase])
                    phrase_item.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": "phrase",
                        "id": item_id,
                        "event": event,
                        "phrase": phrase
                    })
                    event_item.addChild(phrase_item)

            # 展开所有项
            self.commentary_tree.expandAll()
            self.status_label.setText(f"找到 {len(unique_results)} 条匹配结果")

        except Exception as e:
            self.status_label.setText(f"搜索失败: {str(e)}")
            logger.error(f"搜索解说词失败: {str(e)}")

    def clear_search(self):
        """清除搜索内容"""
        self.search_input.clear()
        self.refresh_commentary_list()


    def update_status(self, message):
        """安全更新状态栏"""
        self.status_label.setText(message)
        logger.info(f"状态更新: {message}")

    def test_voice(self):
        """测试语音功能 - 添加状态更新"""
        self.update_status("测试语音中...")
        prompt_path = self.tts.get_current_prompt()
        self.tts.add_text("这是测试语音", prompt_speech_path=prompt_path)


    def load_voice_list(self):
        """在后台线程中加载语音列表"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            voices = loop.run_until_complete(self.get_available_voices())
            logger.info(f"加载了 {len(voices)} 个可用语音")

            # 安全更新下拉框
            self.ui_updater.update_combo_signal.emit(voices)

            # 设置默认选择
            self.ui_updater.set_combo_index_signal.emit(0)

            # 更新状态
            self.ui_updater.update_status_signal.emit(f"已加载 {len(voices)} 个语音")
        except Exception as e:
            logger.error(f"加载语音列表失败: {e}")
            # 设置默认语音列表
            default_voices = [
                "zh-CN-YunyangNeural (男性)",
                "zh-CN-XiaoxiaoNeural (女性)",
                "en-US-JennyNeural (女性)"
            ]
            self.ui_updater.update_combo_signal.emit(default_voices)
            self.ui_updater.set_combo_index_signal.emit(0)
            self.ui_updater.update_status_signal.emit(f"使用默认语音列表: {e}")

    async def get_available_voices(self):
        """获取可用语音列表 - 安全版"""
        try:
            voice_list = os.listdir("example\\prompt")
            return voice_list
        except Exception as e:
            logger.error(f"获取语音列表失败: {e}")
            return []

    def create_voice_tab(self):
        """创建语音设置标签页 - 优化版"""
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # ====== 语音选择区域 ======
        voice_group = QGroupBox("语音选择")
        voice_layout = QVBoxLayout(voice_group)
        voice_layout.setSpacing(12)
        voice_layout.setContentsMargins(15, 20, 15, 15)

        # 语音选择行
        voice_select_layout = QHBoxLayout()
        voice_label = QLabel("选择语音:")
        self.voice_combo = QComboBox()
        self.load_audio_files()
        voice_select_layout.addWidget(voice_label)
        voice_select_layout.addWidget(self.voice_combo, 1)

        # 测试按钮
        self.test_voice_btn = QPushButton("测试语音")
        self.test_voice_btn.clicked.connect(self.test_voice)
        voice_select_layout.addWidget(self.test_voice_btn)

        voice_layout.addLayout(voice_select_layout)

        # 语音选择变化
        self.voice_combo.currentIndexChanged.connect(self.update_selected_prompt)

        # 语音描述卡片
        self.voice_card = QFrame()
        self.voice_card.setFrameShape(QFrame.Shape.StyledPanel)
        self.voice_card.setStyleSheet("""
                QFrame {
                    background-color: #2A2A2A; 
                    border-radius: 5px;
                }
                QLabel#title {
                    font-weight: bold;
                    color: #CCCCCC;
                    margin-bottom: 5px;
                }
                QLabel#content {
                    color: #AAAAAA;
                    padding: 0px;
                }
            """)
        self.card_layout = QVBoxLayout(self.voice_card)
        self.card_layout.setContentsMargins(12, 12, 12, 12)

        # 语音名称标签
        self.voice_card_title = QLabel("语音名称:")
        self.voice_card_title.setObjectName("title")
        self.card_layout.addWidget(self.voice_card_title)

        # 语音名称显示
        self.voice_name_label = QLabel("")
        self.voice_name_label.setObjectName("content")
        self.voice_name_label.setWordWrap(True)
        self.card_layout.addWidget(self.voice_name_label)

        # 初始更新卡片内容
        self.update_voice_card()

        voice_layout.addWidget(self.voice_card)
        main_layout.addWidget(voice_group)

        # 连接信号
        self.voice_combo.currentIndexChanged.connect(self.update_voice_card)

        # ====== 参数设置区域 ======
        settings_group = QGroupBox("参数设置")
        settings_layout = QVBoxLayout(settings_group)
        settings_layout.setSpacing(15)
        settings_layout.setContentsMargins(15, 20, 15, 15)

        # 滑块样式 - 增加高度
        slider_style = """
            QSlider::groove:horizontal {
                height: 8px;
                background: #333333;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #4A90E2;
                width: 18px;
                height: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }
            QSlider::sub-page:horizontal {
                background: #4A90E2;
                border-radius: 4px;
            }
        """

        # 音量控制
        volume_layout = QHBoxLayout()
        volume_label = QLabel("音量调整:")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setStyleSheet(slider_style)
        self.volume_slider.setRange(0, 200)  # 0% - 200%
        self.volume_slider.setValue(100)  # 默认100%
        self.volume_value = QLabel("100%")
        self.volume_value.setMinimumWidth(50)
        self.volume_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        volume_layout.addWidget(volume_label)
        volume_layout.addWidget(self.volume_slider)
        volume_layout.addWidget(self.volume_value)
        settings_layout.addLayout(volume_layout)

        # 语速控制
        rate_layout = QHBoxLayout()
        rate_label = QLabel("语速调整:")
        self.rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.rate_slider.setStyleSheet(slider_style)
        self.rate_slider.setRange(50, 150)
        self.rate_slider.setValue(100)
        self.rate_value = QLabel("100%")
        self.rate_value.setMinimumWidth(50)
        self.rate_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        rate_layout.addWidget(rate_label)
        rate_layout.addWidget(self.rate_slider)
        rate_layout.addWidget(self.rate_value)
        settings_layout.addLayout(rate_layout)

        # 音调控制
        pitch_layout = QHBoxLayout()
        pitch_label = QLabel("音调调整:")
        self.pitch_slider = QSlider(Qt.Orientation.Horizontal)
        self.pitch_slider.setStyleSheet(slider_style)
        self.pitch_slider.setRange(50, 150)
        self.pitch_slider.setValue(100)
        self.pitch_value = QLabel("100%")
        self.pitch_value.setMinimumWidth(50)
        self.pitch_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        pitch_layout.addWidget(pitch_label)
        pitch_layout.addWidget(self.pitch_slider)
        pitch_layout.addWidget(self.pitch_value)
        settings_layout.addLayout(pitch_layout)
        main_layout.addWidget(settings_group)

        # 连接信号
        self.volume_slider.valueChanged.connect(self.update_volume)
        self.rate_slider.valueChanged.connect(lambda v: self.update_rate(v))
        self.pitch_slider.valueChanged.connect(lambda v: self.update_pitch(v))

        return tab

    def update_voice_card(self):
        """更新语音卡片内容"""
        if self.voice_combo.count() > 0:
            current_voice = self.voice_combo.currentText()
            self.voice_name_label.setText(current_voice)

            # 从文件名中提取名称信息（根据实际需要调整）
            # 示例：提取中文名称部分，假设格式为"名称(性别)哈希值.wav"
            import re
            match = re.search(r"([\u4e00-\u9fa5]+\(\w+\))", current_voice)
            if match:
                display_name = match.group(1)
                self.voice_card_title.setText(f"语音名称: {display_name}")
            else:
                self.voice_card_title.setText("语音名称")

    def load_audio_files(self):
        """加载音频文件列表"""
        self.voice_combo.clear()
        prompt_dir = Path("example/prompt")
        if prompt_dir.exists():
            # 加载所有有效的音频文件
            valid_files = []
            for f in os.listdir(prompt_dir):
                if f.lower().endswith(('.wav', '.mp3')):
                    valid_files.append(f)

            self.voice_combo.addItems(valid_files)

            # 设置当前选择的示例文件
            if hasattr(self, 'tts') and self.tts:
                current_prompt = self.tts.get_current_prompt()
                if current_prompt:
                    file_name = Path(current_prompt).name
                    index = self.voice_combo.findText(file_name)
                    if index >= 0:
                        self.voice_combo.setCurrentIndex(index)

    def update_selected_prompt(self, index):
        """更新选择的示例文件"""
        if index >= 0:
            selected_file = self.voice_combo.currentText()
            if hasattr(self, 'tts') and self.tts:
                self.tts.set_prompt(selected_file)
                logger.info(f"设置提示音频: {selected_file}")

    def update_volume(self, value):
        """更新音量设置"""
        self.volume_value.setText(f"{value}%")
        if hasattr(self, 'tts') and self.tts:
            # 将0-200%范围转换为0.0-2.0的浮点数
            logger.info("音量设置: ", value)
            volume = value
            self.tts.set_volume(volume)

    def update_rate(self, value):
        """更新语速设置"""
        self.rate_value.setText(f"{value}%")
        if hasattr(self, 'tts') and self.tts:
            # 将百分比转换为0.5-1.5范围
            rate = value
            self.tts.set_rate(rate)

    def update_pitch(self, value):
        """更新音调设置"""
        self.pitch_value.setText(f"{value}%")
        if hasattr(self, 'tts') and self.tts:
            # 将百分比转换为0.5-1.5范围
            pitch = value
            self.tts.set_pitch(pitch)

    def handle_prompt_change(self, prompt_file):
        """处理示例文件变化"""
        logger.info(f"示例文件已更改为: {prompt_file}")
        # 可以在这里更新主程序状态或UI
        self.ui_updater.update_status_signal.emit(f"使用声音: {prompt_file}")

    def create_volume_slider(self, layout):
        """创建音量滑块 - 带防抖"""
        slider_layout = QHBoxLayout()

        # 标签
        volume_label = QLabel("音量调整:")
        volume_label.setStyleSheet("color: #cccccc; min-width: 80px;")
        slider_layout.addWidget(volume_label)

        # 滑块
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 200)  # 0% - 200%
        self.volume_slider.setValue(100)  # 默认100%
        self.volume_slider.setMinimumHeight(40)
        self.volume_slider.setStyleSheet(self.get_slider_style("#1e90ff"))

        # 创建防抖定时器
        self.volume_timer = QTimer()
        self.volume_timer.setSingleShot(True)
        self.volume_timer.timeout.connect(self.on_volume_timeout)

        # 连接值变化事件 - 使用防抖
        self.volume_slider.valueChanged.connect(self.on_volume_slider_changed)

        slider_layout.addWidget(self.volume_slider, 1)  # 占满剩余空间

        # 值显示标签
        self.volume_value = QLabel("+0%")
        self.volume_value.setStyleSheet("color: #cccccc; min-width: 60px;")
        slider_layout.addWidget(self.volume_value)

        layout.addLayout(slider_layout)

    def on_volume_slider_changed(self, value):
        """音量滑块值变化处理 - 防抖"""
        # 重启定时器
        self.volume_timer.stop()
        self.volume_timer.start(150)  # 150毫秒防抖

    def on_volume_timeout(self):
        """音量滑块值变化处理 - 实际更新"""
        try:
            value = self.volume_slider.value()
            self.update_volume(value)
        except Exception as e:
            logger.error(f"更新音量失败: {e}")

    def create_rate_slider(self, layout):
        """创建语速滑块 - 带防抖"""
        slider_layout = QHBoxLayout()

        # 标签
        rate_label = QLabel("语速调整:")
        rate_label.setStyleSheet("color: #cccccc; min-width: 80px;")
        slider_layout.addWidget(rate_label)

        # 滑块
        self.rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.rate_slider.setRange(50, 300)  # 50% - 300%
        self.rate_slider.setValue(100)  # 默认100%
        self.rate_slider.setMinimumHeight(40)
        self.rate_slider.setStyleSheet(self.get_slider_style("#1e90ff"))

        # 创建防抖定时器
        self.rate_timer = QTimer()
        self.rate_timer.setSingleShot(True)
        self.rate_timer.timeout.connect(self.on_rate_timeout)

        # 连接值变化事件 - 使用防抖
        self.rate_slider.valueChanged.connect(self.on_rate_slider_changed)

        slider_layout.addWidget(self.rate_slider, 1)  # 占满剩余空间

        # 值显示标签
        self.rate_value = QLabel("+0%")
        self.rate_value.setStyleSheet("color: #cccccc; min-width: 60px;")
        slider_layout.addWidget(self.rate_value)

        layout.addLayout(slider_layout)

    def on_rate_slider_changed(self, value):
        """语速滑块值变化处理 - 防抖"""
        # 重启定时器
        self.rate_timer.stop()
        self.rate_timer.start(150)  # 150毫秒防抖

    def on_rate_timeout(self):
        """语速滑块值变化处理 - 实际更新"""
        try:
            value = self.rate_slider.value()
            self.update_rate(value)
        except Exception as e:
            logger.error(f"更新语速失败: {e}")

    def create_pitch_slider(self, layout):
        """创建音调滑块 - 带防抖"""
        slider_layout = QHBoxLayout()

        # 标签
        pitch_label = QLabel("音调调整:")
        pitch_label.setStyleSheet("color: #cccccc; min-width: 80px;")
        slider_layout.addWidget(pitch_label)

        # 滑块
        self.pitch_slider = QSlider(Qt.Orientation.Horizontal)
        self.pitch_slider.setRange(-100, 100)  # -100Hz - +100Hz
        self.pitch_slider.setValue(0)  # 默认0Hz
        self.pitch_slider.setMinimumHeight(40)
        self.pitch_slider.setStyleSheet(self.get_slider_style("#1e90ff"))

        # 创建防抖定时器
        self.pitch_timer = QTimer()
        self.pitch_timer.setSingleShot(True)
        self.pitch_timer.timeout.connect(self.on_pitch_timeout)

        # 连接值变化事件 - 使用防抖
        self.pitch_slider.valueChanged.connect(self.on_pitch_slider_changed)

        slider_layout.addWidget(self.pitch_slider, 1)  # 占满剩余空间

        # 值显示标签
        self.pitch_value = QLabel("+0Hz")
        self.pitch_value.setStyleSheet("color: #cccccc; min-width: 60px;")
        slider_layout.addWidget(self.pitch_value)

        layout.addLayout(slider_layout)

    def on_pitch_slider_changed(self, value):
        """音调滑块值变化处理 - 防抖"""
        # 重启定时器
        self.pitch_timer.stop()
        self.pitch_timer.start(150)  # 150毫秒防抖

    def on_pitch_timeout(self):
        """音调滑块值变化处理 - 实际更新"""
        try:
            value = self.pitch_slider.value()
            self.update_pitch(value)
        except Exception as e:
            logger.error(f"更新音调失败: {e}")

    def get_slider_style(self, color):
        """获取滑块样式"""
        return f"""
            QSlider::groove:horizontal {{
                border: 1px solid #444444;
                height: 8px;
                background: #252525;
                margin: 0px;
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: #666666;
                border: 1px solid #444444;
                width: 18px;
                margin: -4px 0;
                border-radius: 9px;
            }}
            QSlider::sub-page:horizontal {{
                background: {color};
                border-radius: 4px;
            }}
        """

    def update_voice_selection(self, index):
        """更新语音选择 - 修复空值问题"""
        try:
            if index < 0:  # 无效索引
                return

            # 获取当前选择的语音
            voice_item = self.voice_combo.itemText(index)
            if not voice_item or not isinstance(voice_item, str):
                logger.warning(f"无效的语音项: {voice_item}")
                return

            voice_name = voice_item.split(" ")[0]
            if not voice_name or not isinstance(voice_name, str):
                logger.warning(f"无效的语音名称: {voice_name}")
                return

            # 更新语音描述
            self.update_voice_description(index)

            logger.info(f"已选择语音: {voice_name}")
        except Exception as e:
            logger.error(f"更新语音选择失败: {e}")

    def update_voice_description(self, index):
        """更新语音描述"""
        try:
            if index < 0:  # 无效索引
                return

            # 获取当前选择的语音
            voice_item = self.voice_combo.itemText(index)
            if not voice_item or not isinstance(voice_item, str):
                logger.warning(f"无效的语音项: {voice_item}")
                return

            # 解析语音描述
            parts = voice_item.split(" - ")
            if len(parts) >= 3:
                voice_name = parts[0]
                gender = parts[0].split("(")[1].split(")")[0]

                description = (
                    f"<b>语音名称:</b> {voice_name}<br>"
                    f"<b>性别:</b> {gender}<br>"
                    f"<b>语言:</b> {parts[2] if len(parts) >= 3 else '未知'}<br>"
                    f"<b>提供商:</b> {parts[1] if len(parts) >= 2 else '未知'}<br>"
                )
            else:
                description = f"<b>语音名称:</b> {voice_item}"

            # 安全更新UI
            self.ui_updater.update_description_signal.emit(description)
        except Exception as e:
            logger.error(f"更新语音描述失败: {e}")
            self.ui_updater.update_description_signal.emit("无法加载语音描述")


    def closeEvent(self, event):
        """关闭窗口时保存设置"""
        try:
            logger.info("正在安全关闭应用程序...")

            # 1. 停止所有定时器
            self.idle_timer.stop()

            # 2. 停止弹幕客户端
            if hasattr(self, 'danmu_client') and self.danmu_client:
                try:
                    self.danmu_client.stop()
                    self.danmu_client = None
                    logger.info("弹幕客户端已停止")
                except Exception as e:
                    logger.error(f"停止弹幕客户端失败: {e}")

            # 3. 停止摄像头捕获
            if self.capture_thread and self.capture_thread.isRunning():
                try:
                    self.capture_thread.stop()
                    self.capture_thread.quit()
                    self.capture_thread.wait(2000)
                    if self.capture_thread.isRunning():
                        self.capture_thread.terminate()
                    logger.info("摄像头线程已停止")
                except Exception as e:
                    logger.error(f"停止摄像头线程失败: {e}")

            # 4. 清理视频窗口资源
            if hasattr(self, "video_widget") and self.video_widget:
                self.video_widget.safe_close_player()

            # 5. 关闭语音播放器
            if hasattr(self, 'tts') and self.tts:
                try:
                    self.tts.shutdown()
                    logger.info("语音播放器已关闭")
                except Exception as e:
                    logger.error(f"关闭语音播放器失败: {e}")
                finally:
                    self.tts = None

            # 6. 关闭数据库连接
            if hasattr(self, 'db'):
                try:
                    self.db.close()
                    logger.info("数据库连接已关闭")
                except Exception as e:
                    logger.error(f"关闭数据库失败: {e}")

            # 7. 停止所有捕获线程
            if hasattr(self, 'capture_threads'):
                for thread in self.capture_threads.values():
                    if thread and thread.isRunning():
                        try:
                            thread.stop()
                            thread.wait(2000)
                        except Exception as e:
                            logger.error(f"停止捕获线程错误: {str(e)}")

            logger.info("应用程序安全关闭完成")
        except Exception as e:
            logger.critical(f"关闭过程中发生错误: {e}")
            sys.exit(1)
        finally:
            super().closeEvent(event)


def main():
    """主函数"""
    log_startup("进入 main()")
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("icon/0nbum-0m212-001.ico"))

    # 创建暗色调色板
    dark_palette = QPalette()

    # 基础颜色设置
    dark_palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    dark_palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    dark_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(25, 25, 25))
    dark_palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
    dark_palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
    dark_palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
    dark_palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    dark_palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)

    # 设置应用调色板和样式
    app.setPalette(dark_palette)
    app.setStyle("Fusion")

    # 设置应用程序信息
    app.setApplicationName("多功能视频播放器")
    app.setApplicationVersion("1.0")
    app.setOrganizationName("VideoPlayer")

    # 创建并显示主窗口
    log_startup("开始创建 MainWindow")
    main_windows = MainWindow()
    log_startup("MainWindow 创建完成")

    log_startup_summary()
    main_windows.show()
    log_startup("主窗口已显示")

    # 运行应用程序
    sys.exit(app.exec())




if __name__ == "__main__":
    main()
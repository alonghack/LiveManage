import collections
import os
import queue
import re
import threading
import time
import weakref

import numpy as np
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Dict

from PyQt6.QtCore import QObject, pyqtSignal
from loguru import logger

logger.debug("[模块] auxiliary.sound 已导入")

# 以下重型模块改为延迟导入（在主窗口显示后才加载，避免启动卡顿）
# import sounddevice as sd   → _get_sd()
# import torch               → _get_torch()
# transformers.AutoTokenizer, AutoModelForCausalLM → _lazy_load_tts_models()
# SparkTTS 相关              → _lazy_load_tts_models()
# get_torch_device           → _get_torch_device()


def _get_sd():
    """延迟导入 sounddevice（约 0.5s）"""
    import sounddevice as _sd
    return _sd


def _get_torch():
    """延迟导入 torch（约 3-5s，含 CUDA 初始化）"""
    import torch as _torch
    return _torch


def _get_torch_device():
    """延迟导入 get_torch_device"""
    from auxiliary.utils import get_torch_device as _gtd
    return _gtd


class PlaybackType(Enum):
    FILE = auto()
    ARRAY = auto()
    TTS = auto()


class AudioStreamManager:
    """专门的音频流管理器，避免内存访问冲突"""

    def __init__(self):
        self.active_streams = []
        self.lock = threading.Lock()
        self._stream_counter = 0
        self.volume = 1.0

    def set_volume(self, value):
        self.volume = value

    def add_stream(self, stream_info):
        """添加流并返回流ID"""
        with self.lock:
            stream_id = self._stream_counter
            self._stream_counter += 1
            stream_info['id'] = stream_id
            # 使用弱引用避免循环引用
            stream_ref = {
                'id': stream_id,
                'stop_event': stream_info['stop_event'],
                'stream_ref': weakref.ref(stream_info['stream']),
                'volume': self.volume,
                'position': stream_info['position'],
                'is_file_playback': stream_info.get('is_file_playback', False),
                # 添加完整流信息引用以便暂停功能使用
                'stream_info': stream_info
            }
            self.active_streams.append(stream_ref)
            logger.debug(
                f"添加音频流到管理器: ID={stream_id}, 类型={'文件' if stream_info.get('is_file_playback') else '数据'}")
            return stream_id

    def remove_stream(self, stream_id):
        """移除指定ID的流"""
        with self.lock:
            initial_count = len(self.active_streams)
            self.active_streams = [s for s in self.active_streams if s['id'] != stream_id]
            removed_count = initial_count - len(self.active_streams)
            if removed_count > 0:
                logger.debug(f"从管理器移除音频流: ID={stream_id}")

    def get_streams_by_type(self, is_file_playback):
        """根据类型获取流"""
        with self.lock:
            streams = [s for s in self.active_streams if s['is_file_playback'] == is_file_playback]
            logger.debug(f"按类型查找流: 文件播放={is_file_playback}, 找到{len(streams)}个")
            return streams

    def get_all_streams(self):
        """获取所有流"""
        with self.lock:
            return self.active_streams.copy()

    def clear_all_streams(self):
        """清除所有流"""
        with self.lock:
            count = len(self.active_streams)
            self.active_streams.clear()
            logger.debug(f"清除所有音频流: 共{count}个")

    def safe_stop_stream(self, stream_ref):
        """安全地停止流"""
        try:
            stream = stream_ref['stream_ref']()
            if stream and hasattr(stream, 'active'):
                if stream.active:
                    try:
                        # 设置停止事件
                        stream_ref['stop_event'].set()
                        stream.stop()
                        logger.debug(f"停止活动音频流: ID={stream_ref['id']}")
                    except Exception as e:
                        logger.debug(f"停止流时出错: {str(e)}")
                if hasattr(stream, 'close'):
                    try:
                        stream.close()
                    except Exception as e:
                        logger.debug(f"关闭流时出错: {str(e)}")
            return True
        except Exception as e:
            logger.error(f"安全停止流时出错: {str(e)}")
            return False


class TsVoice(QObject):
    """
    深度优化的TTS播放器类，支持并行合成与顺序播放
    """
    initialization_started = pyqtSignal()
    initialization_finished = pyqtSignal(bool, str)  # (成功状态, 消息)
    synthesis_started = pyqtSignal(str)  # 要合成的文本
    synthesis_completed = pyqtSignal(str, bool, str)  # (文本, 成功状态, 消息)
    playback_started = pyqtSignal(str, int)  # 开始播放的文本和类型
    playback_finished = pyqtSignal(str, int)  # 完成播放的文本和类型
    error_occurred = pyqtSignal(str)  # 错误消息
    queue_updated = pyqtSignal(int, int)  # (待合成数, 待播放数)

    def __init__(self):
        logger.info("开始TTS播放器初始化。。。")
        super().__init__()

        self.model_dir = None
        self.prompt_dir = None
        self.audio_files = []

        # 设置离线模式环境变量
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_OFFLINE"] = "1"

        self.is_initialized = False
        self.is_initializing = False
        self.initialization_lock = threading.Lock()

        # 音频播放管理 - 使用专门的流管理器
        self.stream_manager = AudioStreamManager()
        self._alive = True  # 对象存活标志

        # 播放状态跟踪
        self.playback_status = {
            PlaybackType.FILE: {
                'active': False,
                'completed': 0,
                'last_file': None
            },
            PlaybackType.ARRAY: {
                'active': False,
                'completed': 0,
                'last_array': None
            },
            PlaybackType.TTS: {
                'active': False,
                'completed': 0,
                'last_text': None,
                'current_text': None
            }
        }
        self.status_lock = threading.Lock()

        # 新增：暂停状态管理
        self.pause_status = {
            PlaybackType.FILE: {
                'paused': False,
                'paused_position': 0,
                'paused_audio_data': None,
                'paused_sample_rate': None
            },
            PlaybackType.ARRAY: {
                'paused': False,
                'paused_position': 0,
                'paused_audio_data': None,
                'paused_sample_rate': None
            },
            PlaybackType.TTS: {
                'paused': False,
                'paused_position': 0,
                'paused_audio_data': None,
                'paused_sample_rate': None,
                'paused_text': None
            }
        }
        self.pause_lock = threading.Lock()

        # 模型相关
        self.model = None
        self.tokenizer = None
        self.audio_tokenizer = None
        self.device = None

        # 参数
        self.volume = 0.8
        self.video_volume = 0.6
        self.rate = 1.0
        self.pitch = 1.0
        self.gender = "female"
        self.prompt_text = None
        self.default_prompt_speech = None

        # 当前选择的示例文件
        self.selected_prompt_path = None

        # 并行处理系统
        self.synthesis_queue = collections.deque(maxlen=10)  # 待合成队列 (text, kwargs)
        self.playback_queue = queue.Queue()  # 待播放队列 (text, wav, playback_type)
        self.queue_lock = threading.Lock()
        self.stop_requested = threading.Event()

        # 线程池配置
        self.max_workers = 2  # 默认最大并行合成数
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="TTS_Synthesis")

        # 状态跟踪
        self.active_synthesis = 0
        self.playback_active = False

        # 启动队列监控
        self._start_queue_monitor()

        logger.info("TTS播放器初始化完成，等待配置")

    def _start_queue_monitor(self):
        """启动队列监控线程"""

        def monitor():
            while getattr(self, '_alive', True) and not self.stop_requested.is_set():
                try:
                    # 检查是否有待播放项目但播放线程未运行
                    if not self.playback_queue.empty() and not self.playback_active:
                        logger.debug("监控线程检测到有待播放项目，启动播放线程")
                        self._process_playback_queue()

                    # 检查是否有待合成项目但合成线程未运行
                    if len(self.synthesis_queue) > 0 and self.active_synthesis < self.max_workers:
                        logger.debug("监控线程检测到有待合成项目，启动合成线程")
                        self._process_synthesis_queue()

                    time.sleep(0.5)  # 每半秒检查一次
                except Exception as e:
                    logger.error(f"队列监控出错: {str(e)}")
                    time.sleep(1)

        threading.Thread(target=monitor, daemon=True).start()

    @staticmethod
    def _ensure_model_files(model_path: Path) -> None:
        """验证模型权重文件存在，缺失时自动从 HuggingFace 下载"""
        llm_path = model_path / "LLM"
        bicodec_path = model_path / "BiCodec"
        w2v_path = model_path / "wav2vec2-large-xlsr-53"

        # 定义需要检查的文件
        required = [
            (llm_path, ["pytorch_model.bin", "model.safetensors"]),
            (bicodec_path, ["model.safetensors"]),
            (w2v_path, ["pytorch_model.bin", "model.safetensors"]),
        ]

        # 检查哪些缺失
        missing_list = []
        for dir_path, filenames in required:
            found = any((dir_path / f).exists() for f in filenames)
            if not found:
                # 也检查分片文件 *.safetensors (index file case)
                if not list(dir_path.glob("*.safetensors")):
                    missing_list.append((dir_path, filenames))

        if not missing_list:
            return

        logger.warning(f"缺失模型权重文件，尝试从 HuggingFace 下载到 {model_path} ...")
        for d, fs in missing_list:
            logger.warning(f"  - {d.name}/: {' 或 '.join(fs)}")

        try:
            from huggingface_hub import hf_hub_download
            import os

            # 为 hf-transfer 加速
            os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")

            # 待下载的文件清单
            repo_id = "SparkAudio/Spark-TTS-0.5B"
            # 先获取文件列表
            try:
                from huggingface_hub import list_repo_files
                files = list_repo_files(repo_id)
                logger.info(f"远程仓库 {repo_id} 包含 {len(files)} 个文件")
            except Exception:
                files = None

            if not files:
                # 无法列出远程文件，尝试镜像
                for mirror in ["", "https://hf-mirror.com"]:
                    if mirror:
                        old = os.environ.get("HF_ENDPOINT")
                        os.environ["HF_ENDPOINT"] = mirror
                    try:
                        from huggingface_hub import list_repo_files
                        files = list_repo_files(repo_id)
                        if files:
                            if mirror:
                                logger.info(f"通过镜像 {mirror} 成功连接")
                            break
                    except Exception:
                        continue
                    finally:
                        if mirror and old:
                            os.environ["HF_ENDPOINT"] = old

            if not files:
                raise ConnectionError("无法连接到 HuggingFace (huggingface.co / hf-mirror.com 均不可达)")

            # 确定需要下载的文件
            to_download = set()
            for dir_path, filenames in missing_list:
                prefix = f"{dir_path.name}/"
                for f in files:
                    if f.startswith(prefix):
                        to_download.add(f)

            if not to_download:
                raise RuntimeError(f"无法在仓库中找到对应的权重文件路径")

            logger.info(f"需要下载 {len(to_download)} 个文件，开始下载...")
            for i, f in enumerate(sorted(to_download), 1):
                dest = model_path / f
                if dest.exists() and dest.stat().st_size > 1000:
                    logger.info(f"  [{i}/{len(to_download)}] {f} 已存在，跳过")
                    continue
                logger.info(f"  [{i}/{len(to_download)}] 下载 {f} ...")
                try:
                    hf_hub_download(
                        repo_id=repo_id,
                        filename=f,
                        local_dir=str(model_path),
                        local_dir_use_symlinks=False,
                        resume_download=True,
                    )
                except Exception as file_e:
                    logger.warning(f"  下载 {f} 失败: {file_e}")

            logger.success("下载完成，验证文件完整性...")

        except ImportError:
            raise RuntimeError(
                "缺少 huggingface_hub 库，无法自动下载模型。\n"
                "请手动下载模型文件:\n"
                "1. 访问 https://huggingface.co/SparkAudio/Spark-TTS-0.5B\n"
                f"2. 将模型文件下载到 {model_path}\n"
            )

        except Exception as e:
            # 不管什么错误，最后检查文件是否齐全
            logger.warning(f"下载过程遇到问题: {e}")

        # 最终验证
        still_missing = []
        for dir_path, filenames in required:
            found = any((dir_path / f).exists() for f in filenames)
            if not found:
                still_missing.append(f"{dir_path.name}/")

        if still_missing:
            raise RuntimeError(
                f"模型权重文件仍然缺失: {', '.join(still_missing)}\n\n"
                "自动下载失败，请手动下载:\n"
                "1. 访问 https://huggingface.co/SparkAudio/Spark-TTS-0.5B\n"
                "2. 点击 \"Files and versions\" 选项卡\n"
                "3. 下载 LLM/、BiCodec/、wav2vec2-large-xlsr-53/ 中的权重文件\n"
                f"4. 将文件放入 {model_path} 对应的子目录中\n\n"
                "提示: 如果在中国大陆，可设置 HuggingFace 镜像:\n"
                "  set HF_ENDPOINT=https://hf-mirror.com\n"
                "  (Windows CMD) 或 $env:HF_ENDPOINT=\"https://hf-mirror.com\" (PowerShell)"
            )

    def configure(self, model_dir="models", prompt_dir="example/prompt", max_workers=2):
        """配置模型参数并开始初始化"""
        try:
            # 验证模型目录
            model_path = Path(model_dir)
            if not model_path.exists():
                raise FileNotFoundError(f"模型目录不存在: {model_path}")

            # 验证LLM子目录
            llm_path = model_path / "LLM"
            if not llm_path.exists():
                raise FileNotFoundError(f"LLM子目录不存在: {llm_path}")

            # 验证并自动下载模型权重
            self._ensure_model_files(model_path)

            self.model_dir = model_path
            self.prompt_dir = Path(prompt_dir)
            self.max_workers = max_workers

            # 重新配置线程池
            self.executor.shutdown(wait=False)
            self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="TTS_Synthesis")

            self.device, _ = _get_torch_device()()

            # 启动延迟初始化
            self._start_lazy_initialization()

        except Exception as e:
            error_msg = f"配置失败: {str(e)}"
            logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            self.initialization_finished.emit(False, error_msg)

    def _update_playback_status(self, playback_type: PlaybackType, **kwargs):
        """更新播放状态"""
        with self.status_lock:
            for key, value in kwargs.items():
                if key in self.playback_status[playback_type]:
                    self.playback_status[playback_type][key] = value

    def get_playback_status(self, playback_type: Optional[PlaybackType] = None) -> Dict:
        """获取播放状态"""
        with self.status_lock:
            if playback_type:
                return self.playback_status[playback_type].copy()
            else:
                return {k: v.copy() for k, v in self.playback_status.items()}

    def is_file_playing(self) -> bool:
        """检查是否有文件正在播放"""
        return self.get_playback_status(PlaybackType.FILE)['active']

    def is_array_playing(self) -> bool:
        """检查是否有数组正在播放"""
        return self.get_playback_status(PlaybackType.ARRAY)['active']

    def is_tts_playing(self) -> bool:
        """检查是否有TTS正在播放"""
        return self.get_playback_status(PlaybackType.TTS)['active']

    def get_file_playback_count(self) -> int:
        """获取文件播放完成次数"""
        return self.get_playback_status(PlaybackType.FILE)['completed']

    def get_array_playback_count(self) -> int:
        """获取数组播放完成次数"""
        return self.get_playback_status(PlaybackType.ARRAY)['completed']

    def get_tts_playback_count(self) -> int:
        """获取TTS播放完成次数"""
        return self.get_playback_status(PlaybackType.TTS)['completed']

    def get_last_played_file(self) -> Optional[str]:
        """获取最后播放的文件"""
        return self.get_playback_status(PlaybackType.FILE)['last_file']

    def get_last_played_array(self) -> Optional[str]:
        """获取最后播放的数组信息"""
        return self.get_playback_status(PlaybackType.ARRAY)['last_array']

    def get_last_played_text(self) -> Optional[str]:
        """获取最后播放的文本"""
        return self.get_playback_status(PlaybackType.TTS)['last_text']

    def get_current_playing_text(self) -> Optional[str]:
        """获取当前正在播放的文本"""
        return self.get_playback_status(PlaybackType.TTS)['current_text']

    def pause_audio_file(self):
        """暂停当前文件播放"""
        return self._pause_playback(PlaybackType.FILE)

    def resume_audio_file(self):
        """继续播放已暂停的文件"""
        return self._resume_playback(PlaybackType.FILE)

    def pause_audio_array(self):
        """暂停当前数组播放"""
        return self._pause_playback(PlaybackType.ARRAY)

    def resume_audio_array(self):
        """继续播放已暂停的数组"""
        return self._resume_playback(PlaybackType.ARRAY)

    def pause_tts_playback(self):
        """暂停当前TTS播放"""
        return self._pause_playback(PlaybackType.TTS)

    def resume_tts_playback(self):
        """继续播放已暂停的TTS"""
        return self._resume_playback(PlaybackType.TTS)

    def _pause_playback(self, playback_type: PlaybackType) -> bool:
        """暂停指定类型的播放"""
        try:
            with self.pause_lock:
                # 检查是否已经在播放中且未暂停
                status = self.get_playback_status(playback_type)
                if not status['active'] or self.pause_status[playback_type]['paused']:
                    logger.warning(f"{playback_type.name} 未在播放或已暂停")
                    return False

                # 获取当前活动的流
                is_file_playback = (playback_type == PlaybackType.FILE)
                active_streams = self.stream_manager.get_streams_by_type(is_file_playback)

                logger.info(f"找到 {len(active_streams)} 个 {playback_type.name} 的活动流")

                if not active_streams:
                    logger.warning(f"未找到 {playback_type.name} 的活动流")
                    # 检查所有流的状态
                    all_streams = self.stream_manager.get_all_streams()
                    logger.info(f"系统中所有流: {len(all_streams)}")
                    for stream in all_streams:
                        logger.info(
                            f"流 ID: {stream['id']}, 类型: {'文件' if stream['is_file_playback'] else '数据'}, 位置: {stream.get('position', 0)}")
                    return False

                # 只处理第一个活动流（假设每种类型只有一个活动流）
                stream_ref = active_streams[0]
                stream_info = stream_ref.get('stream_info')

                if not stream_info:
                    logger.error(f"{playback_type.name} 流信息不存在")
                    return False

                # 验证流是否还在活动状态
                stream = stream_ref['stream_ref']()
                if not stream or not hasattr(stream, 'active') or not stream.active:
                    logger.warning(f"{playback_type.name} 流已不再活动")
                    return False

                # 保存暂停状态
                current_position = stream_info.get('position', 0)
                self.pause_status[playback_type].update({
                    'paused': True,
                    'paused_position': current_position,
                    'paused_audio_data': stream_info.get('audio_data'),
                    'paused_sample_rate': stream_info.get('sample_rate'),
                    'paused_text': self.get_current_playing_text() if playback_type == PlaybackType.TTS else None
                })

                # 停止当前流
                if self.stream_manager.safe_stop_stream(stream_ref):
                    # 从管理器中移除流但不更新播放状态
                    self.stream_manager.remove_stream(stream_ref['id'])

                    # 更新播放状态为暂停（非活跃但保持其他状态）
                    self._update_playback_status(playback_type, active=False)

                    logger.info(f"{playback_type.name} 播放已暂停，位置: {current_position}")
                    return True
                else:
                    logger.error(f"暂停 {playback_type.name} 时停止流失败")
                    return False

        except Exception as e:
            logger.error(f"暂停 {playback_type.name} 播放时发生错误: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _resume_playback(self, playback_type: PlaybackType) -> bool:
        import sounddevice as sd
        """继续播放指定类型的音频"""
        try:
            with self.pause_lock:
                # 检查是否有暂停的播放
                if not self.pause_status[playback_type]['paused']:
                    logger.warning(f"{playback_type.name} 没有暂停的播放可恢复")
                    return False

                # 获取暂停状态
                pause_info = self.pause_status[playback_type]
                position = pause_info['paused_position']
                audio_data = pause_info['paused_audio_data']
                sample_rate = pause_info['paused_sample_rate']

                if audio_data is None or sample_rate is None:
                    logger.error(f"{playback_type.name} 暂停状态数据不完整")
                    return False

                # 从暂停位置继续播放
                remaining_audio = audio_data[position:]
                if len(remaining_audio) == 0:
                    logger.info(f"{playback_type.name} 已播放完毕，无需恢复")
                    self._clear_pause_status(playback_type)
                    return False

                # 创建新的流从暂停位置开始
                array_info = None
                if playback_type == PlaybackType.ARRAY:
                    array_info = f"恢复播放: 位置 {position}"
                elif playback_type == PlaybackType.TTS:
                    array_info = f"恢复TTS: {pause_info['paused_text']}"

                # 创建新的流信息，从暂停位置开始
                stream_info = {
                    'position': 0,  # 从0开始，因为remaining_audio已经是剩余部分
                    'stop_event': threading.Event(),
                    'stream': None,
                    'is_file_playback': (playback_type == PlaybackType.FILE),
                    'audio_data': remaining_audio,  # 保存音频数据供回调使用
                    'sample_rate': sample_rate
                }

                # 创建音频流
                callback = self._stream_audio_callback(remaining_audio, sample_rate, stream_info,
                                                       stream_info['is_file_playback'])

                try:
                    output_device = sd.default.device[1] if sd.default.device[1] is not None else sd.default.device[0]
                    stream = sd.OutputStream(
                        device=output_device,
                        samplerate=sample_rate,
                        channels=1 if len(remaining_audio.shape) == 1 else remaining_audio.shape[1],
                        callback=callback,
                        finished_callback=lambda: self._safe_remove_stream(stream_info),
                        blocksize=4096,
                        latency='high',
                        dtype='float32'
                    )
                except Exception as e:
                    logger.warning(f"使用指定设备失败，回退到默认设备: {str(e)}")
                    stream = sd.OutputStream(
                        samplerate=sample_rate,
                        channels=1 if len(remaining_audio.shape) == 1 else remaining_audio.shape[1],
                        callback=callback,
                        finished_callback=lambda: self._safe_remove_stream(stream_info),
                        blocksize=4096,
                        latency='high',
                        dtype='float32'
                    )

                stream_info['stream'] = stream
                stream_id = self.stream_manager.add_stream(stream_info)
                stream_info['id'] = stream_id

                # 启动播放
                stream.start()

                # 更新播放状态
                self._update_playback_status(playback_type, active=True)
                if playback_type == PlaybackType.TTS and pause_info['paused_text']:
                    self._update_playback_status(playback_type, current_text=pause_info['paused_text'])

                # 清除暂停状态
                self._clear_pause_status(playback_type)

                # 启动监控线程
                def monitor_thread():
                    try:
                        while stream.active and not stream_info['stop_event'].is_set():
                            if not getattr(self, '_alive', True):
                                break
                            time.sleep(0.05)

                        if stream.active:
                            try:
                                stream.stop()
                            except Exception as e:
                                logger.debug(f"停止流时出错: {str(e)}")
                        try:
                            stream.close()
                        except Exception as e:
                            logger.debug(f"关闭流时出错: {str(e)}")
                        self._safe_remove_stream(stream_info)

                        # 播放完成，更新状态
                        if not stream_info['stop_event'].is_set():  # 正常结束，不是被停止的
                            self._update_playback_status(playback_type, active=False,
                                                         completed=self._get_playback_count(playback_type) + 1)
                            if playback_type == PlaybackType.TTS:
                                self._update_playback_status(playback_type, current_text=None)

                    except Exception as e:
                        logger.error(f"监控线程出错: {str(e)}")
                        self._safe_remove_stream(stream_info)
                        self._update_playback_status(playback_type, active=False)

                threading.Thread(target=monitor_thread, daemon=True).start()

                logger.info(f"{playback_type.name} 播放已恢复，从位置 {position} 开始")
                return True

        except Exception as e:
            logger.error(f"恢复 {playback_type.name} 播放时发生错误: {str(e)}")
            return False

    def _clear_pause_status(self, playback_type: PlaybackType):
        """清除指定播放类型的暂停状态"""
        self.pause_status[playback_type].update({
            'paused': False,
            'paused_position': 0,
            'paused_audio_data': None,
            'paused_sample_rate': None,
            'paused_text': None
        })

    def is_playback_paused(self, playback_type: Optional[PlaybackType] = None) -> bool:
        """检查播放是否暂停"""
        if playback_type:
            return self.pause_status[playback_type]['paused']
        else:
            return any(status['paused'] for status in self.pause_status.values())

    def get_pause_position(self, playback_type: PlaybackType) -> int:
        """获取指定播放类型的暂停位置"""
        return self.pause_status[playback_type]['paused_position']

    def stop_file_playback(self):
        """停止所有文件类型的播放"""
        try:
            logger.info("开始停止文件播放...")

            # 清除文件播放的暂停状态
            self._clear_pause_status(PlaybackType.FILE)

            # 获取所有文件类型的流
            file_streams = self.stream_manager.get_streams_by_type(True)
            logger.info(f"找到 {len(file_streams)} 个文件播放流需要停止")

            # 停止每个文件流
            stopped_count = 0
            for stream_ref in file_streams:
                if self.stream_manager.safe_stop_stream(stream_ref):
                    stopped_count += 1
                    # 从管理器中移除
                    self.stream_manager.remove_stream(stream_ref['id'])

            logger.info(f"成功停止 {stopped_count} 个文件播放流")

            # 更新文件播放状态
            self._update_playback_status(PlaybackType.FILE, active=False)
            logger.info("文件播放已停止")

        except Exception as e:
            logger.error(f"停止文件播放时发生错误: {str(e)}")
            # 确保无论如何都清除状态
            self._update_playback_status(PlaybackType.FILE, active=False)

    def _start_lazy_initialization(self):
        """启动延迟初始化，不影响主线程"""

        def init_task():
            time.sleep(0.5)
            self._initialize_resources()

        init_thread = threading.Thread(target=init_task, daemon=True)
        init_thread.start()

    def set_prompt(self, prompt_file: Optional[str]):
        """设置当前使用的示例文件"""
        if prompt_file:
            self.selected_prompt_path = str(Path(self.prompt_dir) / prompt_file)
            logger.info(f"设置提示音频: {self.selected_prompt_path}")
        else:
            self.selected_prompt_path = None

    def get_current_prompt(self) -> Optional[str]:
        """获取当前选择的示例文件"""
        return self.selected_prompt_path

    def set_volume(self, value: float):
        """设置音量 (0.0-1.0)"""
        new_volume = max(0.0, min(1.0, value / 100.0))
        logger.info(f"设置音量: {new_volume}")
        self.volume = new_volume
        self.stream_manager.set_volume(self.volume)

    def set_video_volume(self, value: float):
        """设置音量 (0.0-1.0)"""
        new_volume = max(0.0, min(1.0, value / 100.0))
        logger.info(f"设置视频音量: {new_volume}")
        self.video_volume = new_volume
        self.stream_manager.set_volume(self.video_volume)

    def set_rate(self, value: float):
        """设置语速"""
        self.rate = max(0.1, min(2.0, value / 100.0))
        logger.info(f"设置语速: {self.rate}")

    def set_pitch(self, value: float):
        """设置音调"""
        self.pitch = max(0.5, min(1.5, value / 100.0))
        logger.info(f"设置音调: {self.pitch}")

    def set_gender(self, gender: str):
        """设置性别"""
        if gender in ["male", "female"]:
            self.gender = gender
            logger.info(f"设置性别: {gender}")

    def _initialize_resources(self):
        """异步初始化资源"""
        if self.is_initializing:
            return

        self.is_initializing = True
        self.initialization_started.emit()

        def init_task():
            try:
                logger.info("开始异步初始化TTS模型...")

                # 延迟导入重型依赖（仅在加载模型时触发）
                from transformers import AutoTokenizer, AutoModelForCausalLM
                from SparkTTS.sparktts.models.audio_tokenizer import BiCodecTokenizer

                if not self.model_dir or not self.model_dir.exists():
                    raise FileNotFoundError(f"模型目录不存在: {self.model_dir}")

                llm_path = self.model_dir / "LLM"
                if not llm_path.exists():
                    raise FileNotFoundError(f"LLM子目录不存在: {llm_path}")

                logger.info(f"加载tokenizer从: {llm_path}")
                self.tokenizer = AutoTokenizer.from_pretrained(str(llm_path))

                logger.info(f"加载模型从: {llm_path}")
                self.model = AutoModelForCausalLM.from_pretrained(str(llm_path))

                logger.info(f"初始化音频tokenizer")
                self.audio_tokenizer = BiCodecTokenizer(self.model_dir, device=self.device)

                logger.info(f"移动模型到设备: {self.device}")
                self.model.to(self.device)

                if self.model is None or self.tokenizer is None or self.audio_tokenizer is None:
                    raise RuntimeError("模型加载失败")

                self._load_and_validate_prompt_audios()
                self.is_initialized = True
                logger.success("TTS模型初始化完成")
                self.initialization_finished.emit(True, "初始化完成")

                # 初始化完成后处理队列中的任务
                self._process_synthesis_queue()
                self._process_playback_queue()

            except Exception as e:
                error_msg = f"初始化失败: {str(e)}"
                logger.error(error_msg)
                self.initialization_finished.emit(False, error_msg)
                self.error_occurred.emit(error_msg)
            finally:
                self.is_initializing = False

        threading.Thread(target=init_task, daemon=True).start()

    def _load_and_validate_prompt_audios(self):
        """加载并验证示例音频"""
        if not self.prompt_dir or not self.prompt_dir.exists():
            logger.warning(f"提示目录未找到: {self.prompt_dir}")
            return

        valid_audios = []
        for f in os.listdir(self.prompt_dir):
            if f.lower().endswith(('.wav', '.mp3')):
                file_path = self.prompt_dir / f
                try:
                    import soundfile as sf
                    data, _ = sf.read(file_path)
                    if len(data) > 100:
                        valid_audios.append(f)
                except:
                    continue

        self.audio_files = valid_audios
        if self.audio_files:
            self.default_prompt_speech = str(self.prompt_dir / self.audio_files[0])
            logger.info(f"加载 {len(self.audio_files)} 有效的提示音频")

    def _stream_audio_callback(self, audio_data, sample_rate, stream_info, is_file_playback):
        """通用的流式音频回调函数"""

        import sounddevice as sd

        # 预计算一些值以提高性能
        audio_length = len(audio_data)

        def callback(outdata, frames, time_info, status):
            # 忽略underflow警告，这是正常现象
            if status and "output underflow" not in str(status).lower():
                logger.debug(f"音频状态: {status}")

            if stream_info['stop_event'].is_set():
                raise sd.CallbackStop()

            audio_position = stream_info['position']

            # 检查是否已经播放完毕
            if audio_position >= audio_length:
                raise sd.CallbackStop()

            # 计算结束位置
            end_position = audio_position + frames
            if end_position > audio_length:
                end_position = audio_length

            # 直接切片获取数据
            chunk = audio_data[audio_position:end_position]

            # 应用音量
            if is_file_playback and self.video_volume != 1.0:
                chunk = chunk * self.video_volume
            if not is_file_playback and self.volume != 1.0:
                chunk = chunk * self.volume

            # 处理数据填充
            if len(chunk) < frames:
                # 音频结束，填充零
                outdata[:len(chunk)] = chunk
                outdata[len(chunk):] = 0.0
                stream_info['position'] = audio_length
            else:
                # 确保形状匹配
                if chunk.shape != outdata.shape:
                    if len(chunk.shape) == 1 and len(outdata.shape) == 2:
                        chunk = chunk.reshape(-1, 1)
                outdata[:] = chunk
                stream_info['position'] = end_position

        return callback

    def _create_audio_stream(self, audio_data, sample_rate, channels=1, is_file_playback=False):
        """创建音频流并返回流信息"""
        import sounddevice as sd
        stream_info = {
            'position': 0,
            'stop_event': threading.Event(),
            'stream': None,
            'is_file_playback': is_file_playback,
            'audio_data': audio_data,  # 保存音频数据供暂停使用
            'sample_rate': sample_rate  # 保存采样率供暂停使用
        }
        self.stream_manager.set_volume(self.video_volume if is_file_playback else self.volume)

        callback = self._stream_audio_callback(audio_data, sample_rate, stream_info, is_file_playback)

        try:
            # 获取默认输出设备
            output_device = sd.default.device[1] if sd.default.device[1] is not None else sd.default.device[0]

            # 使用更大的缓冲区大小来减少underflow
            stream = sd.OutputStream(
                device=output_device,
                samplerate=sample_rate,
                channels=channels,
                callback=callback,
                finished_callback=lambda: self._safe_remove_stream(stream_info),
                blocksize=4096,  # 增加块大小
                latency='high',  # 使用高延迟模式
                dtype='float32'
            )
            logger.info(f"创建音频流到设备 {output_device}")
        except Exception as e:
            logger.warning(f"使用指定设备失败，回退到默认设备: {str(e)}")
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                callback=callback,
                finished_callback=lambda: self._safe_remove_stream(stream_info),
                blocksize=4096,
                latency='high',
                dtype='float32'
            )
            logger.info("创建音频流到默认设备")

        stream_info['stream'] = stream

        # 使用流管理器注册流
        stream_id = self.stream_manager.add_stream(stream_info)
        stream_info['id'] = stream_id

        return stream_info

    def _safe_remove_stream(self, stream_info):
        """安全地从活动流列表中移除指定的流"""
        try:
            if getattr(self, '_alive', False) and 'id' in stream_info:
                self.stream_manager.remove_stream(stream_info['id'])
        except Exception as e:
            logger.error(f"移除流时出错: {str(e)}")

    def _play_audio_data_with_status(self, audio_data, sample_rate, block, playback_type, array_info=None,
                                     is_file_playback=False):
        """
        带状态跟踪的音频数据播放 - 添加调试日志
        """
        try:
            logger.debug(f"开始播放音频数据，类型: {playback_type}, 长度: {len(audio_data)}, 采样率: {sample_rate}")

            # 更新播放状态
            if playback_type == PlaybackType.ARRAY:
                self._update_playback_status(playback_type, active=True, last_array=array_info)
            elif playback_type == PlaybackType.FILE:
                self._update_playback_status(playback_type, active=True)

            import torch
            if isinstance(audio_data, torch.Tensor):
                audio_data = audio_data.cpu().numpy().astype(np.float32)

            # 确保音频数据是连续的，避免性能问题
            audio_data = np.ascontiguousarray(audio_data)

            logger.debug(f"音频数据预处理完成，形状: {audio_data.shape}")

            if len(audio_data.shape) == 1:
                channels = 1
                audio_data = audio_data.reshape(-1, 1)
            else:
                channels = audio_data.shape[1]

            stream_info = self._create_audio_stream(audio_data, sample_rate, channels, is_file_playback)
            stream = stream_info['stream']
            stream.start()

            if block:
                # 阻塞播放
                try:
                    while stream.active and not stream_info['stop_event'].is_set():
                        if not getattr(self, '_alive', True):
                            break
                        time.sleep(0.05)  # 减少休眠时间以提高响应性

                    if stream.active:
                        try:
                            stream.stop()
                        except Exception as e:
                            logger.debug(f"停止流时出错: {str(e)}")
                    try:
                        stream.close()
                        self._safe_remove_stream(stream_info)
                    except Exception as e:
                        logger.debug(f"关闭流时出错: {str(e)}")

                    # 播放完成，更新状态
                    self._update_playback_status(playback_type, active=False,
                                                 completed=self._get_playback_count(playback_type) + 1)

                except Exception as e:
                    logger.error(f"播放过程中出错: {str(e)}")
                    stream_info['stop_event'].set()
                    if stream.active:
                        try:
                            stream.stop()
                        except Exception as e:
                            logger.debug(f"停止流时出错: {str(e)}")
                    try:
                        stream.close()
                    except Exception as e:
                        logger.debug(f"关闭流时出错: {str(e)}")
                    self._safe_remove_stream(stream_info)
                    self._update_playback_status(playback_type, active=False)
            else:
                # 非阻塞播放，启动监控线程
                def monitor_thread():
                    try:
                        while stream.active and not stream_info['stop_event'].is_set():
                            if not getattr(self, '_alive', True):
                                break
                            time.sleep(0.05)

                        if stream.active:
                            try:
                                stream.stop()
                            except Exception as e:
                                logger.debug(f"停止流时出错: {str(e)}")
                        try:
                            stream.close()
                        except Exception as e:
                            logger.debug(f"关闭流时出错: {str(e)}")
                        self._safe_remove_stream(stream_info)

                        # 播放完成，更新状态
                        self._update_playback_status(playback_type, active=False,
                                                     completed=self._get_playback_count(playback_type) + 1)

                    except Exception as e:
                        logger.error(f"监控线程出错: {str(e)}")
                        self._safe_remove_stream(stream_info)
                        self._update_playback_status(playback_type, active=False)

                threading.Thread(target=monitor_thread, daemon=True).start()

            logger.debug(f"音频播放启动成功，类型: {playback_type}")
            return True

        except Exception as e:
            error_msg = f"播放音频数据失败: {str(e)}"
            logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            self._update_playback_status(playback_type, active=False)
            return False

    def _get_playback_count(self, playback_type):
        """获取播放次数"""
        if playback_type == PlaybackType.FILE:
            return self.get_file_playback_count()
        elif playback_type == PlaybackType.ARRAY:
            return self.get_array_playback_count()
        elif playback_type == PlaybackType.TTS:
            return self.get_tts_playback_count()
        return 0

    def play_audio_data(self, audio_data, sample_rate=16000, block=False, playback_type=PlaybackType.ARRAY,
                        array_info=None, is_file_playback=True):
        """
        流式播放音频数据
        """
        return self._play_audio_data_with_status(audio_data, sample_rate, block,
                                                 playback_type=playback_type, array_info=array_info,
                                                 is_file_playback=is_file_playback)

    def play_audio_file(self, file_path, block=False):
        """
        流式播放音频文件
        """
        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"找不到音频文件: {file_path}")

            logger.info(f"开始播放音频文件: {file_path}")

            # 更新播放状态
            self._update_playback_status(PlaybackType.FILE, active=True, last_file=file_path)

            import soundfile as sf
            audio_data, sample_rate = sf.read(file_path)

            logger.debug(f"音频文件加载成功: 长度={len(audio_data)}, 采样率={sample_rate}")

            success = self.play_audio_data(
                audio_data,
                sample_rate=sample_rate,
                block=block,
                playback_type=PlaybackType.FILE,
                array_info=f"文件: {os.path.basename(file_path)}",
                is_file_playback=True
            )

            if not success:
                logger.error("播放音频文件失败")
                self._update_playback_status(PlaybackType.FILE, active=False)
            else:
                logger.info("音频文件播放启动成功")

            return success

        except Exception as e:
            error_msg = f"播放音频文件失败: {str(e)}"
            logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            self._update_playback_status(PlaybackType.FILE, active=False)
            return False

    def _stop_all_streams(self):
        """停止所有活动的音频流"""
        try:
            # 清除所有暂停状态
            for playback_type in PlaybackType:
                self._clear_pause_status(playback_type)

            # 获取所有流并清除
            all_streams = self.stream_manager.get_all_streams()

            for stream_ref in all_streams:
                self.stream_manager.safe_stop_stream(stream_ref)

            self.stream_manager.clear_all_streams()

            # 更新所有播放状态为未激活
            for playback_type in PlaybackType:
                self._update_playback_status(playback_type, active=False)

        except Exception as e:
            logger.error(f"停止所有流时发生错误: {str(e)}")
            # 确保无论如何都清除状态
            for playback_type in PlaybackType:
                self._update_playback_status(playback_type, active=False)

    def stop(self):
        """停止播放并清空队列"""
        try:
            self._stop_all_streams()
            self.stop_requested.set()

            with self.queue_lock:
                self.synthesis_queue = collections.deque(maxlen=10)
                self.playback_queue = queue.Queue()
                self.active_synthesis = 0
                self._update_queue_status()

            logger.info("播放已停止，队列已清空")
        except Exception as e:
            logger.error(f"停止播放时发生错误: {str(e)}")

    def shutdown(self):
        """关闭资源"""
        try:
            self._alive = False
            self.stop()
            self.executor.shutdown(wait=False)
            self._stop_all_streams()
            logger.info("TTS播放器已关闭")
        except Exception as e:
            logger.error(f"关闭资源时发生错误: {str(e)}")

    def _inference_with_prompt(self, text, prompt_speech_path, prompt_text=None):
        """使用示例音频的推理"""
        import torch
        from SparkTTS.sparktts.utils.token_parser import TASK_TOKEN_MAP
        try:
            if not self.is_initialized or any(x is None for x in [self.model, self.tokenizer, self.audio_tokenizer]):
                raise RuntimeError("模型未初始化完成")

            if not os.path.exists(prompt_speech_path):
                raise FileNotFoundError(f"找不到音频文件: {prompt_speech_path}")

            global_token_ids, semantic_token_ids = self.audio_tokenizer.tokenize(prompt_speech_path)

            if global_token_ids.nelement() == 0 or semantic_token_ids.nelement() == 0:
                raise ValueError("标记化返回空结果")

            global_tokens = "".join([f"<|bicodec_global_{i}|>" for i in global_token_ids.squeeze()])

            if prompt_text:
                semantic_tokens = "".join([f"<|bicodec_semantic_{i}|>" for i in semantic_token_ids.squeeze()])
                inputs = [
                    TASK_TOKEN_MAP["tts"],
                    "<|start_content|>",
                    prompt_text,
                    text,
                    "<|end_content|>",
                    "<|start_global_token|>",
                    global_tokens,
                    "<|end_global_token|>",
                    "<|start_semantic_token|>",
                    semantic_tokens,
                ]
            else:
                inputs = [
                    TASK_TOKEN_MAP["tts"],
                    "<|start_content|>",
                    text,
                    "<|end_content|>",
                    "<|start_global_token|>",
                    global_tokens,
                    "<|end_global_token|>",
                ]

            prompt = "".join(inputs)
            model_inputs = self.tokenizer([prompt], return_tensors="pt").to(self.device)

            generated_ids = self.model.generate(
                **model_inputs,
                max_new_tokens=3000,
                do_sample=True,
                top_k=50,
                top_p=0.95,
                temperature=0.8,
            )

            generated_ids = [output_ids[len(input_ids):] for input_ids, output_ids in
                             zip(model_inputs.input_ids, generated_ids)]
            predicts = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]

            semantic_matches = re.findall(r"bicodec_semantic_(\d+)", predicts)
            if not semantic_matches:
                raise ValueError("没有生成语义标记")

            pred_semantic_ids = torch.tensor([int(token) for token in semantic_matches]).long().unsqueeze(0)

            wav = self.audio_tokenizer.detokenize(
                global_token_ids.to(self.device).squeeze(0),
                pred_semantic_ids.to(self.device),
            )

            if len(wav.shape) > 1:
                wav = wav.squeeze()

            return wav

        except Exception as e:
            logger.error(f"提示推理失败: {str(e)}")
            raise

    def _inference_controllable(self, text, gender, pitch, speed):
        """参数控制推理"""
        import torch
        from SparkTTS.sparktts.utils.token_parser import TASK_TOKEN_MAP, GENDER_MAP, LEVELS_MAP
        try:
            if not self.is_initialized or any(x is None for x in [self.model, self.tokenizer, self.audio_tokenizer]):
                raise RuntimeError("模型未初始化完成")

            gender_id = GENDER_MAP[gender]
            pitch_level_id = LEVELS_MAP[pitch]
            speed_level_id = LEVELS_MAP[speed]

            prompt = "".join([
                TASK_TOKEN_MAP["controllable_tts"],
                "<|start_content|>",
                text,
                "<|end_content|>",
                "<|start_style_label|>",
                f"<|gender_{gender_id}|>",
                f"<|pitch_label_{pitch_level_id}|>",
                f"<|speed_label_{speed_level_id}|>",
                "<|end_style_label|>",
            ])

            model_inputs = self.tokenizer([prompt], return_tensors="pt").to(self.device)

            generated_ids = self.model.generate(
                **model_inputs,
                max_new_tokens=3000,
                do_sample=True,
                top_k=50,
                top_p=0.95,
                temperature=0.8,
            )

            generated_ids = [output_ids[len(input_ids):] for input_ids, output_ids in
                             zip(model_inputs.input_ids, generated_ids)]
            predicts = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]

            semantic_matches = re.findall(r"bicodec_semantic_(\d+)", predicts)
            global_matches = re.findall(r"bicodec_global_(\d+)", predicts)

            if not semantic_matches or not global_matches:
                raise ValueError("Insufficient tokens generated")

            pred_semantic_ids = torch.tensor([int(token) for token in semantic_matches]).long().unsqueeze(0)
            global_token_ids = torch.tensor([int(token) for token in global_matches]).long().unsqueeze(0).unsqueeze(0)

            wav = self.audio_tokenizer.detokenize(
                global_token_ids.to(self.device).squeeze(0),
                pred_semantic_ids.to(self.device),
            )

            if len(wav.shape) > 1:
                wav = wav.squeeze()

            return wav

        except Exception as e:
            logger.error(f"Controllable inference failed: {str(e)}")
            raise

    def add_text(self, text, **kwargs):
        """添加要播放的文本到合成队列"""
        if not text or not isinstance(text, str):
            return

        if not self.is_initialized:
            logger.warning("模型未初始化，文本已添加到队列，将在初始化完成后处理")

        with self.queue_lock:
            self.synthesis_queue.append((text, kwargs))
            self._update_queue_status()

        logger.info(f"添加到合成队列: '{text[:20]}...'")

        if self.is_initialized:
            self._process_synthesis_queue()

    def _process_synthesis_queue(self):
        """处理合成队列的内部方法"""
        if not self.is_initialized:
            return

        while len(self.synthesis_queue) > 0 and self.active_synthesis < self.max_workers:
            with self.queue_lock:
                if len(self.synthesis_queue) == 0:
                    break

                text, kwargs = self.synthesis_queue.popleft()
                self.active_synthesis += 1
                self._update_queue_status()

            self.executor.submit(self._synthesis_worker, text, kwargs)

    def _synthesis_worker(self, text, kwargs):
        """合成工作线程 - 增强数据验证"""
        try:
            self.synthesis_started.emit(text)

            prompt_path = kwargs.get('prompt_speech_path', self.default_prompt_speech)
            gender = kwargs.get('gender', self.gender)
            pitch = kwargs.get('pitch', 'moderate')
            speed = kwargs.get('speed', 'moderate')

            if not self.is_initialized:
                raise RuntimeError("模型未初始化完成，无法进行合成")

            if prompt_path and os.path.exists(prompt_path):
                wav = self._inference_with_prompt(text, prompt_path)
            else:
                wav = self._inference_controllable(text, gender, pitch, speed)

            # 验证合成的音频数据
            if wav is None:
                raise ValueError("合成返回的音频数据为None")

            import torch
            if isinstance(wav, torch.Tensor):
                wav = wav.cpu().numpy().astype(np.float32)

            if wav.size == 0:
                raise ValueError("合成返回的音频数据为空")

            logger.info(f"合成成功，音频数据长度: {len(wav)}")

            with self.queue_lock:
                self.playback_queue.put((text, wav, PlaybackType.TTS))
                self.active_synthesis -= 1
                self._update_queue_status()
                self.synthesis_completed.emit(text, True, "合成成功")
                logger.info(f"合成完成: '{text[:20]}...'")

            # 确保播放线程被启动
            self._process_playback_queue()

        except Exception as e:
            error_msg = f"合成失败: {str(e)}"
            logger.error(error_msg)
            with self.queue_lock:
                self.active_synthesis -= 1
                self._update_queue_status()
                self.synthesis_completed.emit(text, False, error_msg)
                self.error_occurred.emit(error_msg)

        finally:
            self._process_synthesis_queue()

    def _process_playback_queue(self):
        """改进的处理播放队列方法"""
        if not self.playback_active and not self.playback_queue.empty():
            self.playback_active = True
            logger.info("启动播放工作线程")
            threading.Thread(target=self._playback_worker, daemon=True, name="PlaybackWorker").start()
        elif self.playback_active and self.playback_queue.empty():
            # 如果播放线程正在运行但队列为空，记录状态但不停止线程
            logger.debug("播放线程运行中，队列暂时为空")
        else:
            logger.debug(f"播放状态: active={self.playback_active}, queue_size={self.playback_queue.qsize()}")

    def _playback_worker(self):
        """改进的播放工作线程 - 持续监听队列"""
        logger.debug("播放工作线程开始运行")

        # 设置超时时间，避免永久阻塞
        QUEUE_TIMEOUT = 5.0  # 5秒超时

        while not self.stop_requested.is_set() and getattr(self, '_alive', True):
            try:
                # 使用带超时的获取方式
                try:
                    text, wav, playback_type = self.playback_queue.get(timeout=QUEUE_TIMEOUT)
                    logger.info(f"从播放队列获取项目: {text[:20]}...")
                except queue.Empty:
                    # 超时后检查是否需要继续等待
                    if self.stop_requested.is_set() or not getattr(self, '_alive', True):
                        break
                    # 继续等待新的项目
                    continue

                self._update_queue_status()

                # 验证音频数据
                if wav is None:
                    logger.error(f"音频数据为None，跳过播放: {text[:20]}...")
                    self.playback_queue.task_done()
                    continue

                import torch
                if isinstance(wav, torch.Tensor):
                    wav = wav.cpu().numpy().astype(np.float32)

                if wav.size == 0:
                    logger.error(f"音频数据为空，跳过播放: {text[:20]}...")
                    self.playback_queue.task_done()
                    continue

                try:
                    self.playback_started.emit(text, playback_type.value)

                    if playback_type == PlaybackType.TTS:
                        self._update_playback_status(playback_type, active=True,
                                                     current_text=text, last_text=text)

                    logger.info(f"开始播放音频，数据长度: {len(wav)}")
                    success = self._play_audio_data_with_status(
                        wav,
                        sample_rate=16000,
                        block=True,
                        playback_type=PlaybackType.TTS,
                        array_info=f"TTS: {text[:30]}..." if playback_type == PlaybackType.TTS else None
                    )

                    if success:
                        logger.info(f"成功播放音频: '{text[:20]}...'")
                    else:
                        logger.error(f"播放音频失败: '{text[:20]}...'")
                        self.error_occurred.emit(f"播放失败: {text[:20]}...")

                    self.playback_finished.emit(text, playback_type.value)
                    logger.info(f"播放完成: '{text[:20]}...'")

                    if playback_type == PlaybackType.TTS:
                        self._update_playback_status(playback_type, active=False,
                                                     completed=self.get_tts_playback_count() + 1,
                                                     current_text=None)

                except Exception as e:
                    error_msg = f"播放失败: {str(e)}"
                    logger.error(error_msg)
                    self.error_occurred.emit(error_msg)

                    if playback_type == PlaybackType.TTS:
                        self._update_playback_status(playback_type, active=False)

                finally:
                    self.playback_queue.task_done()

            except Exception as e:
                logger.error(f"播放工作线程出错: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                time.sleep(0.1)  # 避免快速循环出错

        # 循环结束，重置播放活跃状态
        self.playback_active = False
        logger.debug("播放工作线程结束")

    def _update_queue_status(self):
        """更新队列状态并发出信号"""
        synth_count = len(self.synthesis_queue)
        playback_count = self.playback_queue.qsize()
        self.queue_updated.emit(synth_count, playback_count)

    def speak(self, text, **kwargs):
        """异步语音播报"""
        self.add_text(text, **kwargs)

    def speak_multiple(self, texts, **kwargs):
        """播报多条语音"""
        for text in texts:
            if text and isinstance(text, str):
                self.add_text(text, **kwargs)

    def clear_queue(self):
        """清空播放队列但保留当前播放项目"""
        with self.queue_lock:
            self.synthesis_queue = collections.deque(maxlen=10)
            temp_queue = queue.Queue()
            if not self.playback_queue.empty():
                current_item = self.playback_queue.get()
                temp_queue.put(current_item)
            self.playback_queue = temp_queue
            self._update_queue_status()

        logger.info("播放队列已清空，保留当前项目")

    def get_queue_status(self):
        """获取队列状态"""
        with self.queue_lock:
            return {
                "synthesis_queue": len(self.synthesis_queue),
                "playback_queue": self.playback_queue.qsize(),
                "active_synthesis": self.active_synthesis,
                "playback_active": self.playback_active
            }

    def is_busy(self):
        """检查是否正在播放或有待处理任务"""
        status = self.get_queue_status()
        playback_active = any(self.get_playback_status(t)['active'] for t in PlaybackType)
        return (status["synthesis_queue"] > 0 or status["playback_queue"] > 0 or
                status["active_synthesis"] > 0 or status["playback_active"] or playback_active)

    def wait_for_initialization(self, timeout=60):
        """等待模型初始化完成"""
        start_time = time.time()
        while not self.is_initialized and time.time() - start_time < timeout:
            if self.is_initializing:
                time.sleep(0.2)
            else:
                break

        if not self.is_initialized:
            logger.warning(f"模型初始化超时 ({timeout}秒)")
            return False

        logger.info("模型初始化完成")
        return True

    def get_initialization_status(self):
        """获取初始化状态"""
        return {
            "is_initialized": self.is_initialized,
            "is_initializing": self.is_initializing,
            "model_loaded": self.model is not None,
            "tokenizer_loaded": self.tokenizer is not None,
            "audio_tokenizer_loaded": self.audio_tokenizer is not None
        }


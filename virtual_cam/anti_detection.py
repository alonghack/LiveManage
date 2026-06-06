"""
虚拟摄像头防检测滤镜

对输出到虚拟摄像头的帧进行微妙的随机化处理，使自动化系统难以检测到
视频流来自虚拟摄像头。所有效果仅作用于虚拟摄像头输出，不影响主预览画面。

设计原则:
  - 加法类效果(噪声/亮度/伪影)合并为一个 float32 通道，单次转换+clip
  - 几何类效果(模糊/缩放)在全分辨率运行但仅偶尔触发
  - 噪声和伪影在降采样分辨率下生成后上采样
  - 所有效果有独立的随机化周期
  - 参数范围故意极窄，肉眼不可察觉
  - 1080p 目标: <5ms/帧
"""

import numpy as np
import cv2


class AntiDetectionFilter:
    """
    防检测帧后处理滤镜。

    仅应用于虚拟摄像头输出（不作用于主预览），通过微妙的随机
    视觉效果模拟真实物理摄像头的自然不稳定性。
    """

    NOISE_SIGMA_RANGE = (0.3, 1.8)
    NOISE_TARGET_GRID = 320              # 噪声生成网格的最大边长（自适应降采样）
    BRIGHTNESS_DELTA_RANGE = (-4, 4)
    BRIGHTNESS_PERIOD_RANGE = (60, 300)
    BLUR_KERNEL_RANGE = (3, 5)
    BLUR_INTERVAL_RANGE = (90, 600)
    BLUR_DURATION_RANGE = (2, 15)
    ZOOM_FACTOR_RANGE = (0.98, 1.02)
    ZOOM_INTERVAL_RANGE = (120, 900)
    ARTIFACT_BLOCK_SIZE_RANGE = (4, 16)
    ARTIFACT_INTENSITY_RANGE = (1, 8)
    ARTIFACT_INTERVAL_RANGE = (150, 1200)
    ARTIFACT_COVERAGE_RANGE = (0.01, 0.08)

    def __init__(self, settings: dict):
        self.settings = settings
        self._frame_count = 0
        self._rng = np.random.RandomState()
        self._reset_all_states()

    def _reset_all_states(self):
        r = self._rng

        self._brightness_offset = 0.0
        self._brightness_counter = 0
        self._brightness_period = max(1, r.randint(*self.BRIGHTNESS_PERIOD_RANGE))

        self._blur_active = False
        self._blur_counter = 0
        self._blur_interval = max(1, r.randint(*self.BLUR_INTERVAL_RANGE))
        self._blur_duration = max(1, r.randint(*self.BLUR_DURATION_RANGE))
        self._blur_kernel = 3  # blur kernel fixed to smallest

        self._zoom_factor = 1.0
        self._zoom_counter = 0
        self._zoom_interval = max(1, r.randint(*self.ZOOM_INTERVAL_RANGE))

        self._artifact_counter = 0
        self._artifact_interval = max(1, r.randint(*self.ARTIFACT_INTERVAL_RANGE))

    def process(self, frame: np.ndarray) -> np.ndarray:
        """
        对单帧应用启用的防检测效果。

        Args:
            frame: BGR 格式的输入帧 (uint8, HxWx3)。

        Returns:
            处理后的帧 (新数组)。输入不会被修改。
        """
        if frame is None or frame.size == 0:
            return frame

        h, w = frame.shape[:2]
        s = self.settings

        # ── 阶段 1: 收集所有加法类效果，单次 float 转换 ──
        need_additive = (
            s.get('vary_brightness', False) or
            s.get('add_noise', False) or
            s.get('random_artifacts', False)
        )

        if need_additive:
            # 一次转换到 int16（12MB，比 float32 的 24MB 快）
            result = frame.astype(np.int16)

            # 用 np.add(out=) 原地累加，避免创建临时数组
            if s.get('vary_brightness', False):
                offset = self._get_brightness_offset()
                if abs(offset) >= 0.5:
                    np.add(result, np.int16(round(offset)), out=result)

            if s.get('add_noise', False):
                noise = self._generate_noise_field(h, w)
                np.add(result, noise, out=result)

            if s.get('random_artifacts', False):
                artifacts = self._generate_artifact_field(h, w)
                if artifacts is not None:
                    np.add(result, artifacts, out=result)

            # 原地 clip + 转 uint8
            np.clip(result, 0, 255, out=result)
            result = result.astype(np.uint8)
        else:
            result = frame

        # ── 阶段 2: 几何类效果（在 uint8 上操作）──
        if s.get('add_focus_changes', False):
            result = self._apply_focus_changes(result)
        if s.get('random_zoom', False):
            result = self._apply_zoom(result)

        self._frame_count += 1
        return result

    # ── 加法效果生成器（返回 float32 噪声场，直接与帧相加）──

    def _get_brightness_offset(self) -> float:
        """更新亮度漂移状态，返回当前偏移量。"""
        r = self._rng
        self._brightness_counter += 1
        if self._brightness_counter >= self._brightness_period:
            self._brightness_counter = 0
            delta = r.uniform(*self.BRIGHTNESS_DELTA_RANGE)
            self._brightness_offset += delta * 0.3
            self._brightness_offset = np.clip(self._brightness_offset, -10, 10)
            self._brightness_period = max(1, r.randint(*self.BRIGHTNESS_PERIOD_RANGE))
        return self._brightness_offset

    def _generate_noise_field(self, h: int, w: int) -> np.ndarray:
        """生成自适应降采样高斯噪声场并上采样回全分辨率 (int16)。"""
        r = self._rng
        sigma = r.uniform(*self.NOISE_SIGMA_RANGE)
        # 自适应降采样: 保持噪声网格 ≤ TARGET_GRID 以控制计算成本
        max_dim = max(h, w)
        ds = max(1, max_dim // self.NOISE_TARGET_GRID)
        nh, nw = max(1, h // ds), max(1, w // ds)

        noise_small = (r.randn(nh, nw, 3) * sigma).astype(np.int16)
        return cv2.resize(noise_small, (w, h), interpolation=cv2.INTER_LINEAR)

    def _generate_artifact_field(self, h: int, w: int) -> np.ndarray | None:
        """偶尔生成块级压缩伪影噪声场。"""
        r = self._rng
        self._artifact_counter += 1

        if self._artifact_counter < self._artifact_interval:
            return None

        self._artifact_counter = 0
        self._artifact_interval = max(1, r.randint(*self.ARTIFACT_INTERVAL_RANGE))

        block_size = r.randint(*self.ARTIFACT_BLOCK_SIZE_RANGE)
        coverage = r.uniform(*self.ARTIFACT_COVERAGE_RANGE)
        intensity = r.randint(*self.ARTIFACT_INTENSITY_RANGE)

        ds_h = max(1, h // block_size)
        ds_w = max(1, w // block_size)

        mask = r.rand(ds_h, ds_w) < coverage
        noise = r.randint(-intensity, intensity + 1, size=(ds_h, ds_w, 3))
        noise = (noise * mask[:, :, np.newaxis]).astype(np.int16)

        return cv2.resize(noise, (w, h), interpolation=cv2.INTER_NEAREST)

    # ── 几何效果（在 uint8 上操作）──

    def _apply_focus_changes(self, frame: np.ndarray) -> np.ndarray:
        """周期性应用和取消轻微的高斯模糊。"""
        r = self._rng
        self._blur_counter += 1

        if self._blur_active:
            if self._blur_counter >= self._blur_duration:
                self._blur_active = False
                self._blur_counter = 0
                self._blur_interval = max(1, r.randint(*self.BLUR_INTERVAL_RANGE))
            else:
                blurred = cv2.GaussianBlur(
                    frame, (self._blur_kernel, self._blur_kernel), 0
                )
                return cv2.addWeighted(blurred, 0.4, frame, 0.6, 0)
        else:
            if self._blur_counter >= self._blur_interval:
                self._blur_active = True
                self._blur_counter = 0
                self._blur_kernel = 3  # blur kernel fixed to smallest
                self._blur_duration = max(1, r.randint(*self.BLUR_DURATION_RANGE))

        return frame

    def _apply_zoom(self, frame: np.ndarray) -> np.ndarray:
        """应用微小的随机缩放变化。"""
        r = self._rng
        self._zoom_counter += 1
        h, w = frame.shape[:2]

        if self._zoom_counter >= self._zoom_interval:
            target = r.uniform(*self.ZOOM_FACTOR_RANGE)
            self._zoom_factor += (target - self._zoom_factor) * 0.2
            self._zoom_counter = 0
            self._zoom_interval = max(1, r.randint(*self.ZOOM_INTERVAL_RANGE))

        if abs(self._zoom_factor - 1.0) < 0.001:
            return frame

        new_w = int(w * self._zoom_factor)
        new_h = int(h * self._zoom_factor)
        scaled = cv2.resize(frame, (new_w, new_h))

        if self._zoom_factor > 1.0:
            x_off = (new_w - w) // 2
            y_off = (new_h - h) // 2
            return scaled[y_off:y_off + h, x_off:x_off + w]
        else:
            pad_w = (w - new_w) // 2
            pad_h = (h - new_h) // 2
            result = cv2.copyMakeBorder(
                scaled, pad_h, h - new_h - pad_h,
                pad_w, w - new_w - pad_w,
                cv2.BORDER_REPLICATE
            )
            return result[:h, :w]

    def reset(self):
        """重置所有内部状态。在设置更改后调用。"""
        self._frame_count = 0
        self._reset_all_states()

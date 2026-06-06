import ctypes
import mss
from PIL import Image
from PIL.ImageQt import QPixmap, QImage
from PyQt6.QtGui import QPainter, QColor, QPen, QFont
from loguru import logger
from PyQt6.QtCore import pyqtSignal, Qt, QRect
from PyQt6.QtWidgets import QWidget

logger.debug("[模块] auxiliary.region 已导入")



class RegionSelector(QWidget):
    """区域选择器"""
    region_selected = pyqtSignal(dict)
    selection_cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        logger.debug("RegionSelector.__init__ 开始")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self.sct = mss.mss()
        self.monitor = self.sct.monitors[1]
        logger.debug(f"RegionSelector 主显示器: {self.monitor['width']}x{self.monitor['height']}")
        self.drawing = False
        self.start_pos = None
        self.end_pos = None
        self.selection_rect = None
        self.screen_scale_factor = self._get_screen_scaling_factor()
        logger.debug(f"屏幕缩放因子: {self.screen_scale_factor:.2f}")

        self._capture_screenshot()
        self.setGeometry(
            self.monitor["left"],
            self.monitor["top"],
            self.monitor["width"],
            self.monitor["height"]
        )
        logger.debug("RegionSelector 初始化完成")

    def _get_screen_scaling_factor(self):
        """获取屏幕缩放因子"""
        try:
            user32 = ctypes.windll.user32
            hdc = user32.GetDC(None)
            dpi_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
            user32.ReleaseDC(None, hdc)
            logger.debug(f"DPI X: {dpi_x}, 缩放因子: {dpi_x/96.0:.2f}")
            return dpi_x / 96.0
        except Exception as e:
            logger.warning(f"获取屏幕缩放因子失败, 使用默认值 1.0: {e}")
            return 1.0

    def _capture_screenshot(self):
        """捕获屏幕截图"""
        sct_img = self.sct.grab(self.monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)

        scaled_width = int(self.monitor["width"] / self.screen_scale_factor)
        scaled_height = int(self.monitor["height"] / self.screen_scale_factor)
        img = img.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

        self.screenshot = QPixmap.fromImage(
            QImage(img.tobytes(), img.width, img.height, QImage.Format.Format_RGB888)
        )

    def paintEvent(self, event):
        """绘制事件"""
        painter = QPainter(self)
        painter.setBrush(QColor(0, 0, 0, 120))
        painter.drawRect(self.rect())

        if self.selection_rect:
            src_rect = QRect(
                int(self.selection_rect.x() * self.screen_scale_factor),
                int(self.selection_rect.y() * self.screen_scale_factor),
                int(self.selection_rect.width() * self.screen_scale_factor),
                int(self.selection_rect.height() * self.screen_scale_factor)
            )

            painter.drawPixmap(self.selection_rect, self.screenshot, src_rect)

            border_rect = self.selection_rect.adjusted(0, 0, -1, -1)
            painter.setPen(QPen(QColor(255, 0, 0), 3))
            painter.drawRect(border_rect)

            actual_width = int(self.selection_rect.width() * self.screen_scale_factor)
            actual_height = int(self.selection_rect.height() * self.screen_scale_factor)
            size_text = f"{actual_width} × {actual_height}"

            painter.setPen(QColor(255, 255, 255))
            painter.setFont(QFont("Arial", 12))
            painter.drawText(self.selection_rect.x() + 10, self.selection_rect.y() + 20, size_text)

    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drawing = True
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            self.selection_rect = QRect(self.start_pos, self.end_pos).normalized()

    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if self.drawing:
            self.end_pos = event.pos()
            self.selection_rect = QRect(self.start_pos, self.end_pos).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if event.button() == Qt.MouseButton.LeftButton and self.drawing:
            self.drawing = False

            actual_width = int(self.selection_rect.width() * self.screen_scale_factor)
            actual_height = int(self.selection_rect.height() * self.screen_scale_factor)

            if actual_width < 10 or actual_height < 10:
                self.selection_cancelled.emit()
                self.close()
                return

            region = {
                "left": self.monitor["left"] + int(self.selection_rect.x() * self.screen_scale_factor),
                "top": self.monitor["top"] + int(self.selection_rect.y() * self.screen_scale_factor),
                "width": actual_width,
                "height": actual_height
            }

            logger.info(f"选择的区域: {region}")
            self.region_selected.emit(region)
            self.close()

    def keyPressEvent(self, event):
        """键盘事件"""
        if event.key() == Qt.Key.Key_Escape:
            self.selection_cancelled.emit()
            self.close()

    def showEvent(self, event):
        """显示事件"""
        super().showEvent(event)
        self.setGeometry(
            self.monitor["left"],
            self.monitor["top"],
            self.monitor["width"],
            self.monitor["height"]
        )
        self.raise_()
        self.activateWindow()

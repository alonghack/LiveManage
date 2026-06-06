import multiprocessing
import random
import time
from typing import Optional

from PIL.ImageQt import QPixmap
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QFont, QTextCursor
from PyQt6.QtWidgets import QWidget, QSizePolicy, QVBoxLayout, QGroupBox, QHBoxLayout, QButtonGroup, QRadioButton, \
    QLineEdit, QPushButton, QCheckBox, QTextEdit, QLabel, QMessageBox, QSlider
from loguru import logger

from auxiliary.models.KuaiShou.kwai_chat import KwaiLiveProcess
from auxiliary.models.TikTok_ZH.tiktok_chat import TikTokLiveProcess

logger.debug("[模块] auxiliary.screen 已导入")


class VideoWidget(QWidget):
    """自定义视频显示组件，支持主图像+蒙版，保持等比例缩放"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(512, 384)
        self.setStyleSheet("background-color: black;")

        self.pixmap: Optional[QPixmap] = None
        self.mask_pixmap: Optional[QPixmap] = None
        self.show_mask: bool = False

    def setImage(self, image):
        """设置要显示的图像"""
        if image and not image.isNull():
            self.pixmap = QPixmap.fromImage(image)
        else:
            self.pixmap = None
        self.updateGeometry()   # 强制刷新布局
        self.update()

    def setMaskImage(self, image):
        """设置蒙版图像"""
        if image and not image.isNull():
            self.mask_pixmap = QPixmap.fromImage(image)
        else:
            self.mask_pixmap = None
        self.updateGeometry()
        self.update()

    def setShowMask(self, show: bool):
        """设置是否显示蒙版"""
        self.show_mask = show
        self.update()

    def paintEvent(self, event):
        """重绘事件，绘制视频帧和蒙版"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # 背景填充黑色
        painter.fillRect(self.rect(), QColor(0, 0, 0))

        # 绘制主图像（等比例缩放 + 居中）
        if self.pixmap and not self.pixmap.isNull():
            scaled_size = self.pixmap.size()
            scaled_size.scale(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x = (self.width() - scaled_size.width()) // 2
            y = (self.height() - scaled_size.height()) // 2
            painter.drawPixmap(x, y, scaled_size.width(), scaled_size.height(), self.pixmap)

        # 绘制蒙版（如果开启）
        if self.show_mask and self.mask_pixmap and not self.mask_pixmap.isNull():
            scaled_size = self.mask_pixmap.size()
            scaled_size.scale(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x = (self.width() - scaled_size.width()) // 2
            y = (self.height() - scaled_size.height()) // 2
            painter.drawPixmap(x, y, scaled_size.width(), scaled_size.height(), self.mask_pixmap)

    def clear(self):
        """清除画面"""
        self.pixmap = None
        self.mask_pixmap = None
        self.update()



class ChatWidget(QWidget):
    """聊天对话框组件"""

    def __init__(self, main_window, tts, parent=None):
        super().__init__(parent)

        self.main_window = main_window


        self.is_auto_reply = False
        self.broadcast_status = False

        # 弹幕队列
        self.danmu_queue = []
        self.message_queue = multiprocessing.Queue()  # 使用进程安全的队列

        self.init_ui()


    def init_ui(self):
        """初始化聊天界面"""
        layout = QVBoxLayout(self)

        # 右侧：自动回复控制
        reply_group = QGroupBox("弹幕自动回复")
        reply_layout = QVBoxLayout()
        reply_group.setLayout(reply_layout)
        layout.addWidget(reply_group)  # 1/3宽度

        # 添加一个选择直播平台的单选按钮
        terrace_layout = QHBoxLayout()
        terrace_button_group = QButtonGroup(self)
        self.terrace_tiktok = QRadioButton("抖音")
        terrace_button_group.addButton(self.terrace_tiktok)
        self.terrace_tiktok.setChecked(True)
        terrace_layout.addWidget(self.terrace_tiktok)
        self.terrace_kwai = QRadioButton("快手")
        terrace_button_group.addButton(self.terrace_kwai)
        terrace_layout.addWidget(self.terrace_kwai)
        self.terrace_bili = QRadioButton("哔哩哔哩")
        terrace_button_group.addButton(self.terrace_bili)
        terrace_layout.addWidget(self.terrace_bili)
        reply_layout.addLayout(terrace_layout)

        # 添加一个网址输入框，输入框后加一个获取连接按钮，点击按钮后获取网址的连接信息
        url_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("请输入直播网址")
        url_layout.addWidget(self.url_input)
        self.get_url_btn = QPushButton("获取连接")
        self.get_url_btn.clicked.connect(self.start_danmu_client)
        url_layout.addWidget(self.get_url_btn)

        # 添加断开连接按钮
        self.disconnect_btn = QPushButton("断开连接")
        self.disconnect_btn.clicked.connect(self.stop_danmu_client)
        self.disconnect_btn.setStyleSheet("background-color: #ff6b6b; color: white;")  # 红色按钮
        url_layout.addWidget(self.disconnect_btn)

        reply_layout.addLayout(url_layout)

        # 控制区域
        control_layout = QHBoxLayout()

        self.auto_reply_check = QCheckBox("开启自动回复")
        self.auto_reply_check.stateChanged.connect(self.toggle_auto_reply)
        control_layout.addWidget(self.auto_reply_check)

        self.tts_check = QCheckBox("启用语音播报")
        self.tts_check.setChecked(self.main_window.tts.is_initialized)
        self.tts_check.stateChanged.connect(self.toggle_tts)
        control_layout.addWidget(self.tts_check)

        test_btn = QPushButton("测试弹幕")
        test_btn.clicked.connect(self.test_danmu)
        control_layout.addWidget(test_btn)

        reply_layout.addLayout(control_layout)

        # 聊天记录显示区域
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setFont(QFont("Microsoft YaHei", 10))

        # 输入区域
        input_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("输入消息...")
        self.chat_input.returnPressed.connect(self.send_message)

        send_btn = QPushButton("发送")
        send_btn.clicked.connect(self.send_message)

        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(send_btn)

        # 添加到reply_group布局中
        reply_layout.addWidget(QLabel("聊天对话:"))
        reply_layout.addWidget(self.chat_display)
        reply_layout.addLayout(input_layout)

    def start_danmu_client(self):
        """启动弹幕客户端"""
        live_url = self.url_input.text().strip()
        if not live_url:
            QMessageBox.warning(self, "错误", "❌ 直播地址不能为空")
            return

        try:
            # 在开始新连接之前，先停止已有的连接（如果存在）
            self.stop_danmu_client()
            # 检查直播平台
            if self.terrace_tiktok.isChecked() and "douyin" in live_url:
                client = TikTokLiveProcess(live_url, self.message_queue)
                self.danmu_client = client  # 保存客户端引用
            elif self.terrace_bili.isChecked() and "bilibili" in live_url:
                QMessageBox.warning(self, "提示", "❌ 哔站暂未支持...")

                self.main_window.add_log(f"[系统] 暂不支持哔哩哔哩直播间", "magenta")
            elif self.terrace_kwai.isChecked() and "kuaishou" in live_url:
                client = KwaiLiveProcess(live_url, self.message_queue)
                self.danmu_client = client
            else:
                QMessageBox.warning(self, "警告", "选择平台与填写URL不匹配")
                return

            # 启动客户端
            logger.debug("启动客户端")
            self.main_window.add_log(f"[系统] 弹幕客户端启动中: {live_url}", "green")
            self.danmu_client.warning_signal.connect(self.main_window.show_warning_message)
            self.danmu_client.start()

        except Exception as ex:
            logger.error(f"接口信息获取失败: {str(ex)}")
            self.danmu_client.stop()
            self.main_window.add_log(f"[错误] 信息获取失败，请重新尝试", "red")

    def receive_danmu(self):
        """接收弹幕消息"""
        try:
            prompt_path = self.main_window.tts.get_current_prompt()
            while not self.message_queue.empty():
                msg = self.message_queue.get_nowait()
                # 更新最后弹幕活动时间
                self.last_danmu_time = time.time()
                # 处理消息类型
                if msg["type"] == "user_enter":
                    self.main_window.add_log(f"[系统] {msg['content']}", "magenta")
                    if self.broadcast_status:
                        self.main_window.tts.add_text(f"欢迎 {msg['user_name']} 进入直播间！", prompt_speech_path=prompt_path)
                elif msg["type"] == "gift":
                    self.main_window.add_log(f"[礼物] {msg['content']}", "orange")
                    if self.broadcast_status:
                        self.main_window.tts.add_text(f"感谢 {msg['user_name']} 赠送的 {msg['content'].split()[-1]}",
                                      prompt_speech_path=prompt_path)
                elif msg["type"] == "comment":
                    self.main_window.add_log(f"[弹幕] {msg['user_name']}: {msg['content']}", "white")
                    # 如果开启自动回复，处理这条弹幕
                    if self.is_auto_reply:
                        self.process_danmu_message(msg['user_name'], msg['content'])
                elif msg["type"] == "like":
                    self.main_window.add_log(f"[点赞] {msg['content']}", "green")
                    if self.broadcast_status:
                        self.main_window.tts.add_text(f"感谢 {msg['user_name']} 的点赞！", prompt_speech_path=prompt_path)
                elif msg["type"] == "concern":
                    self.main_window.add_log(f"[关注] {msg['content']}", "green")
                    if self.broadcast_status:
                        self.main_window.tts.add_text(f"感谢 {msg['user_name']} 的关注！", prompt_speech_path=prompt_path)
                elif msg["type"] == "share":
                    self.main_window.add_log(f"[分享] {msg['content']}", "magenta")
                    if self.broadcast_status:
                        self.main_window.tts.add_text(f"感谢 {msg['user_name']} 的分享！", prompt_speech_path=prompt_path)
                elif msg["type"] == "system" and ("连接已建立" in msg["content"] or "弹幕启动中" in msg["content"]):
                    self.main_window.add_log(f"[系统] {msg['content']}", "magenta")
        except Exception as e:
            logger.error(f"处理弹幕消息时出错: {str(e)}")

    def stop_danmu_client(self):
        """安全停止弹幕客户端并断开连接

        1. 停止客户端并断开连接
        2. 清空消息队列
        3. 更新UI状态
        4. 确保所有资源被正确释放
        """
        try:
            # 1. 检查并停止客户端
            if hasattr(self, 'danmu_client') and self.danmu_client:
                # 安全停止客户端
                try:
                    self.danmu_client.stop()
                    self.main_window.tts.stop()
                    logger.info("弹幕客户端已安全停止")
                    self.main_window.add_log("[系统] 已断开弹幕连接", "yellow")
                except Exception as e:
                    logger.error(f"停止弹幕客户端时发生错误: {str(e)}")
                    self.main_window.add_log(f"[错误] 停止客户端失败: {str(e)}", "red")
                finally:
                    # 确保客户端引用被清除
                    self.danmu_client = None
            else:
                self.main_window.add_log("[系统] 没有活动的弹幕连接", "yellow")

            # 2. 清空消息队列
            try:
                while not self.message_queue.empty():
                    self.message_queue.get_nowait()
                logger.info("消息队列已清空")
            except Exception as e:
                logger.error(f"清空消息队列时发生错误: {str(e)}")
        except Exception as e:
            logger.error(f"断开弹幕连接时发生严重错误: {str(e)}")
            self.main_window.add_log(f"[严重错误] 断开连接失败: {str(e)}", "red")
        finally:
            # 确保所有资源被释放
            if hasattr(self, 'danmu_client') and self.danmu_client:
                self.danmu_client = None


    def toggle_auto_reply(self, state):
        """切换自动回复状态"""
        self.is_auto_reply = state
        status = "开启" if self.is_auto_reply else "关闭"
        self.main_window.add_log(f"[系统] 自动回复功能已{status}", "white")

    def toggle_tts(self, state):
        """切换语音播报状态 - 修复初始化状态检查"""
        try:
            # 检查TTS是否已初始化
            print("tts状态：", state)
            if not hasattr(self.main_window, 'tts') or not self.main_window.tts:
                self.tts_check.setChecked(False)
                QMessageBox.warning(self, "错误", "TTS系统未初始化")
                return

            # 更新状态
            self.broadcast_status = state
            status = "开启" if state else "关闭"
            self.main_window.add_log(f"[系统] 语音播报功能已{status}", "white")

        except Exception as e:
            logger.error(f"切换语音播报状态出错: {str(e)}")
            self.main_window.add_log(f"[错误] 语音播报切换失败: {str(e)}", "red")
            self.tts_check.setChecked(False)

    def test_danmu(self):
        """测试弹幕功能"""
        # 模拟弹幕消息
        messages = [
            {"user_name": "测试用户", "content": "主播你好！"},
        ]

        # 更新最后活动时间
        self.main_window.last_danmu_time = time.time()

        for msg in messages:
            self.main_window.add_log(f"[弹幕] {msg['user_name']}: {msg['content']}", "cyan")
            print(f"[弹幕] {msg['user_name']}: {msg['content']}")
            print("回复：", self.is_auto_reply)
            if self.is_auto_reply:
                self.process_danmu_message(msg['user_name'], msg['content'])
            # 将测试弹幕加入队列
            self.danmu_queue.append(msg)


    def process_danmu_message(self, user_name, content):
        """处理弹幕消息并回复"""
        # 检查关键词回复
        for keyword, details in self.main_window.keyword_responses.items():
            responses = details.get("responses", [])
            if responses and keyword in content:
                reply_mode = self.main_window.reply_settings.get(keyword, {}).get("recover", True)

                if reply_mode:  # 随机回复
                    response = random.choice(responses)
                else:  # 指定回复
                    idx = self.main_window.reply_settings.get(keyword, {}).get("specific_index", 0)
                    response = responses[idx] if idx < len(responses) else responses[0]

                # 发送回复
                self.main_window.add_log(f"[回复] {user_name}: {response}", "green")

                # 语音播报 - 使用选择的示例文件
                if self.broadcast_status:
                    # 获取当前选择的示例文件路径
                    prompt_path = self.main_window.tts.get_current_prompt()

                    # 使用选择的示例文件进行语音合成
                    logger.info(f"使用语音示例文件: {prompt_path}")
                    self.main_window.tts.add_text(response, prompt_speech_path=prompt_path)

                # 找到匹配后停止检查其他关键词
                break

        # 如果不开启冷场播报，使用事件名称作为关键词匹配弹幕
        if not self.main_window.enable_idle_commentary:
            # 遍历所有事件（顶级项）
            for i in range(self.main_window.commentary_tree.topLevelItemCount()):
                event_item = self.main_window.commentary_tree.topLevelItem(i)
                event_name = event_item.text(1)  # 事件名称在第二列

                # 如果弹幕内容包含事件名称
                if event_name in content:
                    # 随机选择该事件下的一个解说内容
                    if event_item.childCount() > 0:
                        child_index = random.randint(0, event_item.childCount() - 1)
                        phrase_item = event_item.child(child_index)
                        phrase_data = phrase_item.data(0, Qt.ItemDataRole.UserRole)
                        phrase_content = phrase_data['phrase']

                        # 播放解说
                        prompt_path = self.main_window.tts.get_current_prompt()

                        # 使用选择的示例文件进行语音合成
                        self.main_window.tts.add_text(phrase_content, prompt_speech_path=prompt_path)
                        self.main_window.add_log(f"[解说] 触发事件 {event_name}: {phrase_content[:50]}...", "yellow")

                        # 找到匹配后停止检查其他事件
                        break


    def send_message(self):
        """发送消息"""
        message = self.chat_input.text().strip()
        if message:
            self.add_message("用户", message)
            self.chat_input.clear()

    def add_message(self, sender, message):
        """添加消息到聊天记录"""
        timestamp = time.strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {sender}: {message}"

        self.chat_display.append(formatted_message)
        # 滚动到底部
        self.chat_display.moveCursor(QTextCursor.MoveOperation.End)


class ClickableSlider(QSlider):
    """支持点击跳转的进度条"""
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            x = event.pos().x()
            ratio = x / self.width()
            value = int(ratio * (self.maximum() - self.minimum()) + self.minimum())
            self.setValue(value)
            self.sliderMoved.emit(value)
            self.sliderReleased.emit()
        super().mousePressEvent(event)
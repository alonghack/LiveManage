import os
import sqlite3

import random

from loguru import logger
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

logger.debug("[模块] auxiliary.utils 已导入")


def user_agent():
    """模拟多个浏览器的User-Agent - 不依赖外部库"""

    user_agents = {
        'chrome': [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ],
        'firefox': [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
        ],
        'edge': [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        ]
    }

    # 随机选择一个浏览器类型
    browser = random.choice(['chrome', 'firefox', 'edge'])
    # 从该浏览器类型中随机选择一个User-Agent
    return random.choice(user_agents[browser])


def get_torch_device():
    import torch
    # 检查GPU
    if torch.cuda.is_available() and torch.cuda.device_count() > 0:
        gpu_memory = torch.cuda.get_device_properties(0).total_memory
        logger.info(f"检测到GPU: {torch.cuda.get_device_name(0)}")
        if gpu_memory > 8e9:  # 8GB
            logger.info(f"GPU内存足够( {gpu_memory / 1e9:.2f}GB >= 8GB)，使用GPU运行")
            device = torch.device("cuda")
            # 尝试使用float32而不是float16，避免精度问题
            torch_dtype = torch.float32
        else:
            logger.info(f"GPU内存不足8GB( {gpu_memory / 1e9:.2f}GB < 8GB)，使用CPU运行")
            device = torch.device("cpu")
            torch_dtype = torch.float32
    else:
        device = torch.device("cpu")
        torch_dtype = torch.float32
        logger.info("未检测到GPU，使用CPU运行")

    return device, torch_dtype


class UIUpdater(QObject):
    """安全UI更新器 - 确保所有UI更新在主线程执行"""
    update_description_signal = pyqtSignal(str)
    update_volume_signal = pyqtSignal(str)
    update_rate_signal = pyqtSignal(str)
    update_pitch_signal = pyqtSignal(str)
    update_combo_signal = pyqtSignal(list)
    set_combo_index_signal = pyqtSignal(int)
    update_status_signal = pyqtSignal(str)
    update_voice_signal = pyqtSignal(str)  # 新增的语音信号

    # 新增信号
    update_checkbox_signal = pyqtSignal(bool)
    refresh_keyword_list_signal = pyqtSignal()
    update_idle_setting_signal = pyqtSignal(bool)

    show_message_signal = pyqtSignal(str, str)  # title, message
    enable_button_signal = pyqtSignal(bool)

    def __init__(self, window):
        super().__init__()
        self.window = window

        # 连接信号
        self.update_description_signal.connect(self.safe_update_description)
        self.update_volume_signal.connect(self.safe_update_volume)
        self.update_rate_signal.connect(self.safe_update_rate)
        self.update_pitch_signal.connect(self.safe_update_pitch)
        self.update_combo_signal.connect(self.safe_update_combo)
        self.set_combo_index_signal.connect(self.safe_set_combo_index)
        self.update_status_signal.connect(self.safe_update_status)
        self.update_voice_signal.connect(self.safe_update_voice)  # 连接新的语音信号

        # 连接信号到槽函数
        self.update_checkbox_signal.connect(self.update_checkbox)
        self.refresh_keyword_list_signal.connect(self.refresh_keyword_list)
        self.update_idle_setting_signal.connect(self.update_idle_setting)

        # 在UIUpdater的__init__方法中添加
        self.show_message_signal.connect(self.show_message_signal_handler)
        self.enable_button_signal.connect(self.enable_button_signal_handler)

    def show_message_signal_handler(self, title, message):
        """处理显示消息框信号"""
        QMessageBox.warning(self.window, title, message)

    def enable_button_signal_handler(self, enabled):
        """处理启用按钮信号"""
        self.window.get_url_btn.setEnabled(enabled)

    def update_checkbox(self, state):
        """更新TTS复选框状态"""
        if self.window and hasattr(self.window, 'tts_check'):
            self.window.tts_check.setChecked(state)

    def refresh_keyword_list(self):
        """刷新关键词列表"""
        if self.window and hasattr(self.window, 'refresh_keyword_list'):
            self.window.refresh_keyword_list()

    def update_idle_setting(self, enabled):
        """更新冷场设置状态"""
        if (self.window and
                hasattr(self.window, 'idle_enable_check') and
                hasattr(self.window, 'idle_time_input')):
            self.window.idle_enable_check.setChecked(enabled)
            self.window.idle_time_input.setEnabled(enabled)

    def safe_update_description(self, description):
        """安全更新语音描述"""
        if self.window and hasattr(self.window, 'voice_description'):
            self.window.voice_description.setText(description)

    def safe_update_volume(self, value):
        """安全更新音量显示"""
        if self.window and hasattr(self.window, 'volume_value'):
            self.window.volume_value.setText(value)

    def safe_update_rate(self, value):
        """安全更新语速显示"""
        if self.window and hasattr(self.window, 'rate_value'):
            self.window.rate_value.setText(value)

    def safe_update_pitch(self, value):
        """安全更新音调显示"""
        if self.window and hasattr(self.window, 'pitch_value'):
            self.window.pitch_value.setText(value)

    def safe_update_combo(self, voices):
        """安全更新下拉框内容"""
        if self.window and hasattr(self.window, 'voice_combo'):
            self.window.voice_combo.clear()
            self.window.voice_combo.addItems(voices)

    def safe_set_combo_index(self, index):
        """安全设置下拉框索引"""
        if self.window and hasattr(self.window, 'voice_combo'):
            self.window.voice_combo.setCurrentIndex(index)

    def safe_update_status(self, status):
        """安全更新状态栏"""
        if self.window and hasattr(self.window, 'status_label'):
            self.window.status_label.setText(status)

    def safe_update_voice(self, voice_name):
        """安全更新当前语音显示"""
        if self.window and hasattr(self.window, 'voice_label'):
            self.window.voice_label.setText(f"当前语音: {voice_name}")
        if self.window and hasattr(self.window, 'voice_combo'):
            # 如果下拉框中有该语音，设置选中
            index = self.window.voice_combo.findText(voice_name)
            if index >= 0:
                self.window.voice_combo.setCurrentIndex(index)


class SQLiteManager:
    def __init__(self, db_name='sqlite_db/auto_reply.sqlite_db'):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.initialize_database()

    def initialize_database(self):
        # 创建关键词表
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY,
                keyword TEXT UNIQUE,
                recover BOOLEAN DEFAULT 1,
                specific_index INTEGER DEFAULT 0
            )
        ''')

        # 创建回复表
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY,
                keyword_id INTEGER,
                response TEXT,
                FOREIGN KEY (keyword_id) REFERENCES keywords(id)
            )
        ''')

        # 创建语音设置表
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS voice_settings (
                id INTEGER PRIMARY KEY DEFAULT 1,
                enable_tts BOOLEAN DEFAULT 1,
                voice_index INTEGER DEFAULT 0,
                rate INTEGER DEFAULT 150,
                volume REAL DEFAULT 0.9,
                pitch REAL DEFAULT 1.2
            )
        ''')

        # 创建游戏解说表 - 添加唯一约束
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS commentary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                phrase TEXT NOT NULL,
                UNIQUE(event, phrase)  -- 防止重复添加相同的事件和解说
            )
        ''')

        # 初始化语音设置
        self.cursor.execute('INSERT OR IGNORE INTO voice_settings (id) VALUES (1)')

        self.conn.commit()

    def get_keywords(self):
        self.cursor.execute('SELECT * FROM keywords')
        columns = [col[0] for col in self.cursor.description]
        return [dict(zip(columns, row)) for row in self.cursor.fetchall()]

    def get_keyword_responses(self, keyword_id):
        self.cursor.execute('SELECT * FROM responses WHERE keyword_id = ?', (keyword_id,))
        columns = [col[0] for col in self.cursor.description]
        return [dict(zip(columns, row)) for row in self.cursor.fetchall()]

    def add_keyword(self, keyword):
        try:
            self.cursor.execute('INSERT INTO keywords (keyword) VALUES (?)', (keyword,))
            self.conn.commit()
            return self.cursor.lastrowid
        except sqlite3.IntegrityError:
            return None

    def delete_keyword(self, keyword):
        # 先获取关键字ID
        self.cursor.execute('SELECT id FROM keywords WHERE keyword = ?', (keyword,))
        result = self.cursor.fetchone()
        if not result:
            return False

        keyword_id = result[0]

        # 删除所有关联回复
        self.cursor.execute('DELETE FROM responses WHERE keyword_id = ?', (keyword_id,))
        # 删除关键词
        self.cursor.execute('DELETE FROM keywords WHERE id = ?', (keyword_id,))
        self.conn.commit()
        return True

    def update_keyword(self, old_keyword, new_keyword):
        self.cursor.execute(
            'UPDATE keywords SET keyword = ? WHERE keyword = ?',
            (new_keyword, old_keyword)
        )
        self.conn.commit()
        return self.cursor.rowcount > 0

    def add_response(self, keyword, response):
        self.cursor.execute('SELECT id FROM keywords WHERE keyword = ?', (keyword,))
        result = self.cursor.fetchone()
        if not result:
            return False

        keyword_id = result[0]
        self.cursor.execute(
            'INSERT INTO responses (keyword_id, response) VALUES (?, ?)',
            (keyword_id, response)
        )
        self.conn.commit()
        return True

    def delete_response(self, keyword, response):
        self.cursor.execute('SELECT id FROM keywords WHERE keyword = ?', (keyword,))
        result = self.cursor.fetchone()
        if not result:
            return False

        keyword_id = result[0]
        self.cursor.execute(
            'DELETE FROM responses WHERE keyword_id = ? AND response = ?',
            (keyword_id, response)
        )
        self.conn.commit()
        return True

    def update_response(self, keyword, old_response, new_response):
        self.cursor.execute('SELECT id FROM keywords WHERE keyword = ?', (keyword,))
        result = self.cursor.fetchone()
        if not result:
            return False

        keyword_id = result[0]
        self.cursor.execute(
            'UPDATE responses SET response = ? WHERE keyword_id = ? AND response = ?',
            (new_response, keyword_id, old_response)
        )
        self.conn.commit()
        return True

    def get_voice_settings(self):
        self.cursor.execute('SELECT * FROM voice_settings WHERE id = 1')
        row = self.cursor.fetchone()
        if row:
            columns = [col[0] for col in self.cursor.description]
            return dict(zip(columns, row))
        return {}

    def set_voice_setting(self, name, value):
        VALID_COLUMNS = {'enable_tts', 'voice_index', 'rate', 'volume', 'pitch'}
        if name not in VALID_COLUMNS:
            raise ValueError(f"Invalid column name: {name}")
        self.cursor.execute(f'UPDATE voice_settings SET {name} = ? WHERE id = 1', (value,))
        self.conn.commit()

    # 游戏解说词管理方法
    def add_commentary(self, event, phrase):
        """添加解说词"""
        try:
            self.cursor.execute(
                'INSERT INTO commentary (event, phrase) VALUES (?, ?)',
                (event, phrase)
            )
            self.conn.commit()
            return self.cursor.lastrowid
        except sqlite3.IntegrityError:
            # 如果已存在相同的解说词，返回None
            return None

    def get_commentary(self):
        """获取所有解说词"""
        self.cursor.execute('SELECT id, event, phrase FROM commentary ORDER BY event, id')
        columns = [col[0] for col in self.cursor.description]
        rows = self.cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def get_commentary_by_id(self, commentary_id):
        """根据ID获取解说词"""
        self.cursor.execute('SELECT id, event, phrase FROM commentary WHERE id = ?', (commentary_id,))
        columns = [col[0] for col in self.cursor.description]
        row = self.cursor.fetchone()
        if row:
            return dict(zip(columns, row))
        return None

    def get_commentary_by_event(self, event):
        """获取指定事件下的所有解说词"""
        self.cursor.execute('SELECT id, event, phrase FROM commentary WHERE event = ?', (event,))
        columns = [col[0] for col in self.cursor.description]
        rows = self.cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def update_commentary(self, commentary_id, new_event=None, new_phrase=None):
        """更新解说词"""
        try:
            if new_event and new_phrase:
                # 同时更新事件和解说内容
                self.cursor.execute(
                    'UPDATE commentary SET event = ?, phrase = ? WHERE id = ?',
                    (new_event, new_phrase, commentary_id)
                )
            elif new_event:
                # 只更新事件名称
                self.cursor.execute(
                    'UPDATE commentary SET event = ? WHERE id = ?',
                    (new_event, commentary_id)
                )
            elif new_phrase:
                # 只更新解说内容
                self.cursor.execute(
                    'UPDATE commentary SET phrase = ? WHERE id = ?',
                    (new_phrase, commentary_id)
                )
            else:
                return False

            self.conn.commit()
            return self.cursor.rowcount > 0
        except sqlite3.IntegrityError:
            # 如果更新后的事件和解说内容已存在，返回False
            return False

    def update_event_name(self, old_event, new_event):
        """更新事件名称（批量更新）"""
        try:
            self.cursor.execute(
                'UPDATE commentary SET event = ? WHERE event = ?',
                (new_event, old_event)
            )
            self.conn.commit()
            return self.cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    def delete_commentary(self, commentary_id):
        """删除单个解说词"""
        self.cursor.execute('DELETE FROM commentary WHERE id = ?', (commentary_id,))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def delete_event(self, event):
        """删除整个事件及其所有解说词"""
        self.cursor.execute('DELETE FROM commentary WHERE event = ?', (event,))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def delete_all_commentary(self):
        """删除所有解说词"""
        self.cursor.execute('DELETE FROM commentary')
        self.conn.commit()
        return self.cursor.rowcount > 0

    def search_commentary(self, search_text, search_type="phrase"):
        """
        搜索解说词
        search_type: "event" 或 "phrase"
        """
        if search_type == "event":
            self.cursor.execute(
                "SELECT id, event, phrase FROM commentary WHERE event LIKE ?",
                (f'%{search_text}%',)
            )
        elif search_type == "phrase":
            self.cursor.execute(
                "SELECT id, event, phrase FROM commentary WHERE phrase LIKE ?",
                (f'%{search_text}%',)
            )
        else:
            # 默认搜索解说内容
            self.cursor.execute(
                "SELECT id, event, phrase FROM commentary WHERE phrase LIKE ?",
                (f'%{search_text}%',)
            )

        columns = [col[0] for col in self.cursor.description]
        rows = self.cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def close(self):
        """显式关闭数据库连接"""
        if self.conn:
            try:
                # 提交所有未提交的事务
                self.conn.commit()
                # 关闭游标
                if self.cursor:
                    self.cursor.close()
                # 关闭连接
                self.conn.close()
                self.conn = None
                self.cursor = None
            except sqlite3.Error as e:
                print(f"关闭数据库连接时出错: {str(e)}")
            finally:
                # 确保资源被释放
                self.conn = None
                self.cursor = None

    def __del__(self):
        """析构函数，确保数据库连接被关闭"""
        try:
            if hasattr(self, 'conn') and self.conn is not None:
                self.conn.close()
        except Exception:
            # 忽略析构过程中的异常
            pass
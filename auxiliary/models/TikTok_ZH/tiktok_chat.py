

import subprocess
from functools import partial
from PyQt6.QtCore import pyqtSignal, QObject
from auxiliary.utils import user_agent

subprocess.Popen = partial(subprocess.Popen, encoding="utf-8")

import os
import time
import gzip
import hashlib
import threading
import urllib.parse
import re
import ssl
import execjs
import requests
from websocket import WebSocketApp
import websocket
from loguru import logger
import queue  # 添加queue模块

from auxiliary.models.TikTok_ZH.tik_tok_pb2 import ChatMessage, GiftMessage, LikeMessage, MemberMessage, \
    SocialMessage, RoomUserSeqMessage, FansclubMessage, EmojiChatMessage, RoomMessage, RoomStatsMessage, \
    RoomRankMessage, ControlMessage, RoomStreamAdaptationMessage, PushFrame, Response


class TikTokLiveManager(QObject):

    def __init__(self, live_url, message_queue):
        super().__init__()  # 调用父类初始化
        self.ttwid = None
        self.room_id = None
        self.live_url = live_url
        self.live_id = live_url.split("/")[-1]
        self.message_queue = message_queue
        self.UserAgent = user_agent()
        self.cookie = {
            "__ac_nonce": "068b6070c002a5bc4a8df",
        }
        self.header = {
            "user-agent": self.UserAgent,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-ch-ua": "\"Not;A=Brand\";v=\"99\", \"Microsoft Edge\";v=\"139\", \"Chromium\";v=\"139\"",
            "origin": "https://live.douyin.com",
            "referer": f"https://live.douyin.com/{self.live_id}",
        }
        self.stop_event = None
        self.wss = None

    def get_room_id(self):
        """
        根据直播间的地址获取到真正的直播间roomId
        """
        try:
            logger.info(self.live_url)
            response = requests.get(self.live_url, cookies=self.cookie, headers=self.header, timeout=10)
            if response.status_code == 200:
                # 获取ttwid
                if "ttwid" in response.cookies:
                    self.ttwid = response.cookies["ttwid"]
                else:
                    # 尝试从set-cookie头中获取
                    set_cookie = response.headers.get('set-cookie', '')
                    ttwid_match = re.search(r'ttwid=([^;]+)', set_cookie)
                    if ttwid_match:
                        self.ttwid = ttwid_match.group(1)

                # 获取room_id
                room_id_match = re.findall(r'"roomId":"(\d+)"', response.text)
                if not room_id_match:
                    room_id_match = re.findall(r'roomId\\":\\"(\d+)\\"', response.text)
                if not room_id_match:
                    room_id_match = re.findall(r'room_id[": ]+(\d+)', response.text)

                if room_id_match:
                    self.room_id = room_id_match[0]
                    logger.info(f"成功获取 room_id: {self.room_id}, ttwid: {self.ttwid}")
                    return True
                else:
                    logger.info("未找到 room_id，请检查直播间URL是否正确")
                    return False
            else:
                logger.info(f"请求失败，状态码: {response.status_code}")
                return False
        except Exception as e:
            logger.info(f"获取 room_id 失败: {e}")
            return False

    def on_open(self, ws):
        logger.info("WebSocket 连接成功！")
        # 启动心跳线程
        self.heartbeat_thread = threading.Thread(target=self.send_heartbeat, args=(ws,), daemon=True)
        self.heartbeat_thread.start()
        self.message_queue.put({
            "type": "system",
            "user_name": "系统通知",
            "content": "直播间连接已建立 . . ."
        })

    def send_heartbeat(self, ws):
        """发送心跳包"""
        while True:
            # 检查是否应该停止
            if self.stop_event and self.stop_event.is_set():
                break

            try:
                # 创建心跳帧
                heartbeat = PushFrame()
                heartbeat.payload_type = 'hb'
                ws.send(heartbeat.SerializeToString(), websocket.ABNF.OPCODE_BINARY)
                logger.info("【√】发送心跳包")
            except Exception as e:
                logger.info(f"【X】心跳包发送失败: {e}")
                break
            time.sleep(10)  # 抖音通常10秒发送一次心跳

    def on_close(self, ws, close_status_code, close_msg):
        logger.info(f"WebSocket 连接关闭，状态码: {close_status_code}, 消息: {close_msg}")
        ws.close()

    def on_error(self, ws, error):
        logger.info(f"WebSocket 连接错误: {error}")
        ws.close()

    def on_message(self, ws, message):
        """
        接收到数据
        :param ws: websocket实例
        :param message: 数据
        """

        # 检查是否应该停止
        if self.stop_event and self.stop_event.is_set():
            ws.close()
            return

        # 根据proto结构体解析对象
        package = PushFrame().parse(message)
        response = Response().parse(gzip.decompress(package.payload))

        # 返回直播间服务器链接存活确认消息，便于持续获取数据
        if response.need_ack:
            ack = PushFrame(log_id=package.log_id,
                            payload_type='ack',
                            payload=response.internal_ext.encode('utf-8')
                            ).SerializeToString()
            ws.send(ack, websocket.ABNF.OPCODE_BINARY)

        # 根据消息类别解析消息体
        for msg in response.messages_list:
            method = msg.method
            try:
                {
                    'WebcastChatMessage': self._parseChatMsg,  # 聊天消息
                    'WebcastGiftMessage': self._parseGiftMsg,  # 礼物消息
                    'WebcastLikeMessage': self._parseLikeMsg,  # 点赞消息
                    'WebcastMemberMessage': self._parseMemberMsg,  # 进入直播间消息
                    'WebcastSocialMessage': self._parseSocialMsg,  # 关注消息
                    'WebcastRoomUserSeqMessage': self._parseRoomUserSeqMsg,  # 直播间统计
                    'WebcastFansclubMessage': self._parseFansclubMsg,  # 粉丝团消息
                    'WebcastControlMessage': self._parseControlMsg,  # 直播间状态消息
                    'WebcastEmojiChatMessage': self._parseEmojiChatMsg,  # 聊天表情包消息
                    'WebcastRoomStatsMessage': self._parseRoomStatsMsg,  # 直播间统计信息
                    'WebcastRoomMessage': self._parseRoomMsg,  # 直播间信息
                    'WebcastRoomRankMessage': self._parseRankMsg,  # 直播间排行榜信息
                    'WebcastRoomStreamAdaptationMessage': self._parseRoomStreamAdaptationMsg,  # 直播间流配置
                }.get(method)(msg.payload)
            except Exception:
                pass

    def _parseChatMsg(self, payload):
        """聊天消息"""
        message = ChatMessage().parse(payload)
        user_name = message.user.nick_name
        user_id = message.user.id
        content = message.content
        logger.info(f"【聊天msg】[{user_id}]{user_name}: {content}")
        self.message_queue.put({
            "type": "comment",
            "user_name": user_name,
            "content": f"{content}"
        })

    def _parseGiftMsg(self, payload):
        """礼物消息"""
        message = GiftMessage().parse(payload)
        user_name = message.user.nick_name
        gift_name = message.gift.name
        gift_cnt = message.combo_count
        logger.info(f"【礼物msg】{user_name} 送出了 {gift_name}x{gift_cnt}")
        self.message_queue.put({
            "type": "gift",
            "user_name": user_name,
            "content": f"{user_name} 赠送了 {gift_cnt}个 礼物{gift_name}"
        })

    def _parseLikeMsg(self, payload):
        '''点赞消息'''
        message = LikeMessage().parse(payload)
        user_name = message.user.nick_name
        count = message.count
        logger.info(f"【点赞msg】{user_name} 点了{count}个赞")
        self.message_queue.put({
            "type": "like",
            "user_name": user_name,
            "content": f"{user_name} 点赞了直播间"
        })

    def _parseMemberMsg(self, payload):
        '''进入直播间消息'''
        message = MemberMessage().parse(payload)
        user_name = message.user.nick_name
        user_id = message.user.id
        gender = ["女", "男"][message.user.gender]
        logger.info(f"【进场msg】{user_name} 进入了直播间")
        self.message_queue.put({
            "type": "user_enter",
            "user_name": user_name,
            "content": f"🚪 {gender} {user_name} 进入了直播间"
        })

    def _parseSocialMsg(self, payload):
        '''关注消息'''
        message = SocialMessage().parse(payload)
        user_name = message.user.nick_name
        logger.info(f"【关注msg】{user_name} 关注了主播")
        self.message_queue.put({
            "type": "concern",
            "user_name": user_name,
            "content": f"🚪 {user_name} 关注了主播"
        })

    def _parseRoomUserSeqMsg(self, payload):
        '''直播间统计'''
        message = RoomUserSeqMessage().parse(payload)
        current = message.total
        total = message.total_pv_for_anchor
        logger.info(f"【统计msg】当前观看人数: {current}, 累计观看人数: {total}")
        self.message_queue.put({
            "type": "audience_count",
            "user_name": "系统",
            "content": {"current": current, "total": total}
        })

    def _parseFansclubMsg(self, payload):
        '''粉丝团消息'''
        message = FansclubMessage().parse(payload)
        content = message.content
        logger.info(f"【粉丝团msg】 {content}")

    def _parseEmojiChatMsg(self, payload):
        '''聊天表情包消息'''
        message = EmojiChatMessage().parse(payload)
        emoji_id = message.emoji_id
        user = message.user
        common = message.common
        default_content = message.default_content
        logger.info(f"【聊天表情包id】 {emoji_id},user：{user},common:{common},default_content:{default_content}")

    def _parseRoomMsg(self, payload):
        message = RoomMessage().parse(payload)
        common = message.common
        room_id = common.room_id
        logger.info(f"【直播间msg】直播间id:{room_id}")

    def _parseRoomStatsMsg(self, payload):
        message = RoomStatsMessage().parse(payload)
        display_long = message.display_long
        logger.info(f"【直播间统计msg】{display_long}")

    def _parseRankMsg(self, payload):
        message = RoomRankMessage().parse(payload)
        ranks_list = message.ranks_list
        logger.info(f"【直播间排行榜msg】{ranks_list}")

    def _parseControlMsg(self, payload):
        '''直播间状态消息'''
        message = ControlMessage().parse(payload)

        if message.status == 3:
            logger.info("直播间已结束")
            self.message_queue.put({
                "type": "audience_count",
                "user_name": "系统",
                "content": "当前直播已结束"
            })
            self.stop()

    def _parseRoomStreamAdaptationMsg(self, payload):
        message = RoomStreamAdaptationMessage().parse(payload)
        adaptationType = message.adaptation_type
        logger.info(f'直播间adaptation: {adaptationType}')

    def get_room_status(self):
        """
        获取直播间开播状态:
        room_status: 2 直播已结束
        room_status: 0 直播进行中
        """
        url = ('https://live.douyin.com/webcast/room/web/enter/?aid=6383'
               '&app_name=douyin_web&live_id=1&device_platform=web&language=zh-CN&enter_from=web_live'
               '&cookie_enabled=true&screen_width=1536&screen_height=864&browser_language=zh-CN&browser_platform=Win32'
               '&browser_name=Edge&browser_version=133.0.0.0'
               f'&web_rid={self.live_id}'
               f'&room_id_str={self.room_id}'
               '&enter_source=&is_need_double_stream=false&insert_task_id=&live_reason='
               '&msToken=&a_bogus=')
        resp = requests.get(url, headers={
            'User-Agent': self.UserAgent,
            'Cookie': f'ttwid={self.ttwid};'
        })
        data = resp.json().get('data')
        if data:
            room_status = data.get('room_status')
            user = data.get('user')
            user_id = user.get('id_str')
            nickname = user.get('nickname')
            logger.info(f"【{nickname}】[{user_id}]直播间：{['正在直播', '已结束'][bool(room_status)]}.")

    def generate_wss_url(self):
        """生成WebSocket连接URL"""
        if not self.room_id:
            logger.info("未获取到room_id，无法生成WebSocket URL")
            return None

        base_url = "wss://webcast5-ws-web-lq.douyin.com/webcast/im/push/v2/"
        timestamp = int(time.time() * 1000)

        params = {
            "app_name": "douyin_web",
            "version_code": "180800",
            "webcast_sdk_version": "1.3.0",
            "update_version_code": "1.3.0",
            "compress": "gzip",
            "device_platform": "web",
            "cookie_enabled": "true",
            "screen_width": "1536",
            "screen_height": "864",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Mozilla",
            "browser_version": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "browser_online": "true",
            "tz_name": "Asia/Shanghai",
            "cursor": f"t-{timestamp}",
            "internal_ext": f"internal_src:dim|wss_push_room_id:{self.room_id}|wss_push_did:{hashlib.md5(str(timestamp).encode()).hexdigest()}",
            "host": "https://live.douyin.com",
            "aid": "6383",
            "live_id": "1",
            "did_rule": "3",
            "endpoint": "live_pc",
            "support_wrds": "1",
            "user_unique_id": f"{hashlib.md5(str(timestamp).encode()).hexdigest()}",
            "im_path": "/webcast/im/fetch/",
            "identity": "audience",
            "room_id": self.room_id,
            "heartbeatDuration": "0"
        }

        # 生成签名
        sign_params = ["live_id", "aid", "version_code", "webcast_sdk_version",
                       "room_id", "sub_room_id", "sub_channel_id", "did_rule",
                       "user_unique_id", "device_platform", "device_type", "ac", "identity"]

        sign_str = ",".join([f"{param}={params.get(param, '')}" for param in sign_params])
        md5_param = hashlib.md5(sign_str.encode()).hexdigest()

        try:
            # 获取sign值
            sign_js_path = os.path.join(os.path.dirname(__file__), "sign0.js")
            if os.path.exists(sign_js_path):
                with open(sign_js_path, "r", encoding="utf-8") as f:
                    sign_code = f.read()
                signature = execjs.compile(sign_code).call("get_sign", md5_param)
                params["signature"] = signature
            else:
                logger.info("签名文件不存在，使用默认签名")
                params["signature"] = "default_sign"
        except Exception as e:
            logger.info(f"生成签名失败: {e}")
            params["signature"] = "default_sign"

        # 构建完整的URL
        query_string = "&".join([f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()])
        return f"{base_url}?{query_string}"

    def start(self, stop_event=None):
        """启动WebSocket连接，支持通过stop_event停止"""
        if not self.room_id:
            logger.info("未找到 room_id，请检查直播间URL是否正确或重试")
            return

        wss_url = self.generate_wss_url()
        if not wss_url:
            logger.info("无法生成WebSocket URL")
            return

        logger.info(f"连接URL: {wss_url}")

        # 设置WebSocket头信息
        websocket_headers = {
            "User-Agent": self.UserAgent,
            "Origin": "https://live.douyin.com",
            "Referer": f"https://live.douyin.com/{self.live_id}",
            "Sec-WebSocket-Extensions": "permessage-deflate; client_max_window_bits",
        }

        # 保存停止事件
        self.stop_event = stop_event

        # 建立WebSocket连接
        self.wss = WebSocketApp(
            url=wss_url,
            header=websocket_headers,
            cookie=f"ttwid={self.ttwid}",
            on_open=self.on_open,
            on_message=self.on_message,
            on_close=self.on_close,
            on_error=self.on_error,
        )

        try:
            # 运行WebSocket，禁用SSL验证
            self.wss.run_forever(
                sslopt={"cert_reqs": ssl.CERT_NONE},
                ping_interval=10,
                ping_timeout=5,
            )
        except Exception as e:
            logger.info(f"WebSocket运行失败: {e}")
        finally:
            logger.info("WebSocket连接结束")

    def stop(self):
        """停止WebSocket连接"""
        if self.wss:
            self.wss.close()

        # 设置停止事件（如果存在）
        if self.stop_event:
            self.stop_event.set()


class TikTokLiveProcess(QObject):
    warning_signal = pyqtSignal(str)

    def __init__(self, live_url, message_queue):
        super().__init__()
        self.live_url = live_url
        self.process = None
        self.message_queue = message_queue
        self.stop_event = threading.Event()  # 添加停止事件
        self.tiktok_live_manager = None  # 保存TikTokLiveManager实例

    def start(self):
        """启动 TikTokLiveManager 线程"""
        if self.process and self.process.is_alive():
            logger.info("进程已在运行中")
            return False

        # 重置停止事件
        self.stop_event.clear()
        logger.info("弹幕启动中, 重置停止事件")

        self.message_queue.put({
            "type": "system",
            "user_name": "系统",
            "content": "弹幕启动中 . . ."
        })
        logger.info("弹幕启动中, 创建新线程")

        # 创建新线程
        self.process = threading.Thread(
            target=self._run_tiktok_live,
            daemon=True
        )
        logger.info("弹幕启动中, 开启新线程")
        self.process.start()

        self.message_queue.put({
            "type": "system",
            "user_name": "系统",
            "content": "TikTokLiveManager 进程已启动."
        })
        logger.info("TikTokLiveManager 进程已启动")
        return True

    def stop(self):
        """停止 TikTokLiveManager 线程"""
        if self.process and self.process.is_alive():
            # 设置停止事件
            self.stop_event.set()

            # 停止TikTokLiveManager
            if self.tiktok_live_manager:
                self.tiktok_live_manager.stop()

            # 等待线程结束
            self.process.join(timeout=5)  # 最多等待5秒

            # 清空消息队列以避免task_done错误
            self._clear_message_queue()

            logger.info("TikTokLiveManager 进程已停止")
            return True
        else:
            logger.info("没有正在运行的进程")
            return False

    def _clear_message_queue(self):
        """清空消息队列以避免task_done错误"""
        try:
            while True:
                self.message_queue.get_nowait()
        except queue.Empty:
            pass  # 队列已空

    def _run_tiktok_live(self):
        """在子线程中运行 TikTokLiveManager"""
        # 创建 TikTokLiveManager 实例并启动
        self.tiktok_live_manager = TikTokLiveManager(self.live_url, self.message_queue)

        # 检查是否应该停止
        if self.stop_event.is_set():
            return

        room_id = self.tiktok_live_manager.get_room_id()
        if not room_id:
            self.warning_signal.emit("未找到 room_id，请检查直播间URL是否正确或重试")
            return

        # 检查是否应该停止
        if self.stop_event.is_set():
            return

        # 启动 TikTokLiveManager，传递停止事件
        self.tiktok_live_manager.start(self.stop_event)


# 使用示例
if __name__ == '__main__':
    # 创建 TikTokLiveProcess 实例
    live_url = "https://live.douyin.com/418415460243"
    message_queue = queue.Queue()  # 使用线程安全的队列
    tiktok_process = TikTokLiveProcess(live_url, message_queue)

    # 启动线程
    tiktok_process.start()

    # 运行一段时间后停止
    time.sleep(60)  # 运行60秒
    tiktok_process.stop()
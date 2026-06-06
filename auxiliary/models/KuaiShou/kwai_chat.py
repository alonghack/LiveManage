# import subprocess
# from functools import partial
#
# from PyQt6.QtCore import pyqtSignal, QObject
#
# subprocess.Popen = partial(subprocess.Popen, encoding="utf-8")
#
# import multiprocessing
# import os.path
# import sys
#
# import json
# import threading
# import time
# import ssl
# from datetime import datetime
# from loguru import logger
# from collections import defaultdict
#
# import websocket
# from websocket import WebSocketApp
#
# # from google.protobuf.json_format import MessageToDict
# from auxiliary.models.KuaiShou.live_kwai_pb2 import (SocketMessage, PayloadType, SCWebFeedPush, CSWebEnterRoom, SCWebEnterRoomAck,
#                                                      SimpleUserInfo, WebCommentFeed, WebGiftFeed, SCCommentZoneRichText,
#                                                      SCWebLiveWatchingUsers, SCWebError, SCInfo, CSWebError, SCWebHeartbeatAck,
#                                                      SCWebCurrentRedPackFeed, CSWebHeartbeat, SCWebSuspectedViolation,
#                                                      SCLiveWarningMaskStatusChangedAudience, SCWebGuessOpened, WebCommentFeedShowType,
#                                                      ConfigSwitchType, SCWebLiveSpecialAccountConfigState, SCWebGuessClosed,
#                                                      WebUserPauseType, WebPauseType, AssistantType, StyleType, WebLiveAssistantType,
#                                                      SCWebAuthorPause, SCWebAuthorResume, SCWebPipStarted, SCWebPipEnded,
#                                                      SCWebGuessClosed, SCWebRideChanged, SCWebBetChanged, SCWebBetClosed,
#                                                      SCInteractiveChatSwitchBiz, SCInteractiveChatClosed,
#                                                      SCLiveMultiPkStatistic, PicUrl, UserInfo, LiveAudienceState)
#
#
# from auxiliary.models.KuaiShou.get_kwai_token import get_new_token
#
# # 移除默认日志处理器
# # logger.remove(0)
# # logger.add(sys.stderr, format="{time} | {level} | {message}",level="SUCCESS")
#
#
#
# class KwaiLiveDanmuClient:
#     def __init__(self):
#         """初始化客户端"""
#         self.message_queue = None
#         self.live_url = None
#         self.session_page_id = None
#         self.ws_url = None
#         self.headers = {}
#         self.cookies = {}
#         self.gift_map = {} # 全部礼物墙
#         self.token = None
#         self.live_stream_id = None
#         self.websocket = None
#         self.heartbeat_active = True
#         self.room_entered = False
#         self._stop_flag = False  # 添加停止标志
#         logger.info("⚡ 弹幕客户端已初始化")
#
#
#         # 用户状态跟踪
#         self.all_entered_users = set()  # 在直播间的用户ID
#         self.current_online_users = set()  # 当前在线用户ID
#         self.user_enter_count = defaultdict(int)  # 用户进入次数统计
#
#         # 消息统计
#         self.message_stats = defaultdict(int)
#         self.last_print_stats_time = time.time()
#         self.stats_interval = 30  # 每30秒打印一次统计
#
#         # 调试模式
#         self.debug = True
#
#         # 错误处理和状态控制
#         self.retry_count = 0
#         self.max_retries = 3
#
#         # 错误定义
#         self.fatal_errors = {
#             101: "TOKEN_EXPIRED",
#             201: "ROOM_NOT_EXIST",
#             301: "NO_PERMISSION",
#             401: "SERVER_OVERLOAD"
#         }
#
#         # 心跳控制
#         self.hb_interval = 20000  # 默认20秒心跳间隔
#
#         # 直播间状态
#         self.live_status = {
#             "is_paused": False,
#             "is_pip": False,
#             "warning_mask": False,
#             "violation": False,
#             "audience_count": "0",
#             "like_count": "0"
#         }
#
#         # 红包信息
#         self.redpacks = {}
#         self.active_guess = None
#
#     def set_connection_params(self, ws_url, live_stream_id, token,
#             headers, cookies, session_page_id, live_urls, gift_maps, message_queue):
#         """设置连接参数"""
#         self.ws_url = ws_url
#         self.live_stream_id = live_stream_id
#         self.token = token
#         self.headers = headers
#         self.cookies = cookies if isinstance(cookies, dict) else {}
#         self.session_page_id = session_page_id
#         self.live_url = live_urls
#         self.gift_map = gift_maps
#         self.message_queue = message_queue
#
#         logger.info(f"✅ 已设置连接参数: URL={ws_url}")
#         logger.info(f"📡 直播流ID: {live_stream_id}")
#         logger.info(f"🔑 Token: {token[:15]}...")
#         return True
#
#     def create_enter_room_message(self):
#         """创建进入房间消息 (CSWebEnterRoom)"""
#         try:
#             enter_room = CSWebEnterRoom(
#                 token=self.token,
#                 live_stream_id=self.live_stream_id,
#                 page_id=self.session_page_id
#             )
#
#             # 创建包装消息
#             socket_msg = SocketMessage(
#                 payload_type=PayloadType.CS_ENTER_ROOM,
#                 compression_type=0,  # NONE
#                 payload=enter_room.SerializeToString()
#             )
#
#             return socket_msg.SerializeToString()
#         except Exception as e:
#             logger.error(f"❌ 创建进入房间消息失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"创建进入房间消息失败: {str(e)}"
#             })
#             return None
#
#     def create_heartbeat_message(self):
#         """创建符合协议的心跳包"""
#         try:
#             heartbeat_msg = CSWebHeartbeat()
#             heartbeat_msg.timestamp = int(time.time() * 1000)
#
#             # 创建包装消息
#             socket_msg = SocketMessage(
#                 payload_type=PayloadType.CS_HEARTBEAT,
#                 compression_type=0,  # NONE
#                 payload=heartbeat_msg.SerializeToString()
#             )
#             return socket_msg.SerializeToString()
#         except Exception as e:
#             logger.error(f"❌ 创建心跳消息失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"创建心跳消息失败: {str(e)}"
#             })
#             return None
#
#     def start_heartbeat(self):
#         """启动心跳线程（使用服务器指定的间隔）"""
#
#         def heartbeat_loop():
#             logger.info("💓 心跳线程已启动")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": "心跳线程已启动"
#             })
#             while self.heartbeat_active and self.websocket and self.websocket.sock and self.websocket.sock.connected:
#                 try:
#                     # 动态心跳间隔（毫秒转秒）
#                     interval = self.hb_interval / 1000.0
#
#                     # 只在成功进入房间后发送心跳
#                     heartbeat_msg = self.create_heartbeat_message()
#                     if heartbeat_msg:
#                         self.websocket.send(heartbeat_msg, opcode=websocket.ABNF.OPCODE_BINARY)
#                         if self.debug:
#                             logger.debug(f"💓 发送心跳包 (间隔: {interval:.1f}秒)")
#                             self.message_queue.put({
#                                 "type": "system",
#                                 "user_name": "系统",
#                                 "content": f"发送心跳包 (间隔: {interval:.1f}秒)"
#                             })
#
#                     # 按服务器要求的时间间隔等待
#                     time.sleep(interval)
#
#                 except websocket.WebSocketConnectionClosedException:
#                     logger.error("🛑 WebSocket连接已关闭，停止心跳")
#                     self.message_queue.put({
#                         "type": "system",
#                         "user_name": "系统",
#                         "content": "WebSocket连接已关闭，停止心跳"
#                     })
#
#                     break
#                 except Exception as e:
#                     logger.error(f"⚠️ 心跳发送失败: {str(e)}")
#                     self.message_queue.put({
#                         "type": "system",
#                         "user_name": "系统",
#                         "content": f"心跳发送失败: {str(e)}"
#                     })
#                     time.sleep(1)  # 错误后短暂延迟
#             logger.info("💔 心跳线程已停止")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": "心跳线程已停止"
#             })
#
#         threading.Thread(target=heartbeat_loop, daemon=True, name="HeartbeatThread").start()
#
#     def on_open(self, ws):
#         """WebSocket打开回调"""
#         logger.success("✅ WebSocket连接已建立")
#         self.message_queue.put({
#             "type": "system",
#             "user_name": "系统",
#             "content": "WebSocket连接已建立"
#         })
#
#         # 发送进入房间消息
#         try:
#             enter_msg = self.create_enter_room_message()
#             if enter_msg:
#                 ws.send(enter_msg, opcode=websocket.ABNF.OPCODE_BINARY)
#                 logger.info("📤 已发送进入房间请求")
#                 self.message_queue.put({
#                     "type": "system",
#                     "user_name": "系统",
#                     "content": "直播间连接已建立 . . ."
#                 })
#             else:
#                 logger.error("❌ 无法创建进入房间消息，连接可能失败")
#                 self.message_queue.put({
#                     "type": "system",
#                     "user_name": "系统",
#                     "content": "无法创建进入房间消息，连接可能失败"
#                 })
#         except Exception as e:
#             logger.error(f"❌ 发送进入房间失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"发送进入房间失败: {str(e)}"
#             })
#
#     def on_message(self, ws, message):
#         """WebSocket消息回调 - 支持快手直播全协议处理"""
#         try:
#             # 解析顶层消息
#             socket_msg = SocketMessage()
#             socket_msg.ParseFromString(message)
#
#             # 获取消息类型名称
#             try:
#                 msg_type = PayloadType.Name(socket_msg.payload_type)
#             except ValueError:
#                 msg_type = f"UNKNOWN_TYPE_{socket_msg.payload_type}"
#                 logger.debug(f"⚠️ 未知消息类型: {socket_msg.payload_type}")
#                 self.message_queue.put({
#                     "type": "system",
#                     "user_name": "系统",
#                     "content": f"未知消息类型: {socket_msg.payload_type}"
#                 })
#
#             # 更新消息统计
#             self.message_stats[msg_type] += 1
#
#             # ====================== 消息类型分发处理 ======================
#             # 连接管理类消息
#             if msg_type == "SC_ENTER_ROOM_ACK":
#                 self._handle_enter_room_ack(socket_msg.payload)
#             elif msg_type == "SC_HEARTBEAT_ACK":
#                 self._handle_heartbeat_ack(socket_msg.payload)
#             elif msg_type == "SC_PING_ACK":
#                 self._handle_ping_ack(socket_msg.payload)
#
#             # 用户状态类消息
#             elif msg_type == "SC_AUTHOR_PAUSE":
#                 self._handle_author_pause(socket_msg.payload)
#             elif msg_type == "SC_AUTHOR_RESUME":
#                 self._handle_author_resume(socket_msg.payload)
#             elif msg_type == "SC_PIP_STARTED":
#                 self._handle_pip_started(socket_msg.payload)
#             elif msg_type == "SC_PIP_ENDED":
#                 self._handle_pip_ended(socket_msg.payload)
#
#             # 实时互动类消息
#             elif msg_type == "SC_FEED_PUSH":
#                 self._handle_feed_push(socket_msg.payload)
#             elif msg_type == "SC_RED_PACK_FEED":
#                 self._handle_red_pack_feed(socket_msg.payload)
#             # elif msg_type == "SC_COMMENT_ZONE_RICH_TEXT":
#             #     self._handle_rich_text_comment(socket_msg.payload)
#             elif msg_type == "SC_LIVE_WATCHING_LIST":
#                 self._handle_watching_list(socket_msg.payload)
#
#             # 活动与游戏类消息
#             elif msg_type == "SC_GUESS_OPENED":
#                 self._handle_guess_opened(socket_msg.payload)
#             elif msg_type == "SC_GUESS_CLOSED":
#                 self._handle_guess_closed(socket_msg.payload)
#             elif msg_type == "SC_RIDE_CHANGED":
#                 self._handle_ride_changed(socket_msg.payload)
#             elif msg_type == "SC_BET_CHANGED":
#                 self._handle_bet_changed(socket_msg.payload)
#             elif msg_type == "SC_BET_CLOSED":
#                 self._handle_bet_closed(socket_msg.payload)
#             elif msg_type == "SC_LIVE_MULTI_PK_STATISTIC":
#                 self._handle_pk_statistic(socket_msg.payload)
#
#             # 系统与控制类消息
#             elif msg_type == "SC_ERROR":
#                 self._handle_error(socket_msg.payload)
#             elif msg_type == "SC_INFO":
#                 self._handle_info(socket_msg.payload)
#             elif msg_type == "SC_SUSPECTED_VIOLATION":
#                 self._handle_violation(socket_msg.payload)
#             elif msg_type == "SC_LIVE_SPECIAL_ACCOUNT_CONFIG_STATE":
#                 self._handle_config_state(socket_msg.payload)
#             elif msg_type == "SC_LIVE_WARNING_MASK_STATUS_CHANGED_AUDIENCE":
#                 self._handle_warning_mask(socket_msg.payload)
#
#             # 钱包与资产类消息
#             elif msg_type == "SC_REFRESH_WALLET":
#                 self._handle_refresh_wallet(socket_msg.payload)
#
#             # 互动聊天类消息
#             elif msg_type == "SC_INTERACTIVE_CHAT_SWITCH_BIZ":
#                 self._handle_chat_switch(socket_msg.payload)
#             elif msg_type == "SC_INTERACTIVE_CHAT_CLOSED":
#                 self._handle_chat_closed(socket_msg.payload)
#
#             # 未处理消息类型
#             else:
#                 if self.debug:
#                     logger.debug(f"⏭️ 未处理消息类型: {msg_type}")
#                     self.message_queue.put({
#                         "type": "system",
#                         "user_name": "系统",
#                         "content": f"未处理消息类型: {msg_type}"
#                     })
#
#         except Exception as e:
#             logger.error(f"消息处理异常: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"消息处理异常: {str(e)}"
#             })
#
#     # ===== 连接管理 =====
#     def _handle_enter_room_ack(self, payload):
#         ack = SCWebEnterRoomAck()
#         ack.ParseFromString(payload)
#         self.hb_interval = ack.heartbeat_interval_ms
#         self.room_entered = True
#         logger.success(f"🚪 进入房间成功 | 心跳间隔: {self.hb_interval}ms")
#         self.message_queue.put({
#             "type": "system",
#             "user_name": "系统",
#             "content": f"进入房间成功 | 心跳间隔: {self.hb_interval}ms"
#         })
#         self.start_heartbeat()
#
#     def _handle_heartbeat_ack(self, payload):
#         ack = SCWebHeartbeatAck()
#         ack.ParseFromString(payload)
#         latency = (time.time() * 1000 - ack.client_timestamp) / 1000.0
#         if self.debug:
#             logger.debug(f"💓 心跳ACK | 延迟: {latency:.3f}秒")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"心跳ACK | 延迟: {latency:.3f}秒"
#             })
#
#     def _handle_ping_ack(self, payload):
#         logger.debug("🏓 收到Ping响应")
#         self.message_queue.put({
#             "type": "system",
#             "user_name": "系统",
#             "content": "收到Ping响应"
#         })
#
#     # ===== 用户状态类消息 =====
#     def _handle_author_pause(self, payload):
#         msg = SCWebAuthorPause()
#         msg.ParseFromString(payload)
#         pause_type = WebPauseType.Name(msg.pause_type)
#         self.live_status["is_paused"] = True
#         logger.warning(f"⏸️ 主播暂停直播 | 类型: {pause_type} | 时间: {datetime.fromtimestamp(msg.time / 1000)}")
#         self.message_queue.put({
#             "type": "system",
#             "user_name": "系统",
#             "content": f"主播暂停直播 | 类型: {pause_type} | 时间: {datetime.fromtimestamp(msg.time / 1000)}"
#         })
#
#     def _handle_author_resume(self, payload):
#         msg = SCWebAuthorResume()
#         msg.ParseFromString(payload)
#         self.live_status["is_paused"] = False
#         logger.success(f"▶️ 主播恢复直播 | 时间: {datetime.fromtimestamp(msg.time / 1000)}")
#         self.message_queue.put({
#             "type": "system",
#             "user_name": "系统",
#             "content": f"主播恢复直播 | 时间: {datetime.fromtimestamp(msg.time / 1000)}"
#         })
#
#     def _handle_pip_started(self, payload):
#         msg = SCWebPipStarted()
#         msg.ParseFromString(payload)
#         self.live_status["is_pip"] = True
#         logger.info("📺 进入画中画模式")
#
#     def _handle_pip_ended(self, payload):
#         msg = SCWebPipEnded()
#         msg.ParseFromString(payload)
#         self.live_status["is_pip"] = False
#         logger.info("📺 退出画中画模式")
#
#     # ===== 实时互动处理 =====
#     def _handle_feed_push(self, payload):
#         try:
#             feed = SCWebFeedPush()
#             feed.ParseFromString(payload)
#
#             # 更新在线人数
#             if feed.display_watching_count:
#                 self.live_status["audience_count"] = feed.display_watching_count
#                 logger.info(f"👥 在线观众: {feed.display_watching_count}")
#                 self.message_queue.put({
#                     "type": "audience_count",
#                     "user_name": "系统",
#                     "content": feed.display_watching_count
#                 })
#
#             # 更新点赞数
#             if feed.display_like_count:
#                 self.live_status["like_count"] = feed.display_like_count
#                 logger.info(f"❤️ 点赞总数: {feed.display_like_count}")
#                 self.message_queue.put({
#                     "type": "like_count",
#                     "user_name": "系统",
#                     "content": feed.display_like_count
#                 })
#
#             # 处理评论
#             for comment in feed.comment_feeds:
#                 if comment.show_type == WebCommentFeedShowType.FEED_SHOW_NORMAL:
#                     user_name = comment.user.user_name if comment.user else "匿名用户"
#                     logger.warning(f"💬 {user_name}: {comment.content}")
#                     self.message_queue.put({
#                         "type": "comment",
#                         "user_name": user_name,
#                         "content": f"{comment.content}"
#                     })
#
#             # 处理礼物
#             for gift in feed.gift_feeds:
#                 user_name = gift.user.user_name if gift.user else "神秘人"
#                 gift_id = gift.gift_id
#                 count = gift.batch_size
#                 try:
#                     gift_name = self.gift_map.get(f"{gift_id}").get("giftName", "未知礼物")
#                 except:
#                     gift_name = "未知礼物"
#
#                 # # 根据礼物价值决定日志级别
#                 # if count > 10 or gift_id in [7, 8, 9, 10]:  # 重要礼物
#                 #     logger.warning(f"🎁 {user_name} 赠送了 {count}个 礼物{gift_name}")
#                 # else:  # 普通礼物
#                 logger.warning(f"🎁 {user_name} 赠送的 {gift_name}")
#                 self.message_queue.put({
#                     "type": "gift",
#                     "user_name": user_name,
#                     "content": f"{user_name} 赠送了 {count}个 礼物{gift_name}"
#                 })
#
#             # 处理点赞
#             for like in feed.like_feeds:
#                 user_name = like.user.user_name if like.user else "神秘人"
#                 logger.warning(f"感谢 {user_name} 点赞了直播间")
#                 self.message_queue.put({
#                     "type": "like",
#                     "user_name": user_name,
#                     "content": f"{user_name} 点赞了直播间"
#                 })
#
#             # 处理系统通知
#             for notice in feed.system_notice_feeds:
#                 user_name = notice.user.user_name if notice.user else "系统"
#                 logger.success(f"📢 {user_name}: {notice.content}")
#                 self.message_queue.put({
#                     "type": "system_notice",
#                     "user_name": user_name,
#                     "content": f"{user_name}: {notice.content}"
#                 })
#
#             # 处理分享消息
#             for share in feed.share_feeds:
#                 user_name = share.user.user_name if share.user else "神秘人"
#                 logger.success(f"📤 {user_name} 分享了直播间")
#                 self.message_queue.put({
#                     "type": "share",
#                     "user_name": user_name,
#                     "content": f"📤 {user_name} 分享了直播间"
#                 })
#
#         except Exception as e:
#             logger.error(f"处理Feed推送失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理Feed推送失败: {str(e)}"
#             })
#
#     # ===== 红包处理 =====
#     def _handle_red_pack_feed(self, payload):
#         try:
#             redpack = SCWebCurrentRedPackFeed()
#             redpack.ParseFromString(payload)
#
#             for pack in redpack.red_pack:
#                 author_name = pack.author.user_name if pack.author else "神秘人"
#                 amount = pack.balance / 100  # 转换为元
#                 open_time = datetime.fromtimestamp(pack.open_time / 1000) if pack.open_time else "未知时间"
#
#                 # 存储红包信息
#                 self.redpacks[pack.id] = {
#                     "author": author_name,
#                     "amount": amount,
#                     "open_time": open_time,
#                     "grab_token": pack.grab_token
#                 }
#
#                 logger.success(f"🧧 红包通知 | 来自: {author_name} | 金额: ¥{amount:.2f} | 开抢时间: {open_time}")
#                 self.message_queue.put({
#                     "type": "red_pack",
#                     "user_name": author_name,
#                     "content": f"🧧 红包通知 | 来自: {author_name} | 金额: ¥{amount:.2f} | 开抢时间: {open_time}"
#                 })
#         except Exception as e:
#             logger.error(f"处理红包失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理红包失败: {str(e)}"
#             })
#
#     # ===== 观众列表处理 =====
#     def _handle_watching_list(self, payload):
#         """处理观众列表更新（包含新用户进入房间）"""
#         try:
#             # 解析消息
#             if not payload:
#                 logger.warning("⚠️ 收到空的观众列表消息")
#                 self.message_queue.put({
#                     "type": "system",
#                     "user_name": "系统",
#                     "content": "⚠️ 收到空的观众列表消息"
#                 })
#                 return
#
#             msg = SCWebLiveWatchingUsers()
#             msg.ParseFromString(payload)
#
#             # 1. 更新在线人数
#             if msg.display_watching_count:
#                 prev_count = self.live_status.get("audience_count", "0")
#                 new_count = msg.display_watching_count
#
#                 # 只在人数变化较大时打印
#                 if new_count != prev_count:
#                     self.live_status["audience_count"] = new_count
#                     self.live_status["last_audience_update"] = time.time()
#                     logger.info(f"👥 在线观众: {new_count}")
#                     self.message_queue.put({
#                         "type": "audience_count",
#                         "user_name": "系统",
#                         "content": new_count
#                     })
#
#             # 2. 处理新进入房间的用户
#             # 当前在线用户ID
#             self.current_online_users = [user_info for user_info in msg.watching_user]
#
#             for user_info in self.current_online_users:
#                 # 只处理新进入的用户（非离线状态）, 包含离开再次进入用户, 不打印已经离开的用户与在已在房间也不是新进入的用户与在户
#                 user_id = user_info.user.principal_id
#                 if user_id not in self.all_entered_users and not user_info.offline:
#                     self.all_entered_users.add(user_id)
#                     self._process_user_enter(user_info)
#                 if user_id in self.all_entered_users and user_info.offline:
#                     self.all_entered_users.remove(user_id)
#
#         except Exception as e:
#             logger.error(f"处理观众列表失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理观众列表失败: {str(e)}"
#             })
#
#
#     def _process_user_enter(self, user_info):
#         """处理用户进入房间事件"""
#         try:
#             if not user_info.user:
#                 return
#
#             user_name = user_info.user.user_name
#             # 获取用户身份信息
#             identity = ""
#
#             if user_info.live_assistant_type == WebLiveAssistantType.SUPER_WEB_ASSISTANT:
#                 identity = "【房管】"
#             elif user_info.live_assistant_type == WebLiveAssistantType.JUNIOR_WEB_ASSISTANT:
#                 identity = "【助理】"
#             elif user_info.tuhao:
#                 identity = "【土豪】"
#
#             # 获取用户财富等级（如果有）
#             wealth_info = ""
#             if hasattr(user_info, "wealth_grade") and user_info.wealth_grade > 0:
#                 wealth_info = f" (财富等级:{user_info.wealth_grade})"
#
#             # 打印用户进入消息
#             logger.success(f"🚪 {identity}{user_name}{wealth_info} 进入了直播间")
#             self.message_queue.put({
#                 "type": "user_enter",
#                 "user_name": user_name,
#                 "content": f"🚪 {identity} {user_name} {wealth_info} 进入了直播间"
#             })
#
#         except Exception as e:
#             logger.error(f"处理用户进入事件失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理用户进入事件失败: {str(e)}"
#             })
#
#     # ===== 富文本评论处理 =====
#     def _handle_rich_text_comment(self, payload):
#         try:
#             rich_text = SCCommentZoneRichText()
#             rich_text.ParseFromString(payload)
#
#             for msg in rich_text.message:
#                 segments = []
#                 for seg in msg.segment:
#                     if seg.text_segment:
#                         text = seg.text_segment.text
#                         segments.append(text)
#                     elif seg.icon_segment:
#                         icon_text = seg.icon_segment.text
#                         segments.append(f"[图标:{icon_text}]")
#                     elif seg.gift_segment:
#                         gift_id = seg.gift_segment.gift_id
#                         gift_name = self.gift_map.get(gift_id, f"礼物{gift_id}")
#                         segments.append(f"[礼物:{gift_name}]")
#
#                 if segments:
#                     user_id = msg.user_id
#                     comment = ''.join(segments)
#                     logger.success(f"✨ 富文本弹幕({user_id}): {comment}")
#                     self.message_queue.put({
#                         "type": "rich_text_comment",
#                         "user_id": user_id,
#                         "content": comment
#                     })
#         except Exception as e:
#             logger.error(f"处理富文本弹幕失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理富文本弹幕失败: {str(e)}"
#             })
#
#     # ===== 竞猜处理 =====
#     def _handle_guess_opened(self, payload):
#         try:
#             guess = SCWebGuessOpened()
#             guess.ParseFromString(payload)
#             self.active_guess = guess.guess_id
#             deadline = datetime.fromtimestamp(guess.submit_deadline / 1000) if guess.submit_deadline else "未知时间"
#             logger.warning(f"🎲 竞猜开启! ID:{guess.guess_id} | 截止时间: {deadline}")
#             self.message_queue.put({
#                 "type": "guess_opened",
#                 "user_name": "系统",
#                 "content": f"🎲 竞猜开启! ID:{guess.guess_id} | 截止时间: {deadline}"
#             })
#         except Exception as e:
#             logger.error(f"处理竞猜开启失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理竞猜开启失败: {str(e)}"
#             })
#
#     def _handle_guess_closed(self, payload):
#         try:
#             guess = SCWebGuessClosed()
#             guess.ParseFromString(payload)
#             self.active_guess = None
#             logger.info(f"🎲 竞猜结束! ID:{guess.guess_id}")
#             self.message_queue.put({
#                 "type": "guess_closed",
#                 "user_name": "系统",
#                 "content": f"🎲 竞猜结束! ID:{guess.guess_id}"
#             })
#         except Exception as e:
#             logger.error(f"处理竞猜结束失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理竞猜结束失败: {str(e)}"
#             })
#
#     # ===== 系统警告处理 =====
#     def _handle_warning_mask(self, payload):
#         try:
#             warning = SCLiveWarningMaskStatusChangedAudience()
#             warning.ParseFromString(payload)
#
#             if warning.display_mask:
#                 self.live_status["warning_mask"] = True
#                 logger.critical(f"⛔ 直播间警告: {warning.warning_mask.title}")
#                 self.message_queue.put({
#                     "type": "warning_mask",
#                     "user_name": "系统",
#                     "content": f"⛔ 直播间警告: {warning.warning_mask.title}"
#                 })
#                 logger.critical(f"警告详情: {warning.warning_mask.detail}")
#                 self.message_queue.put({
#                     "type": "warning_mask",
#                     "user_name": "系统",
#                     "content": f"警告详情: {warning.warning_mask.detail}"
#                 })
#             else:
#                 self.live_status["warning_mask"] = False
#                 logger.info("✅ 直播间警告解除")
#                 self.message_queue.put({
#                     "type": "warning_mask",
#                     "user_name": "系统",
#                     "content": "✅ 直播间警告解除"
#                 })
#         except Exception as e:
#             logger.error(f"处理警告蒙层失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理警告蒙层失败: {str(e)}"
#             })
#
#     # ===== 违规处理 =====
#     def _handle_violation(self, payload):
#         try:
#             msg = SCWebSuspectedViolation()
#             msg.ParseFromString(payload)
#
#             if msg.suspected_violation:
#                 self.live_status["violation"] = True
#                 logger.critical("⚠️ 直播间涉嫌违规内容！请谨慎发言")
#                 self.message_queue.put({
#                     "type": "violation",
#                     "user_name": "系统",
#                     "content": "⚠️ 直播间涉嫌违规内容！请谨慎发言"
#                 })
#             else:
#                 self.live_status["violation"] = False
#                 logger.info("✅ 违规状态解除")
#                 self.message_queue.put({
#                     "type": "violation",
#                     "user_name": "系统",
#                     "content": "✅ 违规状态解除"
#                 })
#         except Exception as e:
#             logger.error(f"处理违规警告失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理违规警告失败: {str(e)}"
#             })
#
#     # ===== 配置状态处理 =====
#     def _handle_config_state(self, payload):
#         try:
#             config = SCWebLiveSpecialAccountConfigState()
#             config.ParseFromString(payload)
#
#             for item in config.config_switch_item:
#                 switch_type = ConfigSwitchType.Name(item.config_switch_type)
#                 logger.info(f"⚙️ 配置更新: {switch_type} = {'开启' if item.value else '关闭'}")
#                 self.message_queue.put({
#                     "type": "config_update",
#                     "user_name": "系统",
#                     "content": f"⚙️ 配置更新: {switch_type} = {'开启' if item.value else '关闭'}"
#                 })
#         except Exception as e:
#             logger.error(f"处理配置状态失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理配置状态失败: {str(e)}"
#             })
#
#     # ===== 其他消息类型处理 =====
#     def _handle_ride_changed(self, payload):
#         logger.info("🐎 坐骑状态变化")
#
#     def _handle_bet_changed(self, payload):
#         logger.info("🎰 押注状态变化")
#
#     def _handle_bet_closed(self, payload):
#         logger.info("🎰 押注已结束")
#
#     def _handle_pk_statistic(self, payload):
#         logger.info("🥊 PK统计数据更新")
#
#     def _handle_refresh_wallet(self, payload):
#         logger.info("💰 钱包状态刷新")
#
#     def _handle_chat_switch(self, payload):
#         logger.info("💬 互动聊天切换业务")
#
#     def _handle_chat_closed(self, payload):
#         logger.info("💬 互动聊天已关闭")
#
#     # ===== 错误处理 =====
#     def _handle_error(self, payload):
#         try:
#             error_msg = SCWebError()
#             error_msg.ParseFromString(payload)
#             logger.error(f"❌ 服务器错误 [{error_msg.code}-{error_msg.sub_code}]: {error_msg.msg}")
#             if "直播已停止" in error_msg.msg:
#                 self.message_queue.put({
#                     "type": "system",
#                     "user_name": "系统",
#                     "content": f"❌ 直播已停止 [{error_msg.code}-{error_msg.sub_code}]: {error_msg.msg}"
#                 })
#                 self.stop(reason="直播已停止", exit_program=True)
#         except Exception as e:
#             logger.error(f"处理错误消息失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理错误消息失败: {str(e)}"
#             })
#
#     def _handle_info(self, payload):
#         try:
#             msg = SCInfo()
#             msg.ParseFromString(payload)
#
#             # 根据代码判断消息级别
#             if msg.code >= 500:  # 严重错误
#                 logger.error(f"❌ 系统错误: {msg.msg} (代码: {msg.code})")
#             else:  # 一般信息
#                 logger.info(f"ℹ️ 系统消息: {msg.msg}")
#
#             # 放入消息队列
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"ℹ️ 系统消息: {msg.msg} (代码: {msg.code})"
#             })
#         except Exception as e:
#             logger.error(f"处理系统消息失败: {str(e)}")
#             self.message_queue.put({
#                 "type": "system",
#                 "user_name": "系统",
#                 "content": f"处理系统消息失败: {str(e)}"
#             })
#
#     # ===== 客户端控制方法 =====
#     def stop(self, reason="用户请求", exit_program=False):
#         """安全停止客户端 - 仅供个人使用"""
#         try:
#             # 1. 设置停止标志
#             self._stop_flag = True
#             self.heartbeat_active = False
#             self.room_entered = False
#
#             # 2. 关闭WebSocket连接
#             if self.websocket:
#                 logger.info(f"🛑 正在停止客户端 ({reason})...")
#                 self.websocket.close()
#                 self.websocket = None
#
#             # 3. 清理资源
#             self.cleanup_threads()
#             logger.success("✅ 客户端已安全停止")
#
#             # # 4. 如果需要，退出整个程序
#             # if exit_program:
#             #     logger.info("🛑 退出程序...")
#             #     sys.exit(0)  # 正常退出程序
#             #
#             # logger.info("✅ 客户端已安全停止")
#             # sys.exit(0)
#         except Exception as e:
#             logger.error(f"❌ 停止客户端时发生错误: {str(e)}")
#             if exit_program:
#                 sys.exit(1)  # 异常退出程序
#
#     def cleanup_threads(self):
#             """清理所有后台线程 - 仅供个人使用"""
#             # 等待所有线程结束
#             time.sleep(0.5)  # 给线程一点时间响应
#             logger.info("🔄 清理后台线程...")
#
#             # 重置状态
#             self._stop_flag = False
#             self.message_stats.clear()
#
#     def start(self):
#         """启动WebSocket客户端"""
#         # 验证必要信息
#         if not all([self.ws_url, self.live_stream_id, self.token]):
#             logger.error("❌ 无法启动: 缺少必要的连接参数")
#             return
#
#         logger.info(f"🔗 开始连接: {self.ws_url}")
#         print(f"🔗 开始连接: {self.ws_url}")
#
#         # 禁用SSL验证
#         ssl_opt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
#
#         # 创建WebSocket客户端
#         self.websocket = WebSocketApp(
#             self.ws_url,
#             header=self.headers,
#             on_open=self.on_open,
#             on_message=self.on_message,
#             on_error=self.on_error,
#             on_close=self.on_close
#         )
#
#         self.heartbeat_active = True
#         self.room_entered = False
#
#         # 在独立线程中运行WebSocket客户端
#         ws_thread = threading.Thread(
#             target=self.websocket.run_forever,
#             kwargs={
#                 "ping_interval": 20,
#                 "ping_timeout": 10,
#                 "sslopt": ssl_opt
#             },
#             daemon=True,
#             name="WebSocketThread"
#         )
#         ws_thread.start()
#         logger.info("👂 开始监听消息... (按Ctrl+C退出)")
#         print("👂 开始监听消息... (按Ctrl+C退出)")
#
#         try:
#             # 主线程监控连接状态
#             while self.heartbeat_active and ws_thread.is_alive():
#                 time.sleep(5)
#         except KeyboardInterrupt:
#             logger.info("🛑 用户终止程序")
#         finally:
#             self.heartbeat_active = False
#             if self.websocket:
#                 self.websocket.close()
#             logger.info("程序已退出")
#
#     def on_error(self, ws, error):
#         """WebSocket错误回调"""
#         logger.error(f"🛑 WebSocket错误: {str(error)}")
#         self.heartbeat_active = False
#
#     def on_close(self, ws, close_status_code, close_msg):
#         """WebSocket关闭回调"""
#         self.heartbeat_active = False
#         self.room_entered = False
#         logger.info(f"🔌 连接已关闭, 状态码: {close_status_code}, 消息: {close_msg or '无消息'}")
#
#
#
# class KwaiLiveProcess(QObject):
#     warning_signal = pyqtSignal(str)
#     def __init__(self, live_url, message_queue):
#         super().__init__()
#         self.live_url = live_url
#         self.process = None
#         self.message_queue = message_queue
#
#     def start(self):
#         """启动 KwaiLiveDanmuClient 进程"""
#         if self.process and self.process.is_alive():
#             print("快手进程已在运行中")
#             return False
#
#         self.message_queue.put({
#             "type": "system",
#             "user_name": "系统",
#             "content": "弹幕启动中 . . ."
#         })
#
#         # 创建新进程
#         self.process = multiprocessing.Process(
#             target=self._run_kwai_live,
#             args=(self.live_url, self.message_queue, self.warning_signa)
#         )
#         self.process.daemon = True  # 设置为守护进程，主程序退出时自动终止
#         self.process.start()
#         self.message_queue.put({
#             "type": "system",
#             "user_name": "系统",
#             "content": "KwaiLiveDanmuClient 进程已启动."
#         })
#         logger.info("KwaiLiveDanmuClient 进程已启动")
#         return True
#
#     def stop(self):
#         """停止 KwaiLiveDanmuClient 进程"""
#         if self.process and self.process.is_alive():
#             # self.process.terminate()
#             self.process.join()  # 等待进程完全终止
#             print("KwaiLiveDanmuClient 进程已停止")
#             return True
#         else:
#             print("没有正在运行的快手进程")
#             return False
#
#     @staticmethod
#     def _run_kwai_live(live_url, message_queue, warning_signal):
#         """在子进程中运行 KwaiLiveDanmuClient"""
#         # 需要在子进程中重新导入，因为多进程会重新导入模块
#
#         # 获取直播信息
#         websocket_info_path = os.path.join(os.path.dirname(__file__), "webinfo", "ghoulish_data.json")
#         ghoulish_gift_info_path = os.path.join(os.path.dirname(__file__), "webinfo", "ghoulish_gift.json")
#
#         # 获取直播信息
#         with open(websocket_info_path, "r", encoding="utf-8") as f:
#             data = json.load(f)
#
#         with open(ghoulish_gift_info_path, "r", encoding="utf-8") as f:
#             gift_map = json.load(f)
#
#         if "captured_requests" not in data.keys():
#             data = get_new_token(live_url, websocket_info_path)
#         if data["target_url"] not in live_url:
#             data = get_new_token(live_url, websocket_info_path)
#
#         for response in data["captured_requests"]:
#             if response["response"]["body"]["data"]["result"] == 1:
#                 data = response
#                 break
#         else:
#             print("❌ 无法获取直播信息")
#             warning_signal.emit("无法获取直播信息")
#             return
#         # 创建客户端实例
#         client = KwaiLiveDanmuClient()
#
#         # 设置连接参数
#         client.set_connection_params(
#             ws_url=data["response"]["body"]['data']['websocketUrls'][-1],
#             live_stream_id=data["query_params"]["liveStreamId"],
#             token=data["response"]["body"]['data']['token'],
#             headers=data["headers"],
#             cookies=data["cookies"],
#             session_page_id=data["session_page_id"],
#             live_urls=live_url,
#             gift_maps=gift_map,
#             message_queue=message_queue,
#         )
#
#         # 启动客户端
#         client.start()
#
#     def get_messages(self):
#         """从消息队列中获取消息"""
#         messages = []
#         while not self.message_queue.empty():
#             try:
#                 messages.append(self.message_queue.get_nowait())
#             except:
#                 break
#         return messages
#
#
#
# if __name__ == "__main__":
#
#     # 创建 KwaiLiveProcess 实例
#     kwai_live_url = "https://live.kuaishou.com/u/3xxgygyreajsxca"
#     message_queue = multiprocessing.Queue()
#     kwai_process = KwaiLiveProcess(kwai_live_url, message_queue)
#
#     try:
#         # 启动两个进程
#         if kwai_process.start():
#             # 主循环，处理消息
#             while True:
#
#                 # 获取并处理快手消息
#                 kwai_messages = kwai_process.get_messages()
#                 for msg in kwai_messages:
#                     print(f"快手消息: {msg}")
#
#                 # 等待一段时间
#                 time.sleep(1)
#
#     except KeyboardInterrupt:
#         print("收到中断信号，准备停止进程...")
#     finally:
#         # 停止进程
#         kwai_process.stop()


import subprocess
from functools import partial

from PyQt6.QtCore import pyqtSignal, QObject

subprocess.Popen = partial(subprocess.Popen, encoding="utf-8")

import multiprocessing
import os.path
import sys
import json
import threading
import time
import ssl
from datetime import datetime
from loguru import logger
from collections import defaultdict
import websocket
from websocket import WebSocketApp
import queue  # 添加queue模块

# 导入protobuf相关
from auxiliary.models.KuaiShou.live_kwai_pb2 import (SocketMessage, PayloadType, SCWebFeedPush, CSWebEnterRoom,
                                                     SCWebEnterRoomAck,
                                                     SimpleUserInfo, WebCommentFeed, WebGiftFeed, SCCommentZoneRichText,
                                                     SCWebLiveWatchingUsers, SCWebError, SCInfo, CSWebError,
                                                     SCWebHeartbeatAck,
                                                     SCWebCurrentRedPackFeed, CSWebHeartbeat, SCWebSuspectedViolation,
                                                     SCLiveWarningMaskStatusChangedAudience, SCWebGuessOpened,
                                                     WebCommentFeedShowType,
                                                     ConfigSwitchType, SCWebLiveSpecialAccountConfigState,
                                                     SCWebGuessClosed,
                                                     WebUserPauseType, WebPauseType, AssistantType, StyleType,
                                                     WebLiveAssistantType,
                                                     SCWebAuthorPause, SCWebAuthorResume, SCWebPipStarted,
                                                     SCWebPipEnded,
                                                     SCWebGuessClosed, SCWebRideChanged, SCWebBetChanged,
                                                     SCWebBetClosed,
                                                     SCInteractiveChatSwitchBiz, SCInteractiveChatClosed,
                                                     SCLiveMultiPkStatistic, PicUrl, UserInfo, LiveAudienceState)

from auxiliary.models.KuaiShou.kwai_token import get_new_token


# 移除默认日志处理器
# logger.remove(0)
# logger.add(sys.stderr, format="{time} | {level} | {message}",level="SUCCESS")

class KwaiLiveDanmuClient:
    def __init__(self):
        """初始化客户端"""
        self.message_queue = None
        self.live_url = None
        self.session_page_id = None
        self.ws_url = None
        self.headers = {}
        self.cookies = {}
        self.gift_map = {}  # 全部礼物墙
        self.token = None
        self.live_stream_id = None
        self.websocket = None
        self.heartbeat_active = True
        self.room_entered = False
        self._stop_flag = False  # 添加停止标志
        self.stop_event = None  # 添加停止事件
        logger.info("⚡ 弹幕客户端已初始化")

        # 用户状态跟踪
        self.all_entered_users = set()  # 在直播间的用户ID
        self.current_online_users = set()  # 当前在线用户ID
        self.user_enter_count = defaultdict(int)  # 用户进入次数统计

        # 消息统计
        self.message_stats = defaultdict(int)
        self.last_print_stats_time = time.time()
        self.stats_interval = 30  # 每30秒打印一次统计

        # 调试模式
        self.debug = True

        # 错误处理和状态控制
        self.retry_count = 0
        self.max_retries = 3

        # 错误定义
        self.fatal_errors = {
            101: "TOKEN_EXPIRED",
            201: "ROOM_NOT_EXIST",
            301: "NO_PERMISSION",
            401: "SERVER_OVERLOAD"
        }

        # 心跳控制
        self.hb_interval = 20000  # 默认20秒心跳间隔

        # 直播间状态
        self.live_status = {
            "is_paused": False,
            "is_pip": False,
            "warning_mask": False,
            "violation": False,
            "audience_count": "0",
            "like_count": "0"
        }

        # 红包信息
        self.redpacks = {}
        self.active_guess = None

    def set_connection_params(self, ws_url, live_stream_id, token,
                              headers, cookies, session_page_id, live_urls, gift_maps, message_queue, stop_event=None):
        """设置连接参数"""
        self.ws_url = ws_url
        self.live_stream_id = live_stream_id
        self.token = token
        self.headers = headers
        self.cookies = cookies if isinstance(cookies, dict) else {}
        self.session_page_id = session_page_id
        self.live_url = live_urls
        self.gift_map = gift_maps
        self.message_queue = message_queue
        self.stop_event = stop_event  # 添加停止事件

        logger.info(f"✅ 已设置连接参数: URL={ws_url}")
        logger.info(f"📡 直播流ID: {live_stream_id}")
        logger.info(f"🔑 Token: {token[:15]}...")
        return True

    def create_enter_room_message(self):
        """创建进入房间消息 (CSWebEnterRoom)"""
        try:
            enter_room = CSWebEnterRoom(
                token=self.token,
                live_stream_id=self.live_stream_id,
                page_id=self.session_page_id
            )

            # 创建包装消息
            socket_msg = SocketMessage(
                payload_type=PayloadType.CS_ENTER_ROOM,
                compression_type=0,  # NONE
                payload=enter_room.SerializeToString()
            )

            return socket_msg.SerializeToString()
        except Exception as e:
            logger.error(f"❌ 创建进入房间消息失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"创建进入房间消息失败: {str(e)}"
            })
            return None

    def create_heartbeat_message(self):
        """创建符合协议的心跳包"""
        try:
            heartbeat_msg = CSWebHeartbeat()
            heartbeat_msg.timestamp = int(time.time() * 1000)

            # 创建包装消息
            socket_msg = SocketMessage(
                payload_type=PayloadType.CS_HEARTBEAT,
                compression_type=0,  # NONE
                payload=heartbeat_msg.SerializeToString()
            )
            return socket_msg.SerializeToString()
        except Exception as e:
            logger.error(f"❌ 创建心跳消息失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"创建心跳消息失败: {str(e)}"
            })
            return None

    def start_heartbeat(self):
        """启动心跳线程（使用服务器指定的间隔）"""

        def heartbeat_loop():
            logger.info("💓 心跳线程已启动")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": "心跳线程已启动"
            })

            while (self.heartbeat_active and
                   self.websocket and
                   self.websocket.sock and
                   self.websocket.sock.connected and
                   not (self.stop_event and self.stop_event.is_set())):
                try:
                    # 动态心跳间隔（毫秒转秒）
                    interval = self.hb_interval / 1000.0

                    # 只在成功进入房间后发送心跳
                    heartbeat_msg = self.create_heartbeat_message()
                    if heartbeat_msg:
                        self.websocket.send(heartbeat_msg, opcode=websocket.ABNF.OPCODE_BINARY)
                        if self.debug:
                            logger.debug(f"💓 发送心跳包 (间隔: {interval:.1f}秒)")
                            self.message_queue.put({
                                "type": "system",
                                "user_name": "系统",
                                "content": f"发送心跳包 (间隔: {interval:.1f}秒)"
                            })

                    # 按服务器要求的时间间隔等待
                    time.sleep(interval)

                except websocket.WebSocketConnectionClosedException:
                    logger.error("🛑 WebSocket连接已关闭，停止心跳")
                    self.message_queue.put({
                        "type": "system",
                        "user_name": "系统",
                        "content": "WebSocket连接已关闭，停止心跳"
                    })
                    break
                except Exception as e:
                    logger.error(f"⚠️ 心跳发送失败: {str(e)}")
                    self.message_queue.put({
                        "type": "system",
                        "user_name": "系统",
                        "content": f"心跳发送失败: {str(e)}"
                    })
                    time.sleep(1)  # 错误后短暂延迟
            logger.info("💔 心跳线程已停止")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": "心跳线程已停止"
            })

        threading.Thread(target=heartbeat_loop, daemon=True, name="HeartbeatThread").start()

    def on_open(self, ws):
        """WebSocket打开回调"""
        # 检查是否应该停止
        if self.stop_event and self.stop_event.is_set():
            ws.close()
            return

        logger.success("✅ WebSocket连接已建立")
        self.message_queue.put({
            "type": "system",
            "user_name": "系统",
            "content": "WebSocket连接已建立"
        })

        # 发送进入房间消息
        try:
            enter_msg = self.create_enter_room_message()
            if enter_msg:
                ws.send(enter_msg, opcode=websocket.ABNF.OPCODE_BINARY)
                logger.info("📤 已发送进入房间请求")
                self.message_queue.put({
                    "type": "system",
                    "user_name": "系统",
                    "content": "直播间连接已建立 . . ."
                })
            else:
                logger.error("❌ 无法创建进入房间消息，连接可能失败")
                self.message_queue.put({
                    "type": "system",
                    "user_name": "系统",
                    "content": "无法创建进入房间消息，连接可能失败"
                })
        except Exception as e:
            logger.error(f"❌ 发送进入房间失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"发送进入房间失败: {str(e)}"
            })

    def on_message(self, ws, message):
        """WebSocket消息回调 - 支持快手直播全协议处理"""
        # 检查是否应该停止
        if self.stop_event and self.stop_event.is_set():
            ws.close()
            return

        try:
            # 解析顶层消息
            socket_msg = SocketMessage()
            socket_msg.ParseFromString(message)

            # 获取消息类型名称
            try:
                msg_type = PayloadType.Name(socket_msg.payload_type)
            except ValueError:
                msg_type = f"UNKNOWN_TYPE_{socket_msg.payload_type}"
                logger.debug(f"⚠️ 未知消息类型: {socket_msg.payload_type}")
                self.message_queue.put({
                    "type": "system",
                    "user_name": "系统",
                    "content": f"未知消息类型: {socket_msg.payload_type}"
                })

            # 更新消息统计
            self.message_stats[msg_type] += 1

            # ====================== 消息类型分发处理 ======================
            # 连接管理类消息
            if msg_type == "SC_ENTER_ROOM_ACK":
                self._handle_enter_room_ack(socket_msg.payload)
            elif msg_type == "SC_HEARTBEAT_ACK":
                self._handle_heartbeat_ack(socket_msg.payload)
            elif msg_type == "SC_PING_ACK":
                self._handle_ping_ack(socket_msg.payload)

            # 用户状态类消息
            elif msg_type == "SC_AUTHOR_PAUSE":
                self._handle_author_pause(socket_msg.payload)
            elif msg_type == "SC_AUTHOR_RESUME":
                self._handle_author_resume(socket_msg.payload)
            elif msg_type == "SC_PIP_STARTED":
                self._handle_pip_started(socket_msg.payload)
            elif msg_type == "SC_PIP_ENDED":
                self._handle_pip_ended(socket_msg.payload)

            # 实时互动类消息
            elif msg_type == "SC_FEED_PUSH":
                self._handle_feed_push(socket_msg.payload)
            elif msg_type == "SC_RED_PACK_FEED":
                self._handle_red_pack_feed(socket_msg.payload)
            # elif msg_type == "SC_COMMENT_ZONE_RICH_TEXT":
            #     self._handle_rich_text_comment(socket_msg.payload)
            elif msg_type == "SC_LIVE_WATCHING_LIST":
                self._handle_watching_list(socket_msg.payload)

            # 活动与游戏类消息
            elif msg_type == "SC_GUESS_OPENED":
                self._handle_guess_opened(socket_msg.payload)
            elif msg_type == "SC_GUESS_CLOSED":
                self._handle_guess_closed(socket_msg.payload)
            elif msg_type == "SC_RIDE_CHANGED":
                self._handle_ride_changed(socket_msg.payload)
            elif msg_type == "SC_BET_CHANGED":
                self._handle_bet_changed(socket_msg.payload)
            elif msg_type == "SC_BET_CLOSED":
                self._handle_bet_closed(socket_msg.payload)
            elif msg_type == "SC_LIVE_MULTI_PK_STATISTIC":
                self._handle_pk_statistic(socket_msg.payload)

            # 系统与控制类消息
            elif msg_type == "SC_ERROR":
                self._handle_error(socket_msg.payload)
            elif msg_type == "SC_INFO":
                self._handle_info(socket_msg.payload)
            elif msg_type == "SC_SUSPECTED_VIOLATION":
                self._handle_violation(socket_msg.payload)
            elif msg_type == "SC_LIVE_SPECIAL_ACCOUNT_CONFIG_STATE":
                self._handle_config_state(socket_msg.payload)
            elif msg_type == "SC_LIVE_WARNING_MASK_STATUS_CHANGED_AUDIENCE":
                self._handle_warning_mask(socket_msg.payload)

            # 钱包与资产类消息
            elif msg_type == "SC_REFRESH_WALLET":
                self._handle_refresh_wallet(socket_msg.payload)

            # 互动聊天类消息
            elif msg_type == "SC_INTERACTIVE_CHAT_SWITCH_BIZ":
                self._handle_chat_switch(socket_msg.payload)
            elif msg_type == "SC_INTERACTIVE_CHAT_CLOSED":
                self._handle_chat_closed(socket_msg.payload)

            # 未处理消息类型
            else:
                if self.debug:
                    logger.debug(f"⏭️ 未处理消息类型: {msg_type}")
                    self.message_queue.put({
                        "type": "system",
                        "user_name": "系统",
                        "content": f"未处理消息类型: {msg_type}"
                    })

        except Exception as e:
            logger.error(f"消息处理异常: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"消息处理异常: {str(e)}"
            })

    # ===== 连接管理 =====
    def _handle_enter_room_ack(self, payload):
        ack = SCWebEnterRoomAck()
        ack.ParseFromString(payload)
        self.hb_interval = ack.heartbeat_interval_ms
        self.room_entered = True
        logger.success(f"🚪 进入房间成功 | 心跳间隔: {self.hb_interval}ms")
        self.message_queue.put({
            "type": "system",
            "user_name": "系统",
            "content": f"进入房间成功 | 心跳间隔: {self.hb_interval}ms"
        })
        self.start_heartbeat()

    def _handle_heartbeat_ack(self, payload):
        ack = SCWebHeartbeatAck()
        ack.ParseFromString(payload)
        latency = (time.time() * 1000 - ack.client_timestamp) / 1000.0
        if self.debug:
            logger.debug(f"💓 心跳ACK | 延迟: {latency:.3f}秒")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"心跳ACK | 延迟: {latency:.3f}秒"
            })

    def _handle_ping_ack(self, payload):
        logger.debug("🏓 收到Ping响应")
        self.message_queue.put({
            "type": "system",
            "user_name": "系统",
            "content": "收到Ping响应"
        })

    # ===== 用户状态类消息 =====
    def _handle_author_pause(self, payload):
        msg = SCWebAuthorPause()
        msg.ParseFromString(payload)
        pause_type = WebPauseType.Name(msg.pause_type)
        self.live_status["is_paused"] = True
        logger.warning(f"⏸️ 主播暂停直播 | 类型: {pause_type} | 时间: {datetime.fromtimestamp(msg.time / 1000)}")
        self.message_queue.put({
            "type": "system",
            "user_name": "系统",
            "content": f"主播暂停直播 | 类型: {pause_type} | 时间: {datetime.fromtimestamp(msg.time / 1000)}"
        })

    def _handle_author_resume(self, payload):
        msg = SCWebAuthorResume()
        msg.ParseFromString(payload)
        self.live_status["is_paused"] = False
        logger.success(f"▶️ 主播恢复直播 | 时间: {datetime.fromtimestamp(msg.time / 1000)}")
        self.message_queue.put({
            "type": "system",
            "user_name": "系统",
            "content": f"主播恢复直播 | 时间: {datetime.fromtimestamp(msg.time / 1000)}"
        })

    def _handle_pip_started(self, payload):
        msg = SCWebPipStarted()
        msg.ParseFromString(payload)
        self.live_status["is_pip"] = True
        logger.info("📺 进入画中画模式")

    def _handle_pip_ended(self, payload):
        msg = SCWebPipEnded()
        msg.ParseFromString(payload)
        self.live_status["is_pip"] = False
        logger.info("📺 退出画中画模式")

    # ===== 实时互动处理 =====
    def _handle_feed_push(self, payload):
        try:
            feed = SCWebFeedPush()
            feed.ParseFromString(payload)

            # 更新在线人数
            if feed.display_watching_count:
                self.live_status["audience_count"] = feed.display_watching_count
                logger.info(f"👥 在线观众: {feed.display_watching_count}")
                self.message_queue.put({
                    "type": "audience_count",
                    "user_name": "系统",
                    "content": feed.display_watching_count
                })

            # 更新点赞数
            if feed.display_like_count:
                self.live_status["like_count"] = feed.display_like_count
                logger.info(f"❤️ 点赞总数: {feed.display_like_count}")
                self.message_queue.put({
                    "type": "like_count",
                    "user_name": "系统",
                    "content": feed.display_like_count
                })

            # 处理评论
            for comment in feed.comment_feeds:
                if comment.show_type == WebCommentFeedShowType.FEED_SHOW_NORMAL:
                    user_name = comment.user.user_name if comment.user else "匿名用户"
                    logger.warning(f"💬 {user_name}: {comment.content}")
                    self.message_queue.put({
                        "type": "comment",
                        "user_name": user_name,
                        "content": f"{comment.content}"
                    })

            # 处理礼物
            for gift in feed.gift_feeds:
                user_name = gift.user.user_name if gift.user else "神秘人"
                gift_id = gift.gift_id
                count = gift.batch_size
                try:
                    gift_name = self.gift_map.get(f"{gift_id}").get("giftName", "未知礼物")
                except:
                    gift_name = "未知礼物"

                logger.warning(f"🎁 {user_name} 赠送的 {gift_name}")
                self.message_queue.put({
                    "type": "gift",
                    "user_name": user_name,
                    "content": f"{user_name} 赠送了 {count}个 礼物{gift_name}"
                })

            # 处理点赞
            for like in feed.like_feeds:
                user_name = like.user.user_name if like.user else "神秘人"
                logger.warning(f"感谢 {user_name} 点赞了直播间")
                self.message_queue.put({
                    "type": "like",
                    "user_name": user_name,
                    "content": f"{user_name} 点赞了直播间"
                })

            # 处理系统通知
            for notice in feed.system_notice_feeds:
                user_name = notice.user.user_name if notice.user else "系统"
                logger.success(f"📢 {user_name}: {notice.content}")
                self.message_queue.put({
                    "type": "system_notice",
                    "user_name": user_name,
                    "content": f"{user_name}: {notice.content}"
                })

            # 处理分享消息
            for share in feed.share_feeds:
                user_name = share.user.user_name if share.user else "神秘人"
                logger.success(f"📤 {user_name} 分享了直播间")
                self.message_queue.put({
                    "type": "share",
                    "user_name": user_name,
                    "content": f"📤 {user_name} 分享了直播间"
                })

        except Exception as e:
            logger.error(f"处理Feed推送失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理Feed推送失败: {str(e)}"
            })

    # ===== 红包处理 =====
    def _handle_red_pack_feed(self, payload):
        try:
            redpack = SCWebCurrentRedPackFeed()
            redpack.ParseFromString(payload)

            for pack in redpack.red_pack:
                author_name = pack.author.user_name if pack.author else "神秘人"
                amount = pack.balance / 100  # 转换为元
                open_time = datetime.fromtimestamp(pack.open_time / 1000) if pack.open_time else "未知时间"

                # 存储红包信息
                self.redpacks[pack.id] = {
                    "author": author_name,
                    "amount": amount,
                    "open_time": open_time,
                    "grab_token": pack.grab_token
                }

                logger.success(f"🧧 红包通知 | 来自: {author_name} | 金额: ¥{amount:.2f} | 开抢时间: {open_time}")
                self.message_queue.put({
                    "type": "red_pack",
                    "user_name": author_name,
                    "content": f"🧧 红包通知 | 来自: {author_name} | 金额: ¥{amount:.2f} | 开抢时间: {open_time}"
                })
        except Exception as e:
            logger.error(f"处理红包失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理红包失败: {str(e)}"
            })

    # ===== 观众列表处理 =====
    def _handle_watching_list(self, payload):
        """处理观众列表更新（包含新用户进入房间）"""
        try:
            # 解析消息
            if not payload:
                logger.warning("⚠️ 收到空的观众列表消息")
                self.message_queue.put({
                    "type": "system",
                    "user_name": "系统",
                    "content": "⚠️ 收到空的观众列表消息"
                })
                return

            msg = SCWebLiveWatchingUsers()
            msg.ParseFromString(payload)

            # 1. 更新在线人数
            if msg.display_watching_count:
                prev_count = self.live_status.get("audience_count", "0")
                new_count = msg.display_watching_count

                # 只在人数变化较大时打印
                if new_count != prev_count:
                    self.live_status["audience_count"] = new_count
                    self.live_status["last_audience_update"] = time.time()
                    logger.info(f"👥 在线观众: {new_count}")
                    self.message_queue.put({
                        "type": "audience_count",
                        "user_name": "系统",
                        "content": new_count
                    })

            # 2. 处理新进入房间的用户
            # 当前在线用户ID
            self.current_online_users = [user_info for user_info in msg.watching_user]

            for user_info in self.current_online_users:
                # 只处理新进入的用户（非离线状态）, 包含离开再次进入用户, 不打印已经离开的用户与在已在房间也不是新进入的用户与在户
                user_id = user_info.user.principal_id
                if user_id not in self.all_entered_users and not user_info.offline:
                    self.all_entered_users.add(user_id)
                    self._process_user_enter(user_info)
                if user_id in self.all_entered_users and user_info.offline:
                    self.all_entered_users.remove(user_id)

        except Exception as e:
            logger.error(f"处理观众列表失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理观众列表失败: {str(e)}"
            })

    def _process_user_enter(self, user_info):
        """处理用户进入房间事件"""
        try:
            if not user_info.user:
                return

            user_name = user_info.user.user_name
            # 获取用户身份信息
            identity = ""

            if user_info.live_assistant_type == WebLiveAssistantType.SUPER_WEB_ASSISTANT:
                identity = "【房管】"
            elif user_info.live_assistant_type == WebLiveAssistantType.JUNIOR_WEB_ASSISTANT:
                identity = "【助理】"
            elif user_info.tuhao:
                identity = "【土豪】"

            # 获取用户财富等级（如果有）
            wealth_info = ""
            if hasattr(user_info, "wealth_grade") and user_info.wealth_grade > 0:
                wealth_info = f" (财富等级:{user_info.wealth_grade})"

            # 打印用户进入消息
            logger.success(f"🚪 {identity}{user_name}{wealth_info} 进入了直播间")
            self.message_queue.put({
                "type": "user_enter",
                "user_name": user_name,
                "content": f"🚪 {identity} {user_name} {wealth_info} 进入了直播间"
            })

        except Exception as e:
            logger.error(f"处理用户进入事件失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理用户进入事件失败: {str(e)}"
            })

    # ===== 富文本评论处理 =====
    def _handle_rich_text_comment(self, payload):
        try:
            rich_text = SCCommentZoneRichText()
            rich_text.ParseFromString(payload)

            for msg in rich_text.message:
                segments = []
                for seg in msg.segment:
                    if seg.text_segment:
                        text = seg.text_segment.text
                        segments.append(text)
                    elif seg.icon_segment:
                        icon_text = seg.icon_segment.text
                        segments.append(f"[图标:{icon_text}]")
                    elif seg.gift_segment:
                        gift_id = seg.gift_segment.gift_id
                        gift_name = self.gift_map.get(gift_id, f"礼物{gift_id}")
                        segments.append(f"[礼物:{gift_name}]")

                if segments:
                    user_id = msg.user_id
                    comment = ''.join(segments)
                    logger.success(f"✨ 富文本弹幕({user_id}): {comment}")
                    self.message_queue.put({
                        "type": "rich_text_comment",
                        "user_id": user_id,
                        "content": comment
                    })
        except Exception as e:
            logger.error(f"处理富文本弹幕失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理富文本弹幕失败: {str(e)}"
            })

    # ===== 竞猜处理 =====
    def _handle_guess_opened(self, payload):
        try:
            guess = SCWebGuessOpened()
            guess.ParseFromString(payload)
            self.active_guess = guess.guess_id
            deadline = datetime.fromtimestamp(guess.submit_deadline / 1000) if guess.submit_deadline else "未知时间"
            logger.warning(f"🎲 竞猜开启! ID:{guess.guess_id} | 截止时间: {deadline}")
            self.message_queue.put({
                "type": "guess_opened",
                "user_name": "系统",
                "content": f"🎲 竞猜开启! ID:{guess.guess_id} | 截止时间: {deadline}"
            })
        except Exception as e:
            logger.error(f"处理竞猜开启失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理竞猜开启失败: {str(e)}"
            })

    def _handle_guess_closed(self, payload):
        try:
            guess = SCWebGuessClosed()
            guess.ParseFromString(payload)
            self.active_guess = None
            logger.info(f"🎲 竞猜结束! ID:{guess.guess_id}")
            self.message_queue.put({
                "type": "guess_closed",
                "user_name": "系统",
                "content": f"🎲 竞猜结束! ID:{guess.guess_id}"
            })
        except Exception as e:
            logger.error(f"处理竞猜结束失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理竞猜结束失败: {str(e)}"
            })

    # ===== 系统警告处理 =====
    def _handle_warning_mask(self, payload):
        try:
            warning = SCLiveWarningMaskStatusChangedAudience()
            warning.ParseFromString(payload)

            if warning.display_mask:
                self.live_status["warning_mask"] = True
                logger.critical(f"⛔ 直播间警告: {warning.warning_mask.title}")
                self.message_queue.put({
                    "type": "warning_mask",
                    "user_name": "系统",
                    "content": f"⛔ 直播间警告: {warning.warning_mask.title}"
                })
                logger.critical(f"警告详情: {warning.warning_mask.detail}")
                self.message_queue.put({
                    "type": "warning_mask",
                    "user_name": "系统",
                    "content": f"警告详情: {warning.warning_mask.detail}"
                })
            else:
                self.live_status["warning_mask"] = False
                logger.info("✅ 直播间警告解除")
                self.message_queue.put({
                    "type": "warning_mask",
                    "user_name": "系统",
                    "content": "✅ 直播间警告解除"
                })
        except Exception as e:
            logger.error(f"处理警告蒙层失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理警告蒙层失败: {str(e)}"
            })

    # ===== 违规处理 =====
    def _handle_violation(self, payload):
        try:
            msg = SCWebSuspectedViolation()
            msg.ParseFromString(payload)

            if msg.suspected_violation:
                self.live_status["violation"] = True
                logger.critical("⚠️ 直播间涉嫌违规内容！请谨慎发言")
                self.message_queue.put({
                    "type": "violation",
                    "user_name": "系统",
                    "content": "⚠️ 直播间涉嫌违规内容！请谨慎发言"
                })
            else:
                self.live_status["violation"] = False
                logger.info("✅ 违规状态解除")
                self.message_queue.put({
                    "type": "violation",
                    "user_name": "系统",
                    "content": "✅ 违规状态解除"
                })
        except Exception as e:
            logger.error(f"处理违规警告失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理违规警告失败: {str(e)}"
            })

    # ===== 配置状态处理 =====
    def _handle_config_state(self, payload):
        try:
            config = SCWebLiveSpecialAccountConfigState()
            config.ParseFromString(payload)

            for item in config.config_switch_item:
                switch_type = ConfigSwitchType.Name(item.config_switch_type)
                logger.info(f"⚙️ 配置更新: {switch_type} = {'开启' if item.value else '关闭'}")
                self.message_queue.put({
                    "type": "config_update",
                    "user_name": "系统",
                    "content": f"⚙️ 配置更新: {switch_type} = {'开启' if item.value else '关闭'}"
                })
        except Exception as e:
            logger.error(f"处理配置状态失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理配置状态失败: {str(e)}"
            })

    # ===== 其他消息类型处理 =====
    def _handle_ride_changed(self, payload):
        logger.info("🐎 坐骑状态变化")

    def _handle_bet_changed(self, payload):
        logger.info("🎰 押注状态变化")

    def _handle_bet_closed(self, payload):
        logger.info("🎰 押注已结束")

    def _handle_pk_statistic(self, payload):
        logger.info("🥊 PK统计数据更新")

    def _handle_refresh_wallet(self, payload):
        logger.info("💰 钱包状态刷新")

    def _handle_chat_switch(self, payload):
        logger.info("💬 互动聊天切换业务")

    def _handle_chat_closed(self, payload):
        logger.info("💬 互动聊天已关闭")

    # ===== 错误处理 =====
    def _handle_error(self, payload):
        try:
            error_msg = SCWebError()
            error_msg.ParseFromString(payload)
            logger.error(f"❌ 服务器错误 [{error_msg.code}-{error_msg.sub_code}]: {error_msg.msg}")
            if "直播已停止" in error_msg.msg:
                self.message_queue.put({
                    "type": "system",
                    "user_name": "系统",
                    "content": f"❌ 直播已停止 [{error_msg.code}-{error_msg.sub_code}]: {error_msg.msg}"
                })
                self.stop(reason="直播已停止")
        except Exception as e:
            logger.error(f"处理错误消息失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理错误消息失败: {str(e)}"
            })

    def _handle_info(self, payload):
        try:
            msg = SCInfo()
            msg.ParseFromString(payload)

            # 根据代码判断消息级别
            if msg.code >= 500:  # 严重错误
                logger.error(f"❌ 系统错误: {msg.msg} (代码: {msg.code})")
            else:  # 一般信息
                logger.info(f"ℹ️ 系统消息: {msg.msg}")

            # 放入消息队列
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"ℹ️ 系统消息: {msg.msg} (代码: {msg.code})"
            })
        except Exception as e:
            logger.error(f"处理系统消息失败: {str(e)}")
            self.message_queue.put({
                "type": "system",
                "user_name": "系统",
                "content": f"处理系统消息失败: {str(e)}"
            })

    # ===== 客户端控制方法 =====
    def stop(self, reason="用户请求"):
        """安全停止客户端"""
        try:
            # 1. 设置停止标志
            self._stop_flag = True
            self.heartbeat_active = False
            self.room_entered = False

            # 2. 关闭WebSocket连接
            if self.websocket:
                logger.info(f"🛑 正在停止客户端 ({reason})...")
                self.websocket.close()
                self.websocket = None

            # 3. 清理资源
            self.cleanup_threads()
            logger.success("✅ 客户端已安全停止")
        except Exception as e:
            logger.error(f"❌ 停止客户端时发生错误: {str(e)}")

    def cleanup_threads(self):
        """清理所有后台线程"""
        # 等待所有线程结束
        time.sleep(0.5)  # 给线程一点时间响应
        logger.info("🔄 清理后台线程...")

        # 重置状态
        self._stop_flag = False
        self.message_stats.clear()

    def start(self):
        """启动WebSocket客户端"""
        # 检查是否应该停止
        if self.stop_event and self.stop_event.is_set():
            return

        # 验证必要信息
        if not all([self.ws_url, self.live_stream_id, self.token]):
            logger.error("❌ 无法启动: 缺少必要的连接参数")
            return

        logger.info(f"🔗 开始连接: {self.ws_url}")
        print(f"🔗 开始连接: {self.ws_url}")

        # 禁用SSL验证
        ssl_opt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

        # 创建WebSocket客户端
        self.websocket = WebSocketApp(
            self.ws_url,
            header=self.headers,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )

        self.heartbeat_active = True
        self.room_entered = False

        # 在独立线程中运行WebSocket客户端
        ws_thread = threading.Thread(
            target=self.websocket.run_forever,
            kwargs={
                "ping_interval": 20,
                "ping_timeout": 10,
                "sslopt": ssl_opt
            },
            daemon=True,
            name="WebSocketThread"
        )
        ws_thread.start()
        logger.info("👂 开始监听消息... (按Ctrl+C退出)")
        print("👂 开始监听消息... (按Ctrl+C退出)")

        try:
            # 主线程监控连接状态
            while (self.heartbeat_active and
                   ws_thread.is_alive() and
                   not (self.stop_event and self.stop_event.is_set())):
                time.sleep(5)
        except KeyboardInterrupt:
            logger.info("🛑 用户终止程序")
        finally:
            self.heartbeat_active = False
            if self.websocket:
                self.websocket.close()
            logger.info("程序已退出")

    def on_error(self, ws, error):
        """WebSocket错误回调"""
        # 检查是否应该停止
        if self.stop_event and self.stop_event.is_set():
            return

        logger.error(f"🛑 WebSocket错误: {str(error)}")
        self.heartbeat_active = False

    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket关闭回调"""
        # 检查是否应该停止
        if self.stop_event and self.stop_event.is_set():
            return

        self.heartbeat_active = False
        self.room_entered = False
        logger.info(f"🔌 连接已关闭, 状态码: {close_status_code}, 消息: {close_msg or '无消息'}")


class KwaiLiveProcess(QObject):
    warning_signal = pyqtSignal(str)

    def __init__(self, live_url, message_queue):
        super().__init__()
        self.live_url = live_url
        self.process = None
        self.message_queue = message_queue
        self.stop_event = threading.Event()  # 添加停止事件
        self.client = None  # 保存KwaiLiveDanmuClient实例

    def start(self):
        """启动 KwaiLiveDanmuClient 线程"""
        if self.process and self.process.is_alive():
            print("快手进程已在运行中")
            return False

        # 重置停止事件
        self.stop_event.clear()

        self.message_queue.put({
            "type": "system",
            "user_name": "系统",
            "content": "弹幕启动中 . . ."
        })

        # 创建新线程
        self.process = threading.Thread(
            target=self._run_kwai_live,
            daemon=True
        )
        self.process.start()

        self.message_queue.put({
            "type": "system",
            "user_name": "系统",
            "content": "KwaiLiveDanmuClient 进程已启动."
        })
        logger.info("KwaiLiveDanmuClient 进程已启动")
        return True

    def stop(self):
        """停止 KwaiLiveDanmuClient 线程"""
        if self.process and self.process.is_alive():
            # 设置停止事件
            self.stop_event.set()

            # 停止KwaiLiveDanmuClient
            if self.client:
                self.client.stop()

            # 等待线程结束
            self.process.join(timeout=5)  # 最多等待5秒

            # 清空消息队列以避免task_done错误
            self._clear_message_queue()

            print("KwaiLiveDanmuClient 进程已停止")
            return True
        else:
            print("没有正在运行的快手进程")
            return False

    def _clear_message_queue(self):
        """清空消息队列以避免task_done错误"""
        try:
            while True:
                self.message_queue.get_nowait()
        except queue.Empty:
            pass  # 队列已空

    def _run_kwai_live(self):
        """在子线程中运行 KwaiLiveDanmuClient"""
        # 获取直播信息
        websocket_info_path = os.path.join(os.path.dirname(__file__), "webinfo", "ghoulish_data.json")
        ghoulish_gift_info_path = os.path.join(os.path.dirname(__file__), "webinfo", "ghoulish_gift.json")

        # 获取直播信息
        with open(websocket_info_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        with open(ghoulish_gift_info_path, "r", encoding="utf-8") as f:
            gift_map = json.load(f)

        if "captured_requests" not in data.keys():
            data = get_new_token(self.live_url, websocket_info_path)
        if data["target_url"] not in self.live_url:
            data = get_new_token(self.live_url, websocket_info_path)

        for response in data["captured_requests"]:
            if response["response"]["body"]["data"]["result"] == 1:
                data = response
                break
        else:
            print("❌ 无法获取直播信息")
            self.warning_signal.emit("无法获取直播信息")
            return

        # 检查是否应该停止
        if self.stop_event.is_set():
            return

        # 创建客户端实例
        self.client = KwaiLiveDanmuClient()

        # 设置连接参数
        self.client.set_connection_params(
            ws_url=data["response"]["body"]['data']['websocketUrls'][-1],
            live_stream_id=data["query_params"]["liveStreamId"],
            token=data["response"]["body"]['data']['token'],
            headers=data["headers"],
            cookies=data["cookies"],
            session_page_id=data["session_page_id"],
            live_urls=self.live_url,
            gift_maps=gift_map,
            message_queue=self.message_queue,
            stop_event=self.stop_event  # 传递停止事件
        )

        # 检查是否应该停止
        if self.stop_event.is_set():
            return

        # 启动客户端
        self.client.start()

    def get_messages(self):
        """从消息队列中获取消息"""
        messages = []
        while not self.message_queue.empty():
            try:
                messages.append(self.message_queue.get_nowait())
            except:
                break
        return messages


if __name__ == "__main__":
    # 创建 KwaiLiveProcess 实例
    kwai_live_url = "https://live.kuaishou.com/u/3xxgygyreajsxca"
    message_queue = queue.Queue()  # 使用线程安全的队列
    kwai_process = KwaiLiveProcess(kwai_live_url, message_queue)

    try:
        # 启动两个进程
        if kwai_process.start():
            # 主循环，处理消息
            while True:
                # 获取并处理快手消息
                kwai_messages = kwai_process.get_messages()
                for msg in kwai_messages:
                    print(f"快手消息: {msg}")

                # 等待一段时间
                time.sleep(1)

    except KeyboardInterrupt:
        print("收到中断信号，准备停止进程...")
    finally:
        # 停止进程
        kwai_process.stop()
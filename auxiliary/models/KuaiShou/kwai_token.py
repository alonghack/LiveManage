


import json
import os
import sys
import time
import re
import base64
import platform
import random
from datetime import datetime

from loguru import logger
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright


def generate_user_agent():
    """生成随机且真实的用户代理"""
    # 操作系统特定部分
    if platform.system() == "Windows":
        os_part = f"(Windows NT {random.choice(['10.0', '11.0'])}; Win64; x64)"
    elif platform.system() == "Darwin":  # macOS
        versions = [f"10_{i}" for i in range(15, 16)] + [f"11_{i}" for i in range(0, 4)]
        os_part = f"(Macintosh; Intel Mac OS X {random.choice(versions)}_0)"
    else:  # Linux
        os_part = "(X11; Linux x86_64)"

    # 浏览器引擎部分
    engine = random.choice([
        ("AppleWebKit/605.1.15", "Safari/605.1.15"),
        (f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(120, 125)}.0.0.0", f"Safari/537.36"),
        (f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(120, 125)}.0.0.0",
         f"Edg/{random.randint(120, 125)}.0.0.0")
    ])

    return f"Mozilla/5.0 {os_part} {engine[0]} {engine[1]}"

# 检查已安装浏览器
def check_browser_installed():
    """检查系统是否安装所需浏览器并返回路径"""
    browsers = {
        "Chrome": {
            "win": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "mac": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "linux": "/usr/bin/google-chrome-stable"
        },
        "Edge": {
            "win": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "mac": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "linux": "/usr/bin/microsoft-edge"
        }
    }

    # 检测操作系统
    if sys.platform.startswith('win'):
        os_type = 'win'
    elif sys.platform.startswith('darwin'):
        os_type = 'mac'
    elif sys.platform.startswith('linux'):
        os_type = 'linux'
    else:
        return None

    # 检查浏览器安装状态
    client = {}
    for name, paths in browsers.items():
        path = paths.get(os_type)
        if path and os.path.exists(path):
            client[name] = path
            continue

    return client


# 防检测浏览器启动器
def launch_stealth_browser(p):
    """启动防检测浏览器"""
    # 浏览器启动参数
    args = [
        "--disable-blink-features=AutomationControlled",
        "--ignore-certificate-errors",
        "--start-maximized",
        "--disable-infobars",
        "--disable-extensions",
        "--disable-notifications",
        "--disable-popup-blocking",
        "--disable-web-security",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-site-isolation-trials",
        f"--user-agent={generate_user_agent()}",
        "--remote-debugging-port=0"  # 随机端口
    ]

    # 添加随机浏览器指纹
    if random.random() > 0.5:
        args.append("--enable-features=WebRTC-HideLocalIPs")

    clients = check_browser_installed()
    if clients:
        print(clients)
    else:
        print("未检测到浏览器")
        return None

    # 启动本地浏览器
    browser = p.chromium.launch(
        executable_path= random.choice(list(clients.values())),
        headless=False,
        args=args,
        slow_mo=random.randint(50, 300),
        timeout=120000
    )



    # 创建浏览器上下文
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        ignore_https_errors=True,
        # locale="zh-CN",
        # timezone_id="Asia/Shanghai",
        color_scheme="light",
        permissions=["geolocation"],
        storage_state="browser_state.json" if os.path.exists("browser_state.json") else None
    )

    # 应用反检测脚本
    context.add_init_script("""
        // 删除自动化特征
        delete navigator.__proto__.webdriver;
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // 修改插件属性
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });

        // 修改语言属性
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en'],
        });

        // 修改连接属性
        Object.defineProperty(navigator, 'connection', {
            get: () => ({ downlink: 10, rtt: 100, type: 'wifi' }),
        });

        // 覆盖权限查询
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ? 
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
    """)

    return browser, context


# 人类行为模拟器
class HumanBehaviorSimulator:
    """模拟人类浏览行为"""

    def __init__(self, page):
        self.page = page
        self.safe_classes = ["btn", "button", "link", "tab", "menu-item", "icon"]
        self.danger_classes = ["close", "exit", "cancel", "delete", "remove"]

    def simulate(self, duration=10):
        """模拟人类行为"""
        start_time = time.time()
        logger.info(f"开始模拟人类行为 ({duration}秒)...")

        try:
            while time.time() - start_time < duration and not self.page.is_closed():
                # 随机鼠标移动
                self.random_mouse_move()

                # 随机页面滚动
                self.random_scroll()

                # 随机点击页面元素
                self.random_click()

                # 随机等待
                time.sleep(random.uniform(0.5, 2.0))
        except Exception as e:
            logger.error(f"模拟行为出错: {str(e)}")

    def random_mouse_move(self):
        """随机移动鼠标"""
        if self.page.is_closed():
            return

        x = random.randint(100, self.page.viewport_size["width"] - 100)
        y = random.randint(100, self.page.viewport_size["height"] - 100)
        steps = random.randint(5, 15)
        self.page.mouse.move(x, y, steps=steps)
        logger.debug(f"鼠标移动到 ({x}, {y}), 步数: {steps}")

    def random_scroll(self):
        """随机页面滚动"""
        if self.page.is_closed() or random.random() <= 0.3:  # 30%概率不滚动
            return

        scroll_y = random.randint(300, 800)
        self.page.mouse.wheel(0, scroll_y)
        logger.debug(f"页面滚动 {scroll_y} 像素")

    def random_click(self):
        """安全随机点击页面元素"""
        if self.page.is_closed() or random.random() <= 0.8:  # 20%概率点击
            return

        try:
            # 查找安全的可点击元素
            safe_selectors = [
                "a:not(.close)",
                "button:not(.close)",
                ".btn:not(.close)",
                ".button:not(.close)",
                ".card",
                ".item"
            ]

            # 添加安全的类选择器
            for cls in self.safe_classes:
                safe_selectors.append(f".{cls}")

            # 排除危险类
            for danger_cls in self.danger_classes:
                safe_selectors.append(f":not(.{danger_cls})")

            selector = ", ".join(safe_selectors)
            clickable_elements = self.page.query_selector_all(selector)

            if clickable_elements:
                # 过滤可见元素
                visible_elements = [el for el in clickable_elements if el.is_visible()]

                if visible_elements:
                    element = random.choice(visible_elements)

                    # 确保元素在视图中
                    element.scroll_into_view_if_needed()

                    # 安全点击
                    element.click(delay=random.randint(100, 500))
                    logger.debug(f"安全点击元素: {element.get_attribute('class') or element.get_attribute('id')}")
        except Exception as e:
            logger.debug(f"点击元素失败: {str(e)}")




def capture_websocketinfo_request(live_url):
    # API模式
    api_pattern = re.compile(r".*/live_api/liveroom/websocketinfo\?.*")

    # 结果容器
    capture_result = {
        "target_url": live_url,
        "captured_requests": [],
        "error": None,
        "timestamp": datetime.now().isoformat()
    }

    with sync_playwright() as p:
        browser = None
        context = None
        page = None

        try:
            # 启动防检测浏览器
            browser, context = launch_stealth_browser(p)
            page = context.new_page()
            target_requests = []
            capture_success = False  # 捕获成功的标志

            # 存储验证码状态
            captcha_triggered = False

            def handle_response(response):
                nonlocal capture_success, captcha_triggered
                try:
                    if api_pattern.match(response.url):
                        # 获取关联的请求
                        request = response.request

                        # 解析查询参数为简单字典格式（{key: value}）
                        parsed_url = urlparse(response.url)
                        # 获取所有查询参数
                        all_query_params = parse_qs(parsed_url.query)
                        # 转换为简单字典（只取第一个值）
                        query_params = {k: v[0] for k, v in all_query_params.items()}

                        # 准备请求数据
                        request_data = {
                            "url": response.url,
                            "method": request.method,
                            "headers": dict(request.headers),
                            "query_params": query_params,
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "response": {
                                "status": response.status,
                                "headers": dict(response.headers),
                                "body": None
                            },
                            "cookies": {},  # 初始化cookies字典
                            "session_page_id": None  # 初始化session_page_id
                        }

                        try:
                            # 获取sessionStorage
                            session_storage = page.evaluate("() => JSON.stringify(sessionStorage)")
                            if session_storage:
                                session_storage_dict = json.loads(session_storage)
                                session_page_id = session_storage_dict.get("kslive.log.page_id")
                                if session_page_id:
                                    request_data["session_page_id"] = session_page_id
                                    print(f"✅ 获取到session_page_id: {session_page_id}")
                                else:
                                    print("⚠️ 未找到 kslive.log.page_id")
                            else:
                                print("⚠️ 获取sessionStorage失败")
                        except Exception as e:
                            print(f"获取sessionStorage时出错: {str(e)}")
                            request_data["session_page_id"] = "获取失败: " + str(e)

                        # 获取所有cookies并过滤
                        all_cookies = context.cookies()
                        # 将cookies转为 {key: value} 格式
                        cookies_dict = {cookie["name"]: cookie["value"] for cookie in all_cookies}

                        # 保存到请求数据中
                        request_data["cookies"] = cookies_dict

                        # 获取响应体
                        try:
                            text_body = response.text()
                            # 尝试解析为JSON
                            try:
                                json_body = json.loads(text_body)
                                request_data["response"]["body"] = json_body
                                # 检查是否是有效响应
                                if json_body.get("data") and json_body["data"].get("websocketUrls"):
                                    capture_success = True  # 标记捕获成功
                            except:
                                request_data["response"]["body"] = text_body
                        except Exception as e:
                            print(f"获取响应体失败: {str(e)}")
                            request_data["response"]["body"] = None

                        # 添加到目标请求列表
                        target_requests.append(request_data)
                        print(f"✅ 成功捕获目标请求: {response.url}")

                        # 打印查询参数（简化版本）
                        print("\n查询参数:")
                        for key, value in request_data["query_params"].items():
                            print(f"  {key}: {value}")

                        # 打印cookies示例
                        print("\nCookies示例 (前5个):")
                        for i, (name, value) in enumerate(request_data["cookies"].items()):
                            if i < 5:  # 只显示前5个cookie
                                print(f"  {name}: {value[:30]}...")  # 只显示值的前30个字符
                            if i == 4:
                                print(f"  ...共{len(request_data['cookies'])}个cookies")
                                break

                        # 检查验证码触发
                        if (request_data["response"].get("body") and
                                isinstance(request_data["response"]["body"], dict) and
                                request_data["response"]["body"].get("data", {}).get("result") == 400002):
                            captcha_triggered = True
                            print("⚠️ 检测到验证码挑战")

                except Exception as e:
                    print(f"处理响应时出错: {str(e)}")

            page.on("response", handle_response)

            print(f"🚀 正在访问直播页面: {live_url}")
            page.goto(live_url, wait_until="domcontentloaded", timeout=60000)

            # 优化的等待策略：等待特定元素或目标请求出现
            print("⏳ 等待直播内容或API响应...")
            try:
                # 设置等待超时时间（秒）
                wait_timeout = 60
                start_time = time.time()

                while (time.time() - start_time) < wait_timeout:
                    if capture_success:
                        print("✅ 已获取有效的WebSocket信息请求")
                        break

                    if captcha_triggered:
                        print("⏳ 检测到验证码，等待人工解决...")
                        # 在页面上暂停等待
                        page.wait_for_timeout(5000)  # 每5秒检查一次
                    else:
                        # 检查页面元素：播放器或验证码
                        player = page.query_selector("div.live-player")
                        captcha = page.query_selector("div.captcha-container")

                        if player or captcha:
                            print("⏳ 检测到播放器或验证码元素")
                            # 检测到关键元素后快速检查响应
                            page.wait_for_timeout(1000)
                        else:
                            # 没有检测到关键元素，继续等待
                            page.wait_for_timeout(1000)
                else:
                    print("⌛️ 等待超时")

                if capture_success:
                    print("🎉 成功获取所需信息")
                else:
                    print("❌ 未获取到有效的WebSocket信息请求")

            except Exception as e:
                print(f"等待过程中出错: {str(e)}")

            # 将捕获的请求放入结果
            capture_result["captured_requests"] = target_requests

            if not target_requests:
                capture_result["error"] = "未捕获到目标API请求"
            else:
                # 打印最后一个请求的关键信息
                last_req = target_requests[-1]
                print("\n===== 最新请求信息 =====")
                print(f"URL: {last_req['url']}")
                print(f"方法: {last_req['method']}")
                print(f"状态码: {last_req['response']['status']}")

                # 打印查询参数
                print("\n查询参数:")
                for key, value in last_req["query_params"].items():
                    print(f"  {key}: {value}")

                # 打印cookies总数
                print(f"\nCookies (共{len(last_req['cookies'])}个):")
                # 只打印部分重要的cookie
                important_cookies = ['kuaishou.live.web_st', 'kuaishou.web.cp.api_st', 'kuaishou.live.web_ph',
                                     'kuaishou.web.cp.api_ph', 'kwssectoken', '__NS_hxfalcon']

                printed = 0
                for name, value in last_req["cookies"].items():
                    if name in important_cookies or printed < 3:
                        print(f"  {name}: {value[:50]}...")  # 只显示值的前50个字符
                        printed += 1

                if printed < len(last_req["cookies"]):
                    print(f"  ...和其他{len(last_req['cookies']) - printed}个cookies")

                # 打印session_page_id
                if last_req.get("session_page_id"):
                    print(f"\nkslive.log.page_id: {last_req['session_page_id']}")
                else:
                    print("\n⚠️ 未获取到session_page_id")

                # 打印响应体
                print("\n响应内容:")
                body = last_req["response"]["body"]
                if isinstance(body, dict):
                    # 简化显示响应体 - 只显示关键字段
                    simplified_body = body.copy()
                    if "data" in body and "websocketUrls" in body["data"]:
                        ws_urls = body["data"]["websocketUrls"]
                        # 保留第一个和最后一个，中间用...代替
                        if len(ws_urls) > 3:
                            simplified_body["data"]["websocketUrls"] = [
                                ws_urls[0],
                                f"... (共{len(ws_urls) - 2}个中间项) ...",
                                ws_urls[-1]
                            ]
                    print(json.dumps(simplified_body, indent=2, ensure_ascii=False))
                elif isinstance(body, str):
                    # 只显示前200个字符
                    print(body[:200] + ("..." if len(body) > 200 else ""))
                else:
                    print(f"响应类型: {type(body)}")
                print("=" * 50)

        except Exception as e:
            print(f"❌ 操作出错: {str(e)}")
            capture_result["error"] = str(e)
        finally:
            print("🛑 正在关闭浏览器...")
            try:
                # 关闭浏览器上下文
                context.close()
            except:
                pass

            try:
                # 关闭浏览器
                browser.close()
            except Exception as e:
                print(f"关闭浏览器时发生错误: {str(e)}")
                print("⚠️ 此错误可忽略，数据已成功捕获")

    return capture_result


def save_results(results, websocket_info_path):
    """保存结果到文件"""
    # 获取当前文件的绝对路径
    current_path = os.path.abspath(__file__)

    # 获取当前文件所在的目录路径
    current_dir = os.path.dirname(current_path)
    if not results or ("error" in results and results["error"]):
        return

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    json_filename = os.path.join("webinfo", "ghoulish_websocket.json")

    # 准备保存的数据
    save_data = {
        "target_url": results["target_url"],
        "captured_requests": [],
        "timestamp": timestamp
    }

    # 处理每个请求的数据
    for req in results["captured_requests"]:
        # 创建精简版请求对象
        simple_req = {
            "url": req["url"],
            "method": req["method"],
            "query_params": req["query_params"],  # 已经是字典格式
            "headers": req["headers"],
            "cookies": req["cookies"],  # 已经是字典格式
            "session_page_id": req.get("session_page_id", None),  # 添加session_page_id
            "response": {
                "status": req["response"]["status"],
                "headers": req["response"]["headers"]
            }
        }

        # 处理响应体
        body = req["response"]["body"]
        if isinstance(body, bytes):
            # 如果是字节数据，转换为base64
            simple_req["response"]["body"] = base64.b64encode(body).decode("utf-8")
            simple_req["response"]["body_encoding"] = "base64"
        else:
            simple_req["response"]["body"] = body

        save_data["captured_requests"].append(simple_req)

    # 保存到文件
    with open(websocket_info_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完整结果已保存到: {json_filename}")

    return save_data


def get_new_token(url, websocket_info_path):
    """从文件中获取新的token"""
    print("=" * 80)
    print("快手直播 WebSocket 信息捕获工具")
    print("=" * 80)

    results = capture_websocketinfo_request(url)
    save_data = save_results(results, websocket_info_path    )

    print("\n操作完成!")
    return save_data

if __name__ == "__main__":
    websocket_info_path = "socket.json"
    live_url = "https://live.kuaishou.com/u/RERWP43420"
    get_new_token(live_url, websocket_info_path)




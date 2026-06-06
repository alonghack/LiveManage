# 📺 直播助理 (LiveManage)

基于 **PyQt6** 的多功能直播辅助工具，支持**抖音 / 快手**平台直播互动（B站、视频号、小红书已预留接口）。

> **仅 Windows 平台** — 依赖 DirectShow、Win32 API。

## ✨ 核心功能

### 🎥 视频源
| 源类型 | 说明 |
|--------|------|
| 物理摄像头 | OpenCV 直连，支持多种分辨率和帧率 |
| 屏幕捕获 | 全屏捕获，可选包含光标 |
| 区域捕获 | 拖拽选择屏幕任意区域 |
| 窗口捕获 | 指定应用窗口 |
| 视频文件 | 本地视频循环播放（OpenCV VideoCapture） |
| 图片/视频模板 | 图片或视频叠加背景音乐循环 |

### 📡 虚拟摄像头
- **自研 DirectShow 滤镜** (`vcam_filter.dll`) — 纯 C 实现，无需 OBS
- **免管理员权限** — HKCU 注册，开箱即用
- **OBS / Chrome / 直播伴侣 / Teams / Zoom** 兼容
- **obs-virtual-cam 队列协议** — 环形缓冲共享内存 IPC
- **防检测滤镜** — 随机噪声、亮度变化、对焦模拟、伪影、缩放（仅作用于虚拟摄像头输出）
- **每模式独立分辨率** — 视频/摄像头/屏幕/区域/模板各支持 1920×1080 ~ 3840×2160 独立选择

### 💬 弹幕系统
- **抖音**：WebSocket + Protobuf，`sign.js` 签名
- **快手**：WebSocket + Protobuf，Playwright 浏览器自动获取令牌
- **自动回复**：关键词匹配 → 随机/指定回复 → 可选 TTS 语音播报
- **事件解说**：进入/点赞/送礼/关注/分享触发

### 🔊 TTS 语音合成
- **Spark-TTS 0.5B** 模型，本地推理
- **声音克隆**：通过示例音频 (`example/prompt/`) 克隆音色
- **可控合成**：指定性别、音高、语速
- **双队列系统**：合成队列 + 播放队列

### 📚 RAG 知识库
- **LlamaIndex** + **ChromaDB** 向量存储
- **BAAI/bge-base-zh-v1.5** 嵌入模型（本地）
- **Qwen2.5-0.5B-Instruct** 生成模型（本地）
- 支持构建索引、添加文档、问答查询

---

## 📦 安装

### 环境要求
- **OS**：Windows 10/11
- **Python**：3.12+
- **GCC**：MinGW-w64（仅构建时需要，编译 `vcam_filter.dll`）
- **Node.js**：npm（抖音/快手签名计算）

### 🤖 模型下载

项目使用 4 个本地模型，**首次运行时会自动从 HuggingFace 下载**。如果网络受限，可从 **ModelScope** 手动下载放置。

### 模型清单

| 模型 | 用途 | HF 仓库 | ModelScope 仓库 |
|------|------|---------|-----------------|
| BAAI/bge-base-zh-v1.5 | RAG 文本嵌入 | `BAAI/bge-base-zh-v1.5` | `BAAI/bge-base-zh-v1.5` |
| Qwen/Qwen2.5-0.5B-Instruct | RAG 问答生成 | `Qwen/Qwen2.5-0.5B-Instruct` | `Qwen/Qwen2.5-0.5B-Instruct` |
| SparkAudio/Spark-TTS-0.5B | TTS 语音合成 | `SparkAudio/Spark-TTS-0.5B` | `SparkAudio/Spark-TTS-0.5B` |
| SparkTTS (源码包) | TTS 代码 + 配置 | 随项目分发 | - |

### 手动下载（ModelScope）

#### 1. 安装 ModelScope CLI

```bash
pip install modelscope
```

#### 2. 下载 RAG 嵌入模型

```bash
modelscope download --model BAAI/bge-base-zh-v1.5 --local_dir models/BAAI/bge-base-zh-v1.5
```

**放置位置**：`models/BAAI/bge-base-zh-v1.5/`

```
models/BAAI/bge-base-zh-v1.5/
├── config.json
├── model.safetensors        # 或 pytorch_model.bin
├── tokenizer.json
├── tokenizer_config.json
├── special_tokens_map.json
├── vocab.txt
└── 1_Pooling/config.json
```

#### 3. 下载 RAG 生成模型

```bash
modelscope download --model Qwen/Qwen2.5-0.5B-Instruct --local_dir models/Qwen/Qwen2.5-0.5B-Instruct
```

**放置位置**：`models/Qwen/Qwen2.5-0.5B-Instruct/`

```
models/Qwen/Qwen2.5-0.5B-Instruct/
├── config.json
├── model.safetensors
├── tokenizer.json
├── tokenizer_config.json
├── vocab.json
├── merges.txt
└── ...
```

#### 4. 下载 TTS 模型

```bash
modelscope download --model SparkAudio/Spark-TTS-0.5B --local_dir models/SparkAudio/Spark-TTS-0.5B
```

**放置位置**：`models/SparkAudio/Spark-TTS-0.5B/`

```
models/SparkAudio/Spark-TTS-0.5B/    ← 代码中 model_dir 指向这里
├── LLM/
│   ├── config.json
│   ├── model.safetensors            # 或 pytorch_model.bin
│   └── ...
├── BiCodec/
│   ├── config.json
│   ├── model.safetensors
│   └── ...
├── wav2vec2-large-xlsr-53/
│   ├── config.json
│   ├── pytorch_model.bin
│   └── ...
├── speaker/
│   ├── config.json
│   ├── model.safetensors
│   └── ...
├── vocoder/
│   ├── config.json
│   ├── model.safetensors
│   └── ...
└── config.json                       # Spark-TTS 主配置
```

#### 5. TTS 源码包（随项目分发）

`models/SparkTTS/` 目录内是 Spark-TTS 的 Python 源码（`sparktts/` 包 + `cli/`），**非模型权重**，无需从 ModelScope 下载。如果缺失，从 HuggingFace 仓库 `SparkAudio/Spark-TTS-0.5B` 下载整个仓库即可。

### 验证安装

```bash
# 检查所有模型文件
ls models/BAAI/bge-base-zh-v1.5/model.safetensors && echo "✓ bge" || echo "✗ bge"
ls models/Qwen/Qwen2.5-0.5B-Instruct/model.safetensors && echo "✓ qwen" || echo "✗ qwen"
ls models/SparkAudio/Spark-TTS-0.5B/LLM/model.safetensors && echo "✓ tts-llm" || echo "✗ tts-llm"
ls models/SparkAudio/Spark-TTS-0.5B/BiCodec/model.safetensors && echo "✓ tts-bicodec" || echo "✗ tts-bicodec"
ls models/SparkTTS/sparktts/__init__.py && echo "✓ tts-src" || echo "✗ tts-src"
```

> **提示**：如果 HuggingFace 网络畅通，启动应用时缺少模型文件会自动下载，不需要手动操作。

---

### 快速开始

```bash
# 1. 克隆项目
git clone <repo-url>
cd LiveManage

# 2. 创建虚拟环境
python -m venv .venv
source .venv/Scripts/activate  # Windows Git Bash
# 或: .venv\Scripts\activate   # Windows CMD

# 3. 安装 Python 依赖
pip install -r requirements.txt

# 4. 安装 npm 依赖（sign.js 签名计算）
cd auxiliary/models && npm install && cd ../..

# 5. 安装 Playwright 浏览器（快手令牌获取）
playwright install chromium

# 6. 启动应用
python main.py
```

### 虚拟摄像头编译

首次运行时会自动调用 MinGW GCC 编译。也可手动编译：

```bash
gcc -shared -O2 -std=c17 -m64 \
    -o virtual_cam/vcam_filter/vcam_filter.dll \
    virtual_cam/vcam_filter/vcam_filter.c \
    -lole32 -loleaut32 -luuid -lkernel32 -luser32 -lstrmiids -Wl,--kill-at
```

### 主要依赖

| 包                 | 版本          | 用途                      |
|-------------------|-------------|-------------------------|
| `PyQt6`           | 6.9.1       | 图形界面                    |
| `opencv-python`   | 4.12        | 视频/图像处理                 |
| `loguru`          | 0.7         | 日志                      |
| `numpy`           | 2.2         | 数值计算                    |
| `torch`           | 2.7.1+cu118 | TTS 模型推理                |
| `transformers`    | 4.45        | TTS tokenizer + RAG LLM |
| `tokenizers`      | 0.20        | 分词器                     |
| `safetensors`     | 0.5         | 模型权重加载                  |
| `llama-index`     | 0.14        | RAG 框架                  |
| `chromadb`        | 1.5         | 向量存储                    |
| `sounddevice`     | 0.5         | 音频播放                    |
| `playwright`      | 1.54        | 快手令牌获取 (浏览器自动化)         |
| `mss`             | 10.0        | 屏幕捕获                    |
| `protobuf`        | 6.31        | 直播平台协议解析                |
| `huggingface-hub` | 0.34        | 模型自动下载                  |
| `modelscope`      | 1.37        | 模型手动下载 (ModelScope)     |

---

## 🚀 使用指南

### 界面概览

6 个标签页：**主控制台** | **关键词管理** | **游戏解说话术** | **语音设置** | **模板** | **知识库**

### 主控制台
```
第一行: [▶ ⏹ 🔇] [音量] [模式选择 ▼] [分辨率 ▼] [循环播放 □] [FPS: 0]
第二行: [打开视频/摄像头/屏幕] [====进度条====] [时间显示]
```

操作流程：
1. 选择视频源模式（视频文件 / 摄像头 / 屏幕捕获 / 区域捕获 / 模板）
2. 选择输出分辨率（默认 1920×1080，每模式记忆）
3. 点击 ▶ 开始播放/捕获
4. 虚拟摄像头自动初始化并开始输出

### 虚拟摄像头 API（独立调用）

虚拟摄像头模块可脱离 GUI 独立使用：

```python
from virtual_cam import VirtualCamera

# 启动虚拟摄像头
vcam = VirtualCamera(width=1920, height=1080, fps=30)
vcam.start()

# 在你的帧循环中发送帧 (BGR numpy array)
import cv2
cap = cv2.VideoCapture(0)
while True:
    ret, frame = cap.read()  # OpenCV 默认 BGR
    if ret:
        vcam.send(frame)

# 停止
vcam.stop()
```

带防检测滤镜的用法：

```python
from virtual_cam import VirtualCameraManager, AntiDetectionFilter

manager = VirtualCameraManager()
manager.initialize(1920, 1080, 30)

anti = AntiDetectionFilter({
    'add_noise': False,
    'vary_brightness': False,
    'random_artifacts': False,
    'add_focus_changes': False,
    'random_zoom': False,
})

frame = cv2.imread("frame.png")
processed = anti.process(frame)
manager.send_frame(processed)
```

---

## 📂 项目结构

```
LiveManage/
├── main.py                     # 主入口 (~5000 行: GUI + 视频管线 + 业务逻辑)
├── nuitka_build.py             # Nuitka 独立打包脚本
├── requirements.txt            # Python 依赖
├── README.md
├── CLAUDE.md                   # Claude Code 指导
│
├── virtual_cam/                # 虚拟摄像头（可独立打包）
│   ├── __init__.py             # 公共 API 导出
│   ├── virtual_camera.py       # 共享内存 IPC + COM 注册
│   ├── anti_detection.py       # 防检测帧滤镜
│   ├── register_admin.py       # 管理员注册工具 (HKLM)
│   └── vcam_filter/
│       ├── vcam_filter.c       # DirectShow 源滤镜 (纯 C)
│       ├── vcam_filter.h       # 结构体 + GUID 定义
│       └── vcam_filter.dll     # 编译后 DLL
│
├── auxiliary/                  # 辅助模块
│   ├── screen.py               # VideoWidget + ChatWidget (弹幕 UI)
│   ├── sound.py                # TTS 引擎 (Spark-TTS)
│   ├── region.py               # 屏幕区域选择器
│   ├── utils.py                # SQLite 管理 + UIUpdater
│   ├── logger_config.py        # loguru 统一日志配置
│   ├── models/                 # 直播平台客户端
│   │   ├── TikTok_ZH/          # 抖音 (WebSocket/Protobuf)
│   │   ├── KuaiShou/           # 快手 (WebSocket/Protobuf + Playwright)
│   │   ├── Bilibili/           # (预留)
│   │   ├── ShiPinHao/          # (预留)
│   │   ├── TikTok_EN/          # (预留)
│   │   └── XiaoHongShu/        # (预留)
│   └── logs/                   # 运行日志
│
├── duix/                       # RAG 知识库 + 数字人
│   ├── llama_index_rag.py      # LlamaIndex RAG 系统
│   ├── data/                   # 示例文档
│   ├── chroma_db/              # ChromaDB 持久化存储
│   └── models/                 # 数字人角色模型
│
├── models/                     # 本地模型（权重 + 源码）
│   ├── BAAI/bge-base-zh-v1.5/  # RAG 嵌入模型
│   ├── Qwen/Qwen2.5-0.5B-Instruct/  # RAG LLM
│   ├── SparkAudio/Spark-TTS-0.5B/   # TTS 模型权重
│   │   ├── LLM/                # TTS 语言模型
│   │   ├── BiCodec/            # 音频编解码器
│   │   ├── wav2vec2-large-xlsr-53/  # 语音特征提取
│   │   ├── speaker/            # 说话人编码器
│   │   └── vocoder/            # 声码器
│   └── SparkTTS/               # TTS Python 源码包
│
├── example/                    # 示例资源
│   ├── templates/              # 图片/视频/音乐模板
│   ├── prompt/                 # TTS 语音克隆样本
│   ├── Gift/                   # 礼物动画
│   └── results/                # 示例输出
│
├── sqlite_db/                  # SQLite 数据库
└── icon/                       # 应用图标
```

---

## 🔧 故障排除

### 虚拟摄像头
| 症状 | 解决方案 |
|------|----------|
| OBS 找不到 ManageCamera | 以管理员身份运行 `python virtual_cam/register_admin.py`，然后**重启 OBS** |
| 画面彩色横条 | 重启应用 → DLL 将自动重新初始化共享内存 |
| 快手画面模糊 | 当前使用 YUY2 (4:2:2) 格式；快手直播伴侣会自行做色度子采样。确认源视频为 1080p+ |
| 分辨率切换后画面异常 | DLL 会检测队列重置并自动重新同步 — 等待 2-3 秒即可 |
| 共享内存映射失败 | 重启应用；如果 DLL 已预分配 360MB，第二次启动会复用 |

### 通用问题
| 症状 | 解决方案 |
|------|----------|
| TTS 无法播放 | 检查模型权重是否下载 (`models/SparkAudio/Spark-TTS-0.5B/`) |
| 弹幕接收失败 | 检查直播间 URL 是否正确；快手需确保 Playwright 浏览器已安装 |
| 视频播放异常 | 确认文件格式受 OpenCV 支持；安装 K-Lite Codec Pack |
| 应用无法启动 | 检查 Python 版本 ≥3.10，`pip install -r requirements.txt` |

### 日志

运行时日志位于 `auxiliary/logs/`：
- `debug.log` — DEBUG 级别，50MB 轮转，保留 30 天
- `error.log` — ERROR+ 级别，10MB 轮转，保留 60 天

---

## 🛠️ 构建发布

```bash
# 独立可执行文件构建
python nuitka_build.py

# 输出: output/LiveManage/直播助理.exe
```

构建脚本自动：
1. 编译 `vcam_filter.dll`（如需要）
2. 运行 Nuitka standalone 打包
3. 复制 TTS 模型和 README 到输出目录

---

## 📄 技术架构

```
┌─────────────┐    BGR     ┌──────────────┐   RGB24    ┌──────────────────┐
│  main.py    │ ─────────→ │ virtual_cam/ │ ────────→ │ vcam_filter.dll  │
│  (PyQt6)    │   frame    │ virtual_cam..│ shared mem│  (DirectShow)    │
└─────────────┘            └──────────────┘           └──────┬───────────┘
       │                                                      │
       │  AntiDetectionFilter                                 │ YUY2/NV12/
       │  (仅虚拟摄像头)                                        │ RGB32/I420
       │                                                      ↓
       │                                              ┌──────────────────┐
       └──────────────────────────────────────────────│  OBS / 直播伴侣   │
                        实时预览 (QImage)               │  Chrome / Zoom   │
                                                      └──────────────────┘
```

共享内存协议（源自 obs-virtual-cam）：
```
[queue_header 60B] [elem0: hdr32B + RGB24] [elem1: hdr32B + RGB24] ... [elem9]
         ↑                                                    ↑
    state, write_index, dimensions                  环形缓冲, 10 帧
```

---

## 📞 联系方式

- 微信：**alonghack**
- 提交 Issue 到项目仓库

---

*最后更新：2026 年 6 月*

# MultiLive Desktop — CustomTkinter 低延迟多路监看工具

**状态：可运行的独立桌面实现，不是所提供 APK 的逐功能反编译克隆。**

原始用户文件提供的是 `C:\Users\...\MultiLive-android-v10.5.4.apk` 主机绝对路径；该文件**没有**暴露给当前执行容器，也未出现在本会话可读取的附件中。因此没有读取 APK 清单、DEX 或资源，也不能确认它的具体功能、协议、页面以及“极限追帧”的原始算法。请在可访问的执行环境中提供 APK 本体，之后才能做逐功能核对。本仓库不含原 APK 的任何资源或专有代码。

## 现有功能

- Python 3.10+、CustomTkinter 深色响应式界面、Windows 优先
- 支持 1 / 2 / 4 / 9 路同时监看，可随时切换，隐藏窗口释放解码器
- 手动输入 RTSP / RTMP / HLS (.m3u8) / HTTP-FLV / UDP / SRT 等 **FFmpeg 能读取**的协议，以及本地视频文件
- 低延迟模式：极限 / 均衡 / 稳定；后台解码，始终只保留每路最新一帧，防止 UI 队列积压
- 主动追直播：断开并重连到可获得的直播前沿；断线 / 解码停滞自动重试，指数退避
- 每路独立帧率 / 丢帧计数 / 本地帧龄；UI 和解码线程隔离
- RTSP TCP/UDP 切换；可选 FFmpeg 自动硬解（实验性，默认关闭）
- 频道列表增删、JSON 导入导出、工作区布局自动保存（原子写盘）
- 单路截图 PNG；单路原码流无损封装录制 MKV；可选单路音频监听（使用 FFplay）
- 全屏 F11/F、追帧 L、截图 S、录制 R、数字 1–9 选焦点窗口、Ctrl+O / Ctrl+S 导入导出

## 快速运行（Windows 10/11）

1. 安装 [Python](https://www.python.org/downloads/) 3.10+ 并勾选加入 PATH。
2. 安装 FFmpeg Windows 构建；将包含 `ffmpeg.exe`、`ffplay.exe` 的 `bin` 目录加入 PATH。也可设置环境变量 `MULTILIVE_FFMPEG_DIR` 指向该 `bin` 目录。
3. 双击 `start_windows.bat` 或在项目目录运行：

```powershell
python -m pip install -r requirements.txt
python main.py
```

4. 添加可访问的视频源，点击左侧源的 `▶` 让它进入当前选中的画面。

**示范测试信号**：输入 `lavfi:testsrc=size=640x360:rate=30` 可验证解码/渲染。它是 FFmpeg 的虚拟测试源，不是 APK 功能。

## 追帧与性能说明

- **极限**：较小探测缓冲 + 8 秒无帧看门狗 + 较短重连退避；可能牺牲弱网容错。
- **均衡**：增大探测范围，12 秒无帧重启。
- **稳定**：更加保守的探测参数，20 秒无帧重启。
- **不积压**：每个后台解码器维护 `Frame | None` 单帧槽位，显示端只取最新序号。慢 UI 不会排队显示旧帧。
- **低 CPU**：隐藏窗口停解码；UI 独立设置刷新率；FFmpeg 用 bilinear 小尺寸缩放，工作进程从不操纵 Tk。
- **实时性边界**：本地帧龄 ≠ 端到端直播延迟。CDN 缓存、HLS 分片长度、服务器 DVR、音视频同步等不受桌面客户端控制；“追直播”并不能保证绝对零延迟或精确跳过服务器缓存。
- **录制**：使用独立 FFmpeg 实例 `-c copy` 无重新编码封装 MKV（可能对某些流/服务不兼容）。
- **音频**：可选 FFplay 独立拉流，**不能保证与画面精确同步**。如需多路精准音视频同步或 HLS 低延迟规范、平台 API 集成，须在获取目标 APK 后补充。
- **系统资源**：9 路 1080p 视频同时解码依然较吃 CPU/GPU/网络；建议减小路数、关闭不必要的音频监听，硬解功能按显卡与 FFmpeg 版本测试。

## 配置与隐私

- Windows 默认 `%APPDATA%\MultiLiveDesktop\settings.json`，Linux 则 `~/.config/multilive-desktop/settings.json`。
- 配置包含原始播放 URL；带账号/令牌的 URL 会原样持久化。列表界面与解码错误尽量隐藏授权信息，但**导出 JSON 包含完整 URL**，请妥善保管。
- 可以用 `MULTILIVE_CONFIG_DIR` 改变配置目录；`MULTILIVE_FFMPEG_DIR` 指定 FFmpeg 所在目录。
- 全程无云服务、无遥测、无隐藏网络请求；仅拉取使用者主动添加的源。

## 验证

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

运行 GUI 需图形化桌面环境；在无显示器的 CI 中可用 xvfb-run 执行 GUI 冒烟测试。不可用的协议是否成功播放取决于 FFmpeg 二进制构建和授权/网络条件。测试仅验证本桌面工程，不代表 APK 功能一致性。

## 本地 APK 静态审计（用于补齐原版功能）

在能访问到 APK 的 Windows 电脑上运行：

```powershell
python analyze_apk.py "C:\Users\Mr_xi\.dsh\attachments\v1\files\30\308fe094049bdf00fb91f4055a4524fb5faa7f7d3d63c50d3d1322e8fffa2b25\MultiLive-android-v10.5.4.apk" --out apk_report.json
```

脚本**只读** ZIP/Manifest/DEX，不运行 APK、不解压可执行代码、不修改原文件；生成的 JSON 包含权限、组件、DEX 关键字符串、资源索引及 URL 域名（隐藏账号与参数）。复杂混淆、加密、原生代码、动态加载和真正的业务行为仍需进一步人工核查。

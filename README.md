# Phone2PC (智连)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-v5.7-green)

A lightweight, high-performance tool for seamless collaboration between Android and PC. Connect your phone to your computer via local Wi-Fi to sync clipboards, transfer files, and use your phone as a remote text input device.

**[English](#english) | [简体中文](#简体中文)**

---

<a name="english"></a>
## 🇬🇧 English

### ✨ Features

*   **⚡ Zero-Latency Connection**: Uses WebSocket for real-time bi-directional communication over local LAN.
    *   First-time devices must enter the six-digit pairing code shown on the PC.
    *   Paired devices receive a device-bound token for later connections.
*   **📋 Clipboard Sync**: 
    *   **Foreground**: Automatically syncs when app is open.
    *   **Background**: PC updates are announced with a privacy-safe copy notification.
*   **📂 High-Speed File Transfer**:
    *   Supports binary transfer protocol.
    *   Bounded backpressure, queued batch transfer, SHA-256 verification, and atomic save.
    *   Batch transfer.
*   **⌨️ Remote Input**: Type on your phone's keyboard and send text directly to your PC's active window.
*   **🛡️ Persistent Connection**: An Android foreground service keeps the session alive in the background and reconnects automatically until you explicitly disconnect.
*   **🖥️ Single PC Instance**: Reopening the PC executable restores its tray window instead of starting a second conflicting server.

### 🚀 Getting Started

#### 1. Requirements
*   **PC**: Windows 10/11.
*   **Phone**: Android 8.0+.
*   **Network**: Both devices must be on the same Wi-Fi network.

#### 2. Installation
*   **PC**: Download and run `phone2pc.exe` (No installation required).
*   **Android**: Install `phone2pc.apk` on your phone.

#### 3. Usage
1.  Run `phone2pc.exe` on your computer. Note the IP address displayed (or let the app auto-detect).
2.  Open the **Phone2PC** app on Android.
3.  Enter your PC's IP address and the pairing code shown by the PC, then tap **Connect**.
4.  Once you see "Connected", you are ready to go!
    *   **Text**: Type in the text box and hit Send (or enable "Enter to Send").
    *   **File**: Go to the "File" tab to select and send files.
    *   **Clipboard**: 
        *   Keep app in foreground for auto-sync.
        *   PC clipboard updates appear as a privacy-safe notification with a copy action.

### 🛠️ Development

Built with:
*   **PC Side**: Python 3.13, Tkinter, `websockets`, PyInstaller.
*   **Android Side**: Flutter 3.38.3, Dart 3.10, `web_socket_channel`.
*   **Protocol**: v6. Both sides must be upgraded together.

---

<a name="简体中文"></a>
## 🇨🇳 简体中文

### ✨ 主要功能

*   **⚡ 极速连接**: 基于 WebSocket 的局域网实时双向通信，无需联网，安全快速。
    *   新设备首次连接必须输入 PC 窗口显示的 6 位配对码。
    *   配对成功后保存设备专属令牌，后续连接无需重复输入。
*   **📋 剪贴板同步**:
    *   **前台自动**: APP 处于前台时自动同步。
    *   **后台通知**: PC 剪贴板更新通过不展示原文的复制通知提示。
*   **📂 高速文件传输**:
    *   参考 LocalSend，优先使用带一次性令牌的 HTTP 原始流，无需 Base64 或 WebSocket 消息封装。
    *   Android 发送由原生后台线程执行；快速通道不可用时自动回退到 WebSocket。
    *   采用有界背压、串行队列、SHA-256 校验和原子落盘，避免大文件堆积与批量文件交叉损坏。
    *   支持批量发送。
*   **⌨️ 远程输入**: 将手机作为电脑的无线键盘，直接将文字输入到电脑当前活动窗口。
*   **🛡️ 后台常驻连接**: 连接后通过 Android 前台服务保持会话；临时断网会自动退避重连，只有主动断开才停止常驻。
*   **🖥️ PC 单实例运行**: 重复打开程序时直接恢复已有托盘窗口，避免多个进程争抢连接端口。


### 🚀 使用指南

#### 1. 环境准备
*   **电脑**: Windows 10 或 11。
*   **手机**: 安卓 8.0 及以上系统。
*   **网络**: 直连局域网（电脑和手机需连接同一个 Wi-Fi）。

#### 2. 安装说明
*   **电脑端**: 下载 `phone2pc.exe` 直接运行即可（绿色免安装）。
*   **安卓端**: 下载并安装 `phone2pc.apk`。

#### 3. 操作步骤
1.  在电脑上运行 `phone2pc.exe`，允许防火墙访问。
2.  打开手机 APP，输入电脑显示的 IP 地址和首次连接配对码（APP 会记录历史 IP 和设备令牌）。
3.  点击 **连接**。
4.  当状态栏显示“已连接”时：
    *   **输入**: 在输入框打字，电脑端即刻响应。
    *   **文件**: 切换到“文件”标签页，选择照片或文件发送。
    *   **剪贴板**:
        *   APP 保持前台可自动同步。
        *   收到 PC 剪贴板后，可通过不展示原文的通知按钮复制。

### 🛠️ 开发构建

技术栈：
*   **PC 服务端**: Python 3.13, Tkinter (界面), `websockets` (核心通信).
*   **Android 客户端**: Flutter 3.38.3, Dart 3.10.
*   **通信协议**: v6，PC 与 Android 必须同时升级。

**自行构建**:
```powershell
# PC（在仓库根目录）
py -3.13 -m venv .venv
.\.venv\Scripts\pip install -r pc_server\requirements-dev.txt
.\.venv\Scripts\pyinstaller phone2pc.spec

# Android（需安装 FVM，在 android_app 目录）
fvm install 3.38.3
fvm flutter pub get
fvm flutter test
fvm flutter build apk
```

Android 正式发布前，请在 `android/key.properties` 配置独立签名密钥；未配置时只生成未签名的 release 产物。Android 接收文件保存在公共 `Download/Phone2PC` 目录；PC 接收文件保存在当前用户的 `Downloads/Phone2PC`。

v5.4 将 Android Application ID 从模板值改为 `io.github.oooocoooo1.phone2pc`。旧测试包无法原地升级，需要先备份所需记录并卸载旧包。

> 安全说明：v5.4 会鉴权设备、限制来源和消息大小，HTTP 文件流还使用每个文件独立的随机令牌，但局域网传输仍是明文。请勿将 WebSocket 端口 8765 或 HTTP 快速通道端口 8766 映射到公网；在不可信 Wi-Fi 上请配合可信 VPN 使用。

---
© 2025-2026 Phone2PC Project.

# Phone2PC (智连)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-v5.3-green)

A lightweight, high-performance tool for seamless collaboration between Android and PC. Connect your phone to your computer via local Wi-Fi to sync clipboards, transfer files, and use your phone as a remote text input device.

**[English](#english) | [简体中文](#简体中文)**

---

<a name="english"></a>
## 🇬🇧 English

### ✨ Features

*   **⚡ Zero-Latency Connection**: Uses WebSocket for real-time bi-directional communication over local LAN.
*   **📋 Clipboard Sync**: 
    *   **Foreground**: Automatically syncs when app is open.
    *   **Background**: Sync via notification "Send to PC" button (Android 10+ restriction compliant).
*   **📂 High-Speed File Transfer**:
    *   Supports binary transfer protocol.
    *   Smart flow control for stability on any network.
    *   Batch transfer.
*   **⌨️ Remote Input**: Type on your phone's keyboard and send text directly to your PC's active window.

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
3.  Enter your PC's IP address and tap **Connect**.
4.  Once you see "Connected", you are ready to go!
    *   **Text**: Type in the text box and hit Send (or enable "Enter to Send").
    *   **File**: Go to the "File" tab to select and send files.
    *   **Clipboard**: 
        *   Keep app in foreground for auto-sync.

### 🛠️ Development

Built with:
*   **PC Side**: Python 3.13, Tkinter (UI), `websockets` (Server), `pyinstaller` (Build).
*   **Android Side**: Flutter, Dart, `web_socket_channel`.

---

<a name="简体中文"></a>
## 🇨🇳 简体中文

### ✨ 主要功能

*   **⚡ 极速连接**: 基于 WebSocket 的局域网实时双向通信，无需联网，安全快速。
*   **📋 剪贴板同步**:
    *   **前台自动**: APP 处于前台时自动同步。
    *   **后台手动**: 通过通知栏“发送到电脑”按钮同步（符合安卓隐私规范）。
*   **📂 高速文件传输**:
    *   采用二进制传输协议，无需 Base64 转码，效率更高。
    *   智能流控机制，告别大文件传输卡顿。
    *   支持批量发送。
*   **⌨️ 远程输入**: 将手机作为电脑的无线键盘，直接将文字输入到电脑当前活动窗口。


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
2.  打开手机 APP，输入电脑显示的 IP 地址（APP 会记录历史 IP）。
3.  点击 **连接**。
4.  当状态栏显示“已连接”时：
    *   **输入**: 在输入框打字，电脑端即刻响应。
    *   **文件**: 切换到“文件”标签页，选择照片或文件发送。
    *   **剪贴板**:
        *   APP 保持前台可自动同步。

### 🛠️ 开发构建

技术栈：
*   **PC 服务端**: Python 3.13, Tkinter (界面), `websockets` (核心通信).
*   **Android 客户端**: Flutter 3.x, Dart.

**自行构建**:
```bash
# PC (在 pc_server 目录)
pyinstaller -F -w --name phone2pc --icon=pc_server/icon.ico --add-data "pc_server/icon.ico;pc_server" pc_server/main.py --hidden-import windnd

# Android (在 android_app 目录)
flutter build apk
```

---
© 2025-2026 Phone2PC Project.

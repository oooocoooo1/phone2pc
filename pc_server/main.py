import asyncio
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import socket
import logging
from logging.handlers import RotatingFileHandler
import sys
import os
import winreg
from PIL import Image, ImageDraw
import pystray
import json
import secrets
import time
import queue
import ctypes
from collections import defaultdict, deque

from server import WebSocketServer
from input_handler import InputHandler
from clipboard_manager import ClipboardManager
from file_manager import FileManager
from constants import APP_VERSION, MAX_CLIPBOARD_SIZE, MAX_REMOTE_INPUT_SIZE, PROTOCOL_VERSION, SERVER_PORT
from security import PairingManager, get_app_data_dir
from windows_drop import WindowsDropTarget
from tkinter import filedialog

_instance_mutex = None
_instance_mutex_name = "Local\\Phone2PC-io.github.oooocoooo1"
_window_title_prefix = "Phone2PC 智连"


def _activate_existing_window():
    """Restore the already-running hidden tray window."""
    if os.name != "nt":
        return False
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_window(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if title.value.startswith(_window_title_prefix):
            user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(enum_window, 0)
    return bool(found)


def _claim_single_instance(activate_existing=True):
    """Return False when another Phone2PC process already owns the mutex."""
    global _instance_mutex
    if os.name != "nt":
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, _instance_mutex_name)
    if not handle:
        return True
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        if activate_existing:
            _activate_existing_window()
        return False
    _instance_mutex = handle
    return True


def _release_single_instance():
    global _instance_mutex
    if _instance_mutex and os.name == "nt":
        ctypes.windll.kernel32.CloseHandle(_instance_mutex)
    _instance_mutex = None

class TextHandler(logging.Handler):
    """用于将日志输出到 Tkinter 文本框"""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        if not self.text_widget: return
        msg = self.format(record)
        def append():
            try:
                self.text_widget.configure(state='normal')
                self.text_widget.insert(tk.END, msg + '\n')
                self.text_widget.see(tk.END)
                self.text_widget.configure(state='disabled')
            except tk.TclError:
                pass
        try:
            self.text_widget.after(0, append)
        except tk.TclError:
            pass

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEI
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    return os.path.join(base_path, relative_path)

class AppGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Phone2PC 智连 (v{APP_VERSION})")
        # Leave enough room for the pairing controls and transfer history at
        # common Windows display scaling levels.  Keep a practical minimum so
        # resizing the window cannot hide the reset-device button again.
        self.root.geometry("720x720")
        self.root.minsize(680, 640)
        
        # 设置窗口图标 (Runtime)
        try:
            icon_path = resource_path("pc_server/icon.ico")
            if not os.path.exists(icon_path):
                 # Try local dev path if not in bundled subfolder
                 icon_path = resource_path("icon.ico")
            
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
            else:
                 # Fallback: check if in current dir directly (dev)
                 if os.path.exists("pc_server/icon.ico"):
                     self.root.iconbitmap("pc_server/icon.ico")
        except Exception as e:
            logging.error(f"Failed to set icon: {e}")
        
        self.loop = None
        self.server = None
        self.input_handler = None
        self.clipboard_manager = None
        self.file_manager = None
        self.server_thread = None
        self.tray_icon = None
        self.connected_websocket = None 
        self.authenticated_clients = set()
        self.auth_challenges = {}
        self.auth_failures = defaultdict(deque)
        self.pairing_manager = PairingManager()
        
        self.is_closing = False
        self._drop_queue = queue.SimpleQueue()
        self._drop_target = None
        self._drop_poll_after_id = None

        self._init_ui()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_click)
        
        # 分步启动
        self.root.after(200, self._init_autorun_state)
        self.root.after(500, self._start_ip_check)
        self.root.after(700, self._start_clipboard)
        self.root.after(900, self._init_file_manager)
        self.root.after(1200, self._start_server_safe)
        self.root.after(1800, self._init_tray_safe)

    def _init_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: 主页
        self.tab_home = tk.Frame(self.notebook)
        self.notebook.add(self.tab_home, text="  主页  ")
        self._init_home_tab(self.tab_home)

        # Tab 2: 云剪贴板
        self.tab_clipboard = tk.Frame(self.notebook)
        self.notebook.add(self.tab_clipboard, text="  云剪贴板  ")
        self._init_clipboard_tab(self.tab_clipboard)
        
        # Tab 3: 文件传输
        self.tab_files = tk.Frame(self.notebook)
        self.notebook.add(self.tab_files, text="  文件传输  ")
        self._init_file_tab(self.tab_files)

    def _init_home_tab(self, parent):
        # 顶部框架 (IP & Checkbox)
        top_frame = tk.Frame(parent, pady=15)
        top_frame.pack(fill=tk.X, padx=15)
        
        # IP 显示
        tk.Label(top_frame, text="本机 IP:", font=("Arial", 11, "bold")).pack(side=tk.LEFT)
        self.ip_entry = tk.Entry(top_frame, font=("Arial", 11), width=15, fg="blue")
        self.ip_entry.pack(side=tk.LEFT, padx=5)
        self.ip_entry.insert(0, "正在检测...")
        self.ip_entry.configure(state='readonly')

        # 开机自启 Checkbox
        # 初始设为 False，稍后异步更新，避免阻塞 UI
        self.autorun_var = tk.BooleanVar(value=False)
        cb_autorun = tk.Checkbutton(top_frame, text="开机自启", variable=self.autorun_var, command=self._toggle_autorun)
        cb_autorun.pack(side=tk.RIGHT)

        pairing_frame = tk.Frame(parent)
        pairing_frame.pack(fill=tk.X, padx=15, pady=(0, 8))
        tk.Label(pairing_frame, text="配对码:", font=("Arial", 11, "bold")).pack(side=tk.LEFT)
        self.pairing_code_var = tk.StringVar(value=self.pairing_manager.pairing_code)
        tk.Label(
            pairing_frame,
            textvariable=self.pairing_code_var,
            font=("Consolas", 15, "bold"),
            fg="#D84315",
        ).pack(side=tk.LEFT, padx=8)
        tk.Label(pairing_frame, text="首次连接时在手机输入", fg="gray").pack(side=tk.LEFT)
        tk.Button(
            pairing_frame,
            text="重置设备",
            command=self._reset_pairing,
            width=10,
        ).pack(side=tk.RIGHT, padx=(10, 0))

        # 日志区域
        log_frame = tk.LabelFrame(parent, text="运行日志", padx=5, pady=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, state='disabled', height=10)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self._setup_logging()
        
        tk.Label(parent, text=f"v{APP_VERSION} | 已鉴权 · 校验传输", fg="gray").pack(pady=5)

    def _init_clipboard_tab(self, parent):
        # 左右分栏：左边本机历史，右边手机历史
        paned = tk.PanedWindow(parent, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 左栏：PC 剪贴板
        left_frame = tk.LabelFrame(paned, text="PC 剪贴板历史 (点击复制)")
        paned.add(left_frame, minsize=200)
        
        self.list_pc = tk.Listbox(left_frame, selectmode=tk.SINGLE)
        self.list_pc.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.list_pc.bind("<<ListboxSelect>>", self._on_pc_list_click)
        
        btn_clear_pc = tk.Button(left_frame, text="清空列表", command=lambda: self._clear_list("pc"))
        btn_clear_pc.pack(fill=tk.X, padx=5, pady=2)

        # 右栏：手机 剪贴板
        right_frame = tk.LabelFrame(paned, text="手机 剪贴板历史 (点击复制)")
        paned.add(right_frame, minsize=200)

        self.list_phone = tk.Listbox(right_frame, selectmode=tk.SINGLE)
        self.list_phone.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.list_phone.bind("<<ListboxSelect>>", self._on_phone_list_click)

        btn_clear_phone = tk.Button(right_frame, text="清空列表", command=lambda: self._clear_list("phone"))
        btn_clear_phone.pack(fill=tk.X, padx=5, pady=2)

    def _init_file_tab(self, parent):
        # 顶部提示
        lbl_hint = tk.Label(parent, text="支持拖拽文件到此窗口直接发送", fg="gray", pady=10)
        lbl_hint.pack()

        # 发送按钮
        btn_send = tk.Button(parent, text="选择文件发送", command=self._select_file_to_send, bg="#E1F5FE", height=2)
        btn_send.pack(fill=tk.X, padx=20, pady=5)

        progress_frame = tk.LabelFrame(parent, text="实时传输状态", padx=8, pady=6)
        progress_frame.pack(fill=tk.X, padx=10, pady=5)
        self.file_progress_ui = {}
        self._create_file_progress_row(progress_frame, "send", "发送")
        self._create_file_progress_row(progress_frame, "receive", "接收")

        # 接收记录
        tk.Label(parent, text="队列传输 · 背压 · SHA-256 校验", fg="gray").pack(fill=tk.X, padx=10, pady=5)
        
        self.list_files = tk.Listbox(parent, selectmode=tk.SINGLE, height=16)
        self.list_files.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.list_files.bind("<Double-Button-1>", self._on_file_list_double_click)
        
        btn_open_dir = tk.Button(parent, text="打开接收文件夹", command=self._open_recv_dir)
        btn_open_dir.pack(fill=tk.X, padx=10, pady=5)

    def _init_file_manager(self):
        download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "Phone2PC")
        self.file_manager = FileManager(
            save_dir=download_dir,
            send_callback=self._send_raw_json,
            on_receive_complete=self._on_file_received,
            on_send_complete=self._on_file_sent_success,
            on_send_failed=self._on_file_send_failed,
            on_transfer_progress=self._on_file_transfer_progress,
            peer_host_callback=self._get_connected_peer_host,
        )
        # Hook Drag & Drop
        try:
            self._drop_target = WindowsDropTarget(self.root, self._on_drop_files)
            self._schedule_drop_poll()
            logging.info("文件拖拽功能已启用")
        except Exception as e:
            logging.error(f"拖拽初始化失败: {e}")

    def _on_drop_files(self, filenames):
        """Native drop callback: enqueue only; never enter Tcl/Tk here."""
        try:
            paths = tuple(
                item.decode("mbcs", errors="replace") if isinstance(item, bytes) else str(item)
                for item in (filenames or ())
            )
            if paths:
                self._drop_queue.put(paths)
        except BaseException:
            # Exceptions escaping a ctypes WNDPROC callback can terminate the process.
            logging.exception("接收拖拽文件失败")

    def _schedule_drop_poll(self):
        if not self.is_closing:
            self._drop_poll_after_id = self.root.after(40, self._poll_drop_queue)

    def _poll_drop_queue(self):
        self._drop_poll_after_id = None
        if self.is_closing:
            return
        while True:
            try:
                filenames = self._drop_queue.get_nowait()
            except queue.Empty:
                break
            self._process_dropped_files(filenames)
        self._schedule_drop_poll()

    def _process_dropped_files(self, filenames):
        """Handle dropped files safely from Tk's normal event loop."""
        if self.is_closing or not filenames:
            return
        try:
            if not self.connected_websocket:
                logging.info("已忽略拖拽文件：手机尚未连接")
                self.notebook.select(self.tab_files)
                self._log_file_ui("未发送：手机尚未连接，请先完成配对并连接")
                self.root.bell()
                return
            if not self.file_manager:
                self._log_file_ui("未发送：文件传输服务尚未就绪")
                return

            for filepath in filenames:
                self._log_file_ui(f"准备发送: {os.path.basename(filepath)}")
                self.file_manager.send_file_thread(filepath)
        except Exception:
            logging.exception("处理拖拽文件失败")
            try:
                self._log_file_ui("拖拽文件处理失败，请查看运行日志")
            except tk.TclError:
                pass

    def _select_file_to_send(self):
        if not self.connected_websocket:
            messagebox.showwarning("未连接", "请先完成手机配对并连接")
            return
        files = filedialog.askopenfilenames()
        if files:
            for f in files:
                self._log_file_ui(f"准备发送: {os.path.basename(f)}")
                self.file_manager.send_file_thread(f)

    def _send_raw_json(self, json_str, wait=False):
        """文件管理器使用的底层发送回调"""
        if not self.connected_websocket or not self.loop or not self.loop.is_running():
            raise RuntimeError("没有已鉴权的连接")
        future = asyncio.run_coroutine_threadsafe(self.connected_websocket.send(json_str), self.loop)
        if wait:
            return future.result(timeout=30)
        return future

    def _get_connected_peer_host(self):
        websocket = self.connected_websocket
        if not websocket or not websocket.remote_address:
            return None
        return websocket.remote_address[0]

    def _on_file_received(self, filepath):
        self.root.after(0, lambda: self._log_file_ui(f"已接收: {os.path.basename(filepath)} (双击打开)", filepath))

    def _on_file_sent_success(self, filename):
        self.root.after(0, lambda: messagebox.showinfo("发送成功", f"文件 '{filename}' 已成功发送给客户端"))

    def _on_file_send_failed(self, filename, reason):
        self.root.after(0, lambda: self._log_file_ui(f"发送失败: {filename} ({reason})"))

    def _create_file_progress_row(self, parent, direction, title):
        frame = tk.Frame(parent)
        frame.pack(fill=tk.X, pady=3)

        title_var = tk.StringVar(value=f"{title}：空闲")
        detail_var = tk.StringVar(value="0 B / 0 B · --")
        progress_var = tk.DoubleVar(value=0.0)

        tk.Label(frame, textvariable=title_var, anchor="w").pack(fill=tk.X)
        ttk.Progressbar(frame, variable=progress_var, maximum=100).pack(fill=tk.X, pady=(2, 1))
        tk.Label(frame, textvariable=detail_var, anchor="w", fg="gray").pack(fill=tk.X)
        self.file_progress_ui[direction] = {
            "title": title_var,
            "detail": detail_var,
            "progress": progress_var,
            "label": title,
        }

    def _on_file_transfer_progress(self, event):
        snapshot = dict(event)
        self.root.after(0, lambda: self._update_file_transfer_progress(snapshot))

    def _update_file_transfer_progress(self, event):
        ui = self.file_progress_ui.get(event.get("direction"))
        if not ui:
            return

        status_labels = {
            "waiting": "等待接收端",
            "sending": "发送中",
            "receiving": "接收中",
            "verifying": "校验中",
            "completed": "已完成",
            "failed": "失败",
            "cancelled": "已取消",
        }
        status = event.get("status", "")
        transferred = max(0, int(event.get("transferred", 0)))
        total = max(0, int(event.get("total", 0)))
        percent = 100.0 if status == "completed" and total == 0 else (transferred * 100.0 / total if total else 0.0)
        percent = min(100.0, max(0.0, percent))
        speed = max(0.0, float(event.get("speed_bps", 0.0)))
        speed_text = f"{self._format_file_size(speed)}/s" if speed > 0 else "--"
        name = event.get("name") or "未命名文件"
        status_text = status_labels.get(status, status or "空闲")
        message = event.get("message")

        ui["title"].set(f"{ui['label']}：{status_text} · {name}")
        detail = (
            f"{self._format_file_size(transferred)} / {self._format_file_size(total)}"
            f" · {percent:.1f}% · {speed_text}"
        )
        if message:
            detail += f" · {message}"
        if event.get("transport") == "http":
            detail += " · HTTP 高速"
        ui["detail"].set(detail)
        ui["progress"].set(percent)

    @staticmethod
    def _format_file_size(byte_count):
        value = float(byte_count)
        units = ("B", "KB", "MB", "GB", "TB")
        unit = units[0]
        for unit in units:
            if value < 1024 or unit == units[-1]:
                break
            value /= 1024
        if unit == "B":
            return f"{int(value)} {unit}"
        return f"{value:.1f} {unit}"

    def _log_file_ui(self, msg, filepath=None):
        self.list_files.insert(0, msg)
        if filepath:
            # 存储 filepath 以便双击打开，简单起见存个 map?
            # 简化：只用 log。打开需去文件夹。
            # 或者：tag? Listbox 没有 data payload.
            pass

    def _on_file_list_double_click(self, event):
        # 简单实现：双击若包含文件名，尝试去文件夹找
        # 或者直接打开文件夹
        self._open_recv_dir()

    def _open_recv_dir(self):
        if self.file_manager:
            path = os.path.abspath(self.file_manager.save_dir)
            os.startfile(path)

    # ... (Keep existing methods: _init_autorun_state, _start_ip_check, etc.) ...
    
    # Updated message handler
    async def _handle_client_message(self, message, websocket):
        if websocket not in self.authenticated_clients:
            await self._handle_auth_message(message, websocket)
            return

        # Binary Frame -> FileManager directly
        if isinstance(message, bytes):
            if self.file_manager:
                await asyncio.to_thread(self.file_manager.handle_binary, message)
            return
        
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            # 路由：剪贴板消息
            if msg_type == "CLIPBOARD_SYNC":
                content = data.get("content", "")
                if isinstance(content, str) and 0 < len(content.encode("utf-8")) <= MAX_CLIPBOARD_SIZE:
                    await asyncio.to_thread(self._apply_phone_clipboard, content)
                    self.root.after(0, lambda: self._update_list("phone"))
                    logging.info("收到手机剪贴板同步，已写入本机剪贴板")
                elif content:
                    await websocket.send(json.dumps({"type": "ERROR", "message": "剪贴板内容超出限制"}))
                return
            
            # 路由：文件消息 (包括 ACK)
            if msg_type in ["FILE_OFFER", "FILE_END", "FILE_ACCEPT", "FILE_ACK", "FILE_COMPLETE", "FILE_ERROR", "ACK"]:
                if self.file_manager:
                    await asyncio.to_thread(self.file_manager.handle_message, data)
                return
            if msg_type is not None:
                await websocket.send(json.dumps({"type": "ERROR", "message": "未知的协议消息"}))
                return
        except json.JSONDecodeError:
            pass

        # 默认作为文本输入处理
        if len(message.encode("utf-8")) > MAX_REMOTE_INPUT_SIZE:
            await websocket.send(json.dumps({"type": "ERROR", "message": "远程输入内容超出限制"}))
            return
        if not self.input_handler.submit_text(message):
            await websocket.send(json.dumps({"type": "ERROR", "message": "远程输入队列已满"}))

    def _apply_phone_clipboard(self, content):
        self.clipboard_manager.add_phone_history(content)
        self.clipboard_manager.set_clipboard(content)

    async def _handle_auth_message(self, message, websocket):
        peer_ip = websocket.remote_address[0] if websocket.remote_address else "unknown"
        failures = self.auth_failures[peer_ip]
        now = time.monotonic()
        while failures and now - failures[0] > 60:
            failures.popleft()
        if len(failures) >= 5:
            await websocket.close(code=1008, reason="too many authentication attempts")
            return
        if not isinstance(message, str):
            await websocket.close(code=1008, reason="authentication required")
            return
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            await websocket.close(code=1008, reason="authentication required")
            return

        msg_type = data.get("type")
        device_id = data.get("device_id")
        token = None
        paired = False
        if msg_type == "AUTH":
            challenge = self.auth_challenges.get(websocket)
            if self.pairing_manager.authenticate_challenge(device_id, data.get("response"), challenge):
                token = True
        elif msg_type == "PAIR":
            token = self.pairing_manager.pair(device_id, data.get("code"))
            paired = token is not None

        if token is None:
            failures.append(time.monotonic())
            await websocket.send(json.dumps({"type": "AUTH_ERROR", "message": "配对码或设备令牌无效"}))
            await asyncio.sleep(0.3)
            await websocket.close(code=1008, reason="authentication failed")
            return

        previous = self.connected_websocket
        if previous and previous != websocket:
            await previous.close(code=1000, reason="replaced by a new authenticated connection")

        self.authenticated_clients.add(websocket)
        self.auth_failures.pop(peer_ip, None)
        self.auth_challenges.pop(websocket, None)
        self.connected_websocket = websocket
        response = {
            "type": "PAIR_SUCCESS" if paired else "AUTH_OK",
            "version": APP_VERSION,
            "protocol": PROTOCOL_VERSION,
        }
        if paired:
            response["token"] = token
            self.root.after(0, lambda: self.pairing_code_var.set(self.pairing_manager.pairing_code))
        await websocket.send(json.dumps(response))
        logging.info("设备已通过鉴权: %s", websocket.remote_address)

        pc_history = self.clipboard_manager.get_history("pc") if self.clipboard_manager else []
        if pc_history:
            latest = pc_history[0]
            if latest and len(latest.encode("utf-8")) <= MAX_CLIPBOARD_SIZE:
                await websocket.send(json.dumps({"type": "CLIPBOARD_SYNC", "source": "PC", "content": latest}))

    def _init_autorun_state(self):
        try:
            is_auto = self._check_autorun()
            self.autorun_var.set(is_auto)
            if is_auto:
                self._ensure_autorun_command()
        except Exception as e:
            logging.error(f"注册表读取失败: {e}")

    def _start_ip_check(self):
        threading.Thread(target=self._update_ip_display, daemon=True).start()

    def _start_server_safe(self):
        try: 
            self._start_server()
        except Exception as e:
            logging.error(f"Server启动失败: {e}")

    def _start_clipboard(self):
        try:
            self._start_clipboard_manager()
        except Exception as e:
            logging.error(f"剪贴板服务启动失败: {e}")

    def _init_tray_safe(self):
        try:
            self._init_tray()
            logging.info("托盘图标已加载")
        except Exception as e:
            logging.error(f"托盘启动失败: {e}")
            # Do not leave the application permanently invisible when tray
            # integration isn't available.
            self.root.deiconify()

    def _init_tray(self):
        try:
            # 尝试加载应用图标
            icon_path = resource_path("pc_server/icon.ico")
            if not os.path.exists(icon_path):
                 icon_path = resource_path("icon.ico")
            if not os.path.exists(icon_path) and os.path.exists("pc_server/icon.ico"):
                 icon_path = "pc_server/icon.ico"
            
            image = Image.open(icon_path)
        except Exception:
            # 加载失败则绘制默认图标
            width = 64
            height = 64
            image = Image.new('RGB', (width, height), color=(73, 109, 137))
            dc = ImageDraw.Draw(image)
            dc.rectangle([16, 16, 48, 48], fill='white')
        
        # 将左键点击绑定到显示窗口
        menu = (pystray.MenuItem('显示窗口', self._show_window, default=True), pystray.MenuItem('退出', self._quit_app))
        self.tray_icon = pystray.Icon("phone2pc", image, "Phone2PC 服务端", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _show_window(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)

    def _on_close_click(self):
        self.root.withdraw()

    def _reset_pairing(self):
        if not messagebox.askyesno("重置已配对设备", "所有手机都需要重新输入配对码，是否继续？"):
            return
        self.pairing_manager.reset_trusted_devices()
        self.pairing_code_var.set(self.pairing_manager.pairing_code)
        if self.connected_websocket and self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.connected_websocket.close(code=1000, reason="trusted devices reset"), self.loop
            )
        logging.info("已重置所有受信任设备")

    def _quit_app(self, icon=None, item=None):
        self.is_closing = True
        if self.tray_icon: self.tray_icon.stop()
        self.root.after(0, self._destroy_app)

    def _destroy_app(self):
        if self._drop_poll_after_id is not None:
            try:
                self.root.after_cancel(self._drop_poll_after_id)
            except tk.TclError:
                pass
            self._drop_poll_after_id = None
        if self._drop_target:
            self._drop_target.close()
            self._drop_target = None
        if self.clipboard_manager: self.clipboard_manager.stop()
        if self.input_handler: self.input_handler.stop()
        if self.file_manager: self.file_manager.stop()
        if self.loop and self.loop.is_running() and self.server:
            try:
                asyncio.run_coroutine_threadsafe(self.server.stop(), self.loop).result(timeout=3)
            except Exception as e:
                logging.warning(f"关闭服务时出现异常: {e}")
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=3)
        self.root.destroy()

    def _setup_logging(self):
        handler = TextHandler(self.log_text)
        formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S')
        handler.setFormatter(formatter)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            log_dir = get_app_data_dir() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_dir / "phone2pc.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            root_logger.addHandler(file_handler)
        except OSError:
            pass
        root_logger.setLevel(logging.INFO)

    def _check_autorun(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, "Phone2PC")
            return True
        except OSError:
            return False

    @staticmethod
    def _autorun_command():
        if getattr(sys, 'frozen', False):
            return f'"{sys.executable}" --minimized'
        return f'"{sys.executable}" "{os.path.abspath(__file__)}" --minimized'

    def _ensure_autorun_command(self):
        expected = self._autorun_command()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as key:
            current, _ = winreg.QueryValueEx(key, "Phone2PC")
            if current != expected:
                winreg.SetValueEx(key, "Phone2PC", 0, winreg.REG_SZ, expected)
                logging.info("已更新开机自启配置，将自动进入托盘")

    def _toggle_autorun(self):
        target = self._autorun_command()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
                if self.autorun_var.get():
                    winreg.SetValueEx(key, "Phone2PC", 0, winreg.REG_SZ, target)
                    logging.info("已开启开机自启")
                else:
                    try:
                        winreg.DeleteValue(key, "Phone2PC")
                    except FileNotFoundError:
                        pass
                    logging.info("已关闭开机自启")
        except Exception as e:
            logging.error(f"设置开机自启失败: {e}")
            self.autorun_var.set(not self.autorun_var.get())

    def _start_clipboard_manager(self):
        self.clipboard_manager = ClipboardManager(on_clipboard_change=self._on_pc_clipboard_change)
        self.clipboard_manager.start()
        logging.info("云剪贴板服务已启动")

    def _on_pc_clipboard_change(self, text):
        # PC 剪贴板变化 -> 更新 UI -> 发送给手机
        self.root.after(0, lambda: self._update_list("pc"))
        if self.connected_websocket and len(text.encode("utf-8")) <= MAX_CLIPBOARD_SIZE:
            msg = json.dumps({"type": "CLIPBOARD_SYNC", "source": "PC", "content": text})
            asyncio.run_coroutine_threadsafe(self.connected_websocket.send(msg), self.loop)
        elif len(text.encode("utf-8")) > MAX_CLIPBOARD_SIZE:
            logging.warning("剪贴板内容超过 256 KiB，已跳过同步")

    def _update_list(self, type_):
        if type_ == "pc":
            data = self.clipboard_manager.get_history("pc")
            lb = self.list_pc
        else:
            data = self.clipboard_manager.get_history("phone")
            lb = self.list_phone
        
        lb.delete(0, tk.END)
        for item in data:
            display_text = item.replace('\n', ' ')[:30] + ('...' if len(item) > 30 else '')
            lb.insert(tk.END, display_text)

    def _on_pc_list_click(self, event):
        idx = self.list_pc.curselection()
        if idx:
            history = self.clipboard_manager.get_history("pc")
            if idx[0] < len(history):
                self.clipboard_manager.set_clipboard(history[idx[0]])
                logging.info("已复制 PC 历史记录")

    def _on_phone_list_click(self, event):
        idx = self.list_phone.curselection()
        if idx:
            history = self.clipboard_manager.get_history("phone")
            if idx[0] < len(history):
                self.clipboard_manager.set_clipboard(history[idx[0]])
                logging.info("已复制手机历史到本机")

    def _clear_list(self, type_):
        self.clipboard_manager.clear_history(type_)
        self._update_list(type_)

    def _update_ip_display(self):
        ip = self._get_local_ip()
        def update():
            try:
                self.ip_entry.configure(state='normal')
                self.ip_entry.delete(0, tk.END)
                self.ip_entry.insert(0, ip)
                self.ip_entry.configure(state='readonly')
            except tk.TclError:
                pass
        self.root.after(0, update)

    def _get_local_ip(self):
        try:
            import subprocess
            cmd = r"Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -match 'Ethernet|Wi-Fi|以太网|WLAN' -and $_.InterfaceAlias -notmatch 'vEthernet|Virtual|WSL|Pseudo' } | Select-Object -ExpandProperty IPAddress | Select-Object -First 1"
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            p = subprocess.Popen(["powershell", "-Command", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo)
            out, _ = p.communicate(timeout=3)
            ip = out.strip()
            if ip: return ip
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(1)
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"

    def _start_server(self):
        if not self.clipboard_manager or not self.file_manager:
            raise RuntimeError("剪贴板或文件服务尚未初始化")
        self.input_handler = InputHandler(clipboard_manager=self.clipboard_manager)
        # 传入 on_connect_callback 和 on_disconnect_callback
        self.server = WebSocketServer(
            host="0.0.0.0", 
            port=SERVER_PORT,
            on_message_callback=self._handle_client_message,
            on_connect_callback=self._on_new_client_connected,
            on_disconnect_callback=self._on_client_disconnected
        )
        self.server_thread = threading.Thread(target=self._run_asyncio_loop, daemon=True)
        self.server_thread.start()

    def _run_asyncio_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.server.start())
        except Exception:
            logging.exception("WebSocket 服务异常退出")
        finally:
            self.loop.close()

    async def _on_new_client_connected(self, websocket):
        logging.info(f"新设备请求连接: {websocket.remote_address}")
        challenge = secrets.token_hex(16)
        self.auth_challenges[websocket] = challenge
        await websocket.send(json.dumps({
            "type": "AUTH_REQUIRED",
            "version": APP_VERSION,
            "protocol": PROTOCOL_VERSION,
            "challenge": challenge,
        }))
        async def expire_unauthenticated():
            await asyncio.sleep(10)
            if websocket not in self.authenticated_clients:
                await websocket.close(code=1008, reason="authentication timeout")
        asyncio.create_task(expire_unauthenticated())

    async def _on_client_disconnected(self, websocket):
        logging.warning(f"设备已断开: {websocket.remote_address}")
        self.authenticated_clients.discard(websocket)
        self.auth_challenges.pop(websocket, None)
        if self.connected_websocket == websocket:
            self.connected_websocket = None
            if self.file_manager:
                self.file_manager.abort_all()

if __name__ == "__main__":
    minimized_start = "--minimized" in sys.argv
    if not _claim_single_instance(activate_existing=not minimized_start):
        raise SystemExit(0)
    try:
        root = tk.Tk()
        if minimized_start:
            root.withdraw()
        app = AppGUI(root)
        root.mainloop()
    finally:
        _release_single_instance()

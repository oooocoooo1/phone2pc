import asyncio
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import socket
import logging
import sys
import os
import winreg
from PIL import Image, ImageDraw
import pystray
import json

from server import WebSocketServer
from input_handler import InputHandler
from clipboard_manager import ClipboardManager
from file_manager import FileManager
import windnd
from tkinter import filedialog

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
            except: pass
        try:
            self.text_widget.after(0, append)
        except: pass

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEI
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

class AppGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Phone2PC 智连 (v5.2)")
        self.root.geometry("500x600")
        
        # 启动时自动最小化到任务栏
        self.root.iconify()
        
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
        
        self.is_closing = False

        self._init_ui()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_click)
        
        # 分步启动
        self.root.after(200, self._init_autorun_state)
        self.root.after(500, self._start_ip_check)
        self.root.after(1000, self._start_server_safe)
        self.root.after(1500, self._start_clipboard)
        self.root.after(2000, self._init_file_manager) # 2s: 初始化文件管理器
        self.root.after(3000, self._init_tray_safe)

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

        # 日志区域
        log_frame = tk.LabelFrame(parent, text="运行日志", padx=5, pady=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, state='disabled', height=10)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self._setup_logging()
        
        # v5.0 Status Label
        tk.Label(parent, text="v5.0 已就绪 | 二进制+流控", fg="gray").pack(pady=5)

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
        
        # 接收记录
        tk.Label(parent, text="v5.0 已就绪 | 二进制+流控", fg="gray").pack(fill=tk.X, padx=10, pady=5)
        
        self.list_files = tk.Listbox(parent, selectmode=tk.SINGLE, height=15)
        self.list_files.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.list_files.bind("<Double-Button-1>", self._on_file_list_double_click)
        
        btn_open_dir = tk.Button(parent, text="打开接收文件夹", command=self._open_recv_dir)
        btn_open_dir.pack(fill=tk.X, padx=10, pady=5)

    def _init_file_manager(self):
        self.file_manager = FileManager(
            save_dir="received_files",
            send_callback=self._send_raw_json,
            on_receive_complete=self._on_file_received,
            on_send_complete=self._on_file_sent_success
        )
        # Hook Drag & Drop
        try:
            windnd.hook_dropfiles(self.root, func=self._on_drop_files)
            logging.info("文件拖拽功能已启用")
        except Exception as e:
            logging.error(f"拖拽初始化失败: {e}")

    def _on_drop_files(self, filenames):
        if not filenames: return
        # windnd 返回的是 bytes 列表 (Windows ANSI), 需解码? 
        # 文档说通常是 list of bytes. 
        # 测试中 windnd 1.0.7 可能返回 bytes. 需处理 decoding.
        
        files_to_send = []
        for f in filenames:
            if isinstance(f, bytes):
                f = f.decode("gbk") # Windows 路径通常是 gbk
            files_to_send.append(f)
            
        for f in files_to_send:
            self._log_file_ui(f"准备发送: {os.path.basename(f)}")
            self.file_manager.send_file_thread(f)

    def _select_file_to_send(self):
        files = filedialog.askopenfilenames()
        if files:
            for f in files:
                self._log_file_ui(f"准备发送: {os.path.basename(f)}")
                self.file_manager.send_file_thread(f)

    def _send_raw_json(self, json_str):
        """文件管理器使用的底层发送回调"""
        if self.connected_websocket:
            asyncio.run_coroutine_threadsafe(self.connected_websocket.send(json_str), self.loop)

    def _on_file_received(self, filepath):
        self.root.after(0, lambda: self._log_file_ui(f"已接收: {os.path.basename(filepath)} (双击打开)", filepath))

    def _on_file_sent_success(self, filename):
        self.root.after(0, lambda: messagebox.showinfo("发送成功", f"文件 '{filename}' 已成功发送给客户端"))

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
    def _handle_client_message(self, message, websocket):
        self.connected_websocket = websocket
        
        # v5.0: Binary Frame -> FileManager directly
        if isinstance(message, bytes):
            if self.file_manager:
                self.file_manager.handle_binary(message)
            return
        
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            # 路由：剪贴板消息
            if msg_type == "CLIPBOARD_SYNC":
                content = data.get("content", "")
                if content:
                    self.clipboard_manager.add_phone_history(content)
                    self.root.after(0, lambda: self._update_list("phone"))
                    logging.info("收到手机剪贴板同步")
                return
            
            # 路由：文件消息 (包括 ACK)
            if msg_type in ["FILE_OFFER", "FILE_DATA", "FILE_END", "ACK"]:
                if self.file_manager:
                    self.file_manager.handle_message(data)
                return
        except json.JSONDecodeError:
            pass

        # 默认作为文本输入处理
        threading.Thread(target=self.input_handler.type_text, args=(message,), daemon=True).start()

    def _init_autorun_state(self):
        try:
            is_auto = self._check_autorun()
            self.autorun_var.set(is_auto)
        except Exception as e:
            logging.error(f"注册表读取失败: {e}")

    def _start_ip_check(self):
        try: 
            threading.Thread(target=self._update_ip_display, daemon=True).start()
        except: pass

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

    def _init_tray(self):
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

    def _quit_app(self, icon=None, item=None):
        self.is_closing = True
        if self.tray_icon: self.tray_icon.stop()
        self.root.after(0, self._destroy_app)

    def _destroy_app(self):
        if self.clipboard_manager: self.clipboard_manager.stop()
        if self.loop: self.loop.call_soon_threadsafe(self.loop.stop)
        self.root.destroy()
        sys.exit(0)

    def _setup_logging(self):
        handler = TextHandler(self.log_text)
        formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S')
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

    def _check_autorun(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, "Phone2PC")
            winreg.CloseKey(key)
            return True
        except: return False

    def _toggle_autorun(self):
        app_path = sys.executable 
        if getattr(sys, 'frozen', False): target = sys.executable
        else: target = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            if self.autorun_var.get():
                winreg.SetValueEx(key, "Phone2PC", 0, winreg.REG_SZ, target)
                logging.info("已开启开机自启")
            else:
                try: winreg.DeleteValue(key, "Phone2PC"); logging.info("已关闭开机自启")
                except: pass
            winreg.CloseKey(key)
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
        if self.connected_websocket:
            msg = json.dumps({"type": "CLIPBOARD_SYNC", "source": "PC", "content": text})
            asyncio.run_coroutine_threadsafe(self.connected_websocket.send(msg), self.loop)

    def _update_list(self, type_):
        if type_ == "pc":
            data = self.clipboard_manager.pc_history
            lb = self.list_pc
        else:
            data = self.clipboard_manager.phone_history
            lb = self.list_phone
        
        lb.delete(0, tk.END)
        for item in data:
            display_text = item.replace('\n', ' ')[:30] + ('...' if len(item) > 30 else '')
            lb.insert(tk.END, display_text)

    def _on_pc_list_click(self, event):
        idx = self.list_pc.curselection()
        if idx:
            text = self.clipboard_manager.pc_history[idx[0]]
            self.clipboard_manager.set_clipboard(text)
            logging.info("已复制 PC 历史记录")

    def _on_phone_list_click(self, event):
        idx = self.list_phone.curselection()
        if idx:
            text = self.clipboard_manager.phone_history[idx[0]]
            self.clipboard_manager.set_clipboard(text) # 设置本机剪贴板
            logging.info("已复制手机历史到本机")

    def _clear_list(self, type_):
        if type_ == "pc": self.clipboard_manager.pc_history.clear(); self._update_list("pc")
        else: self.clipboard_manager.phone_history.clear(); self._update_list("phone")

    def _update_ip_display(self):
        ip = self._get_local_ip()
        def update():
            try:
                self.ip_entry.configure(state='normal')
                self.ip_entry.delete(0, tk.END)
                self.ip_entry.insert(0, ip)
                self.ip_entry.configure(state='readonly')
            except: pass
        self.root.after(0, update)

    def _get_local_ip(self):
        try:
            import subprocess
            cmd = r"Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -match 'Ethernet|Wi-Fi|以太网|WLAN' -and $_.InterfaceAlias -notmatch 'vEthernet|Virtual|WSL|Pseudo' } | Select-Object -ExpandProperty IPAddress | Select-Object -First 1"
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            p = subprocess.Popen(["powershell", "-Command", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo)
            out, err = p.communicate(timeout=3)
            ip = out.strip()
            if ip: return ip
        except: pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
        except: return "127.0.0.1"

    def _start_server(self):
        self.input_handler = InputHandler()
        # 传入 on_connect_callback 和 on_disconnect_callback
        self.server = WebSocketServer(
            host="0.0.0.0", 
            port=8765, 
            on_message_callback=self._handle_client_message,
            on_connect_callback=self._on_new_client_connected,
            on_disconnect_callback=self._on_client_disconnected
        )
        self.server_thread = threading.Thread(target=self._run_asyncio_loop, daemon=True)
        self.server_thread.start()

    def _run_asyncio_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.server.start())

    async def _on_new_client_connected(self, websocket):
        self.connected_websocket = websocket
        logging.info(f"新设备已连接: {websocket.remote_address}")
        
        # 增加延迟，避免连接握手期并发冲突 (Fix for v3.6)
        await asyncio.sleep(0.5)

        # 握手确认 (v5.2)
        try:
            await websocket.send(json.dumps({"type": "WELCOME", "version": "v5.2"}))
        except: pass

        # 连接建立时，立即推送最新一条 PC 剪贴板历史
        try:
            if self.clipboard_manager and self.clipboard_manager.pc_history:
                # 获取最新一条，注意线程安全（列表读取通常是原子的，但为了保险起见...）
                # 这里只读，冲突风险极低
                latest = self.clipboard_manager.pc_history[0]
                if latest:
                    msg = json.dumps({"type": "CLIPBOARD_SYNC", "source": "PC", "content": latest})
                    await websocket.send(msg)
                    logging.info("已向新连接推送最新剪贴板内容")
        except Exception as e:
            logging.error(f"推送剪贴板失败: {e}")

    async def _on_client_disconnected(self, websocket):
        logging.warning(f"设备已断开: {websocket.remote_address}")
        if self.connected_websocket == websocket:
            self.connected_websocket = None

if __name__ == "__main__":
    root = tk.Tk()
    app = AppGUI(root)
    if "--minimized" in sys.argv: root.withdraw()
    root.mainloop()

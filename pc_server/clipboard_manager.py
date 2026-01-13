import threading
import time
import logging
import pyperclip

class ClipboardManager:
    def __init__(self, on_clipboard_change=None, max_history=200):
        """
        :param on_clipboard_change: 当本机剪贴板变化时的回调 func(text)
        """
        self.on_clipboard_change = on_clipboard_change
        self.max_history = max_history
        
        self.pc_history = []
        self.phone_history = []
        
        self._running = False
        self._last_content = ""
        self._lock = threading.Lock()

    def start(self):
        self._running = True
        self._last_content = self._get_clipboard_safe()
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def stop(self):
        self._running = False

    def _get_clipboard_safe(self):
        try:
            return pyperclip.paste()
        except:
            return ""

    def _monitor_loop(self):
        """轮询本机剪贴板"""
        while self._running:
            current = self._get_clipboard_safe()
            if current and current != self._last_content:
                self._last_content = current
                self.add_pc_history(current)
                if self.on_clipboard_change:
                    self.on_clipboard_change(current)
            time.sleep(1.0) # 1秒轮询一次

    def add_pc_history(self, text):
        with self._lock:
            # 去重：如果已存在，先移除
            if text in self.pc_history:
                self.pc_history.remove(text)
            self.pc_history.insert(0, text)
            if len(self.pc_history) > self.max_history:
                self.pc_history.pop()

    def add_phone_history(self, text):
        with self._lock:
            if text in self.phone_history:
                self.phone_history.remove(text)
            self.phone_history.insert(0, text)
            if len(self.phone_history) > self.max_history:
                self.phone_history.pop()
    
    def delete_pc_item(self, index):
        with self._lock:
            if 0 <= index < len(self.pc_history):
                del self.pc_history[index]

    def delete_phone_item(self, index):
        with self._lock:
            if 0 <= index < len(self.phone_history):
                del self.phone_history[index]

    def set_clipboard(self, text):
        """将文本写入本机剪贴板 (不会触发 monitor 回调，需要处理循环更新问题)"""
        try:
            self._last_content = text # 更新 last，避免死循环触发 on_change
            pyperclip.copy(text)
        except Exception as e:
            logging.error(f"Failed to set clipboard: {e}")

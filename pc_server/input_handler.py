import pyperclip
import pyautogui
import time
import logging
import queue
import threading

class InputHandler:
    def __init__(self, on_activate_callback=None, clipboard_manager=None):
        """
        初始化输入处理器
        :param on_activate_callback: (已弃用，保留接口兼容性)
        """
        self.on_activate_callback = on_activate_callback
        self.clipboard_manager = clipboard_manager
        self._queue = queue.Queue(maxsize=64)
        self._stopped = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, name="remote-input", daemon=True)
        self._worker.start()

    def submit_text(self, text):
        try:
            self._queue.put_nowait(text)
            return True
        except queue.Full:
            return False

    def stop(self):
        self._stopped.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker is not threading.current_thread():
            self._worker.join(timeout=1)

    def _worker_loop(self):
        while not self._stopped.is_set():
            text = self._queue.get()
            if text is None:
                return
            self.type_text(text)

    def type_text(self, text):
        """
        模拟输入文本
        通过剪贴板 + Ctrl+V 的方式以支持中文和特殊字符
        """
        if not text:
            return

        logging.info("准备输入文本: %d 个字符", len(text))
        
        original = None
        try:
            original = pyperclip.paste()
            if self.clipboard_manager:
                self.clipboard_manager.set_clipboard(text)
            else:
                pyperclip.copy(text)
            # 稍微等待一下剪贴板操作生效
            time.sleep(0.1) 
            # 模拟 Ctrl+V
            pyautogui.hotkey('ctrl', 'v')
            # Most applications have consumed the paste by this point. Restoring
            # avoids destroying the user's clipboard and prevents sync echo.
            time.sleep(0.15)
            logging.info("模拟粘贴完成")
        except Exception as e:
            logging.error(f"输入文本失败: {e}")
        finally:
            if original is not None:
                try:
                    if self.clipboard_manager:
                        self.clipboard_manager.set_clipboard(original)
                    else:
                        pyperclip.copy(original)
                except Exception as e:
                    logging.error(f"恢复剪贴板失败: {e}")

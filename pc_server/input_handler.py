import pyperclip
import pyautogui
import time
import logging

class InputHandler:
    def __init__(self, on_activate_callback=None):
        """
        初始化输入处理器
        :param on_activate_callback: (已弃用，保留接口兼容性)
        """
        self.on_activate_callback = on_activate_callback

    def type_text(self, text):
        """
        模拟输入文本
        通过剪贴板 + Ctrl+V 的方式以支持中文和特殊字符
        """
        if not text:
            return

        logging.info(f"准备输入文本: {text}")
        
        try:
            pyperclip.copy(text)
            # 稍微等待一下剪贴板操作生效
            time.sleep(0.1) 
            # 模拟 Ctrl+V
            pyautogui.hotkey('ctrl', 'v')
            logging.info("模拟粘贴完成")
        except Exception as e:
            logging.error(f"输入文本失败: {e}")

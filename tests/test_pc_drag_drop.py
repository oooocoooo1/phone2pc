import sys
import queue
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_server"))

from main import AppGUI


class _FakeRoot:
    def __init__(self):
        self.callbacks = []
        self.bell_count = 0

    def after(self, delay, callback):
        self.callbacks.append((delay, callback))
        return len(self.callbacks)

    def bell(self):
        self.bell_count += 1


class _FakeNotebook:
    def __init__(self):
        self.selected = None

    def select(self, tab):
        self.selected = tab


class _FakeFileManager:
    def __init__(self):
        self.queued = []

    def send_file_thread(self, path):
        self.queued.append(path)


class PcDragDropTests(unittest.TestCase):
    def test_native_callback_defers_all_processing(self):
        gui = AppGUI.__new__(AppGUI)
        gui.root = _FakeRoot()
        gui._drop_queue = queue.SimpleQueue()
        processed = []
        gui._process_dropped_files = processed.append

        gui._on_drop_files([r"D:\drop\example.txt"])

        self.assertEqual(processed, [])
        gui.is_closing = False
        gui._drop_poll_after_id = None
        gui._poll_drop_queue()
        self.assertEqual(processed, [(r"D:\drop\example.txt",)])
        self.assertEqual(gui.root.callbacks[0][0], 40)

    def test_disconnected_drop_is_reported_without_queuing(self):
        gui = AppGUI.__new__(AppGUI)
        gui.root = _FakeRoot()
        gui.notebook = _FakeNotebook()
        gui.tab_files = object()
        gui.file_manager = _FakeFileManager()
        gui.connected_websocket = None
        gui.is_closing = False
        messages = []
        gui._log_file_ui = messages.append

        gui._process_dropped_files((r"D:\drop\example.txt",))

        self.assertEqual(gui.file_manager.queued, [])
        self.assertEqual(gui.root.bell_count, 1)
        self.assertIs(gui.notebook.selected, gui.tab_files)
        self.assertIn("手机尚未连接", messages[0])

    def test_connected_drop_is_queued(self):
        gui = AppGUI.__new__(AppGUI)
        gui.root = _FakeRoot()
        gui.file_manager = _FakeFileManager()
        gui.connected_websocket = object()
        gui.is_closing = False
        messages = []
        gui._log_file_ui = messages.append

        gui._process_dropped_files((r"D:\drop\example.txt",))

        self.assertEqual(gui.file_manager.queued, [r"D:\drop\example.txt"])
        self.assertIn("准备发送", messages[0])


if __name__ == "__main__":
    unittest.main()

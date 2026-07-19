import ctypes
import os
import queue
import sys
import tkinter as tk
import unittest
from ctypes import wintypes
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_server"))

from windows_drop import WM_DROPFILES, WindowsDropTarget


@unittest.skipUnless(os.name == "nt", "WM_DROPFILES is Windows-only")
class WindowsDropTargetTests(unittest.TestCase):
    def test_real_wm_dropfiles_message_uses_full_width_pointers(self):
        class DropFiles(ctypes.Structure):
            _fields_ = [
                ("pFiles", wintypes.DWORD),
                ("pt", wintypes.POINT),
                ("fNC", wintypes.BOOL),
                ("fWide", wintypes.BOOL),
            ]

        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        received = queue.SimpleQueue()
        target = WindowsDropTarget(root, lambda paths: received.put(tuple(paths)))
        path = r"D:\drop\unicode-文件.txt"

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t

        encoded_path = path.encode("utf-16le") + b"\0\0\0\0"
        header = DropFiles(
            ctypes.sizeof(DropFiles), wintypes.POINT(0, 0), False, True
        )
        handle = kernel32.GlobalAlloc(
            0x42, ctypes.sizeof(header) + len(encoded_path)
        )
        try:
            pointer = kernel32.GlobalLock(handle)
            self.assertTrue(pointer)
            ctypes.memmove(pointer, ctypes.byref(header), ctypes.sizeof(header))
            ctypes.memmove(
                pointer + ctypes.sizeof(header), encoded_path, len(encoded_path)
            )
            kernel32.GlobalUnlock(handle)
            # The drop target owns and frees the handle after this message.
            user32.SendMessageW(
                wintypes.HWND(root.winfo_id()),
                WM_DROPFILES,
                wintypes.WPARAM(handle),
                0,
            )
            handle = None
            self.assertEqual(received.get_nowait(), (path,))
        finally:
            target.close()
            root.destroy()
            if handle:
                kernel32.GlobalFree(handle)


if __name__ == "__main__":
    unittest.main()

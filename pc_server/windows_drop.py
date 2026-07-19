"""Safe WM_DROPFILES support for 64-bit Windows.

The native window procedure must not call into Tk.  It only extracts the
paths and hands them to a callback that is expected to perform a cheap,
thread-safe enqueue operation.
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from typing import Callable, Iterable


WM_DROPFILES = 0x0233
GWL_WNDPROC = -4


class WindowsDropTarget:
    """Install and safely restore a correctly typed WM_DROPFILES hook."""

    def __init__(self, window, on_drop: Callable[[Iterable[str]], None]):
        if os.name != "nt":
            raise OSError("WindowsDropTarget is only available on Windows")

        self._hwnd = wintypes.HWND(
            window.winfo_id() if hasattr(window, "winfo_id") else int(window)
        )
        self._on_drop = on_drop
        self._closed = False
        self._old_wndproc = 0
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._long_ptr = ctypes.c_ssize_t
        self._lresult = ctypes.c_ssize_t
        self._wndproc_type = ctypes.WINFUNCTYPE(
            self._lresult,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        self._set_window_long_ptr = self._user32.SetWindowLongPtrW
        self._set_window_long_ptr.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            self._long_ptr,
        ]
        self._set_window_long_ptr.restype = self._long_ptr

        self._call_window_proc = self._user32.CallWindowProcW
        self._call_window_proc.argtypes = [
            self._long_ptr,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._call_window_proc.restype = self._lresult

        self._drag_accept_files = self._shell32.DragAcceptFiles
        self._drag_accept_files.argtypes = [wintypes.HWND, wintypes.BOOL]
        self._drag_accept_files.restype = None

        self._drag_query_file = self._shell32.DragQueryFileW
        self._drag_query_file.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
            wintypes.LPWSTR,
            wintypes.UINT,
        ]
        self._drag_query_file.restype = wintypes.UINT

        self._drag_finish = self._shell32.DragFinish
        self._drag_finish.argtypes = [wintypes.HANDLE]
        self._drag_finish.restype = None

        # Keep this object alive for as long as Windows can call the hook.
        self._wndproc = self._wndproc_type(self._window_proc)
        new_proc = ctypes.cast(self._wndproc, ctypes.c_void_p).value
        ctypes.set_last_error(0)
        old_proc = self._set_window_long_ptr(
            self._hwnd, GWL_WNDPROC, self._long_ptr(new_proc)
        )
        error = ctypes.get_last_error()
        if not old_proc and error:
            raise ctypes.WinError(error)
        self._old_wndproc = int(old_proc)
        self._drag_accept_files(self._hwnd, True)

    def _read_drop_paths(self, drop_handle: int) -> tuple[str, ...]:
        handle = wintypes.HANDLE(drop_handle)
        count = self._drag_query_file(handle, 0xFFFFFFFF, None, 0)
        paths = []
        for index in range(count):
            length = self._drag_query_file(handle, index, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            copied = self._drag_query_file(handle, index, buffer, length + 1)
            if copied:
                paths.append(buffer.value)
        return tuple(paths)

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == WM_DROPFILES:
            try:
                paths = self._read_drop_paths(wparam)
                if paths:
                    self._on_drop(paths)
            except BaseException:
                # ctypes callbacks must never leak exceptions into Windows.
                logging.exception("读取 Windows 拖拽文件失败")
            finally:
                self._drag_finish(wintypes.HANDLE(wparam))
            return 0

        return self._call_window_proc(
            self._long_ptr(self._old_wndproc), hwnd, message, wparam, lparam
        )

    def close(self):
        """Stop new drops and restore Tk's original window procedure."""
        if self._closed:
            return
        self._closed = True
        self._drag_accept_files(self._hwnd, False)
        if self._old_wndproc:
            ctypes.set_last_error(0)
            restored = self._set_window_long_ptr(
                self._hwnd, GWL_WNDPROC, self._long_ptr(self._old_wndproc)
            )
            error = ctypes.get_last_error()
            if not restored and error:
                logging.warning("恢复 Windows 窗口过程失败: %s", ctypes.WinError(error))
        self._old_wndproc = 0

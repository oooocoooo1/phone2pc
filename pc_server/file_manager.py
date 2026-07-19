import hashlib
import http.client
import json
import logging
import os
import queue
import secrets
import socket
import threading
import time
import uuid

from constants import FILE_ACK_WINDOW, FILE_CHUNK_SIZE, MAX_FILE_SIZE
from fast_transfer import FAST_TRANSFER_PATH, FastUploadServer


class FileManager:
    """Single-session, queued file transfer with bounded buffering and integrity checks."""

    def __init__(
        self,
        save_dir="received_files",
        send_callback=None,
        on_receive_complete=None,
        on_send_complete=None,
        on_send_failed=None,
        on_transfer_progress=None,
        peer_host_callback=None,
    ):
        self.save_dir = os.path.abspath(save_dir)
        self.send_callback = send_callback  # func(data, wait=False)
        self.on_receive_complete = on_receive_complete
        self.on_send_complete = on_send_complete
        self.on_send_failed = on_send_failed
        self.on_transfer_progress = on_transfer_progress
        self.peer_host_callback = peer_host_callback

        os.makedirs(self.save_dir, exist_ok=True)

        self.receiving_files = {}
        self.current_receive_id = None
        self.chunk_size = FILE_CHUNK_SIZE
        self.ack_threshold = FILE_ACK_WINDOW
        self._receive_lock = threading.RLock()

        self._outbound = {}
        self._outbound_lock = threading.Lock()
        self._send_queue = queue.Queue(maxsize=128)
        self._stop_event = threading.Event()
        self._fast_server = None
        try:
            self._fast_server = FastUploadServer(self._handle_http_upload)
            self._fast_server.start()
        except OSError as exc:
            self._fast_server = None
            logging.warning("HTTP 高速文件通道不可用，将回退到 WebSocket: %s", exc)
        self._worker = threading.Thread(target=self._send_loop, name="file-transfer", daemon=True)
        self._worker.start()

    def handle_binary(self, data):
        with self._receive_lock:
            file_id = self.current_receive_id
            info = self.receiving_files.get(file_id) if file_id else None
        if not file_id:
            logging.warning("收到二进制数据，但没有活动的文件传输")
            return
        if info and info.get("transport") == "http":
            logging.warning("HTTP 传输期间收到意外的 WebSocket 二进制数据")
            return
        self._write_chunk(file_id, data)

    def handle_message(self, data):
        msg_type = data.get("type")
        file_id = data.get("file_id")

        if msg_type == "FILE_OFFER":
            self._start_receive(file_id, data.get("name"), data.get("size"), data.get("http_token"))
        elif msg_type == "FILE_END":
            self._finish_receive(file_id, data.get("size"), data.get("sha256"))
        elif msg_type == "FILE_ACCEPT":
            self._accept_outbound(file_id, data)
        elif msg_type == "FILE_COMPLETE":
            self._complete_outbound(file_id, data)
        elif msg_type == "FILE_ERROR":
            self._set_outbound_error(file_id, str(data.get("message", "对端拒绝了文件")))
        elif msg_type in ("FILE_ACK", "ACK"):
            # PC sender awaits each websocket.send(), so transport-level backpressure
            # is already bounded. ACK is accepted for protocol symmetry.
            return

    def send_file_thread(self, filepath):
        filepath = os.path.abspath(filepath)
        try:
            self._send_queue.put_nowait(filepath)
        except queue.Full:
            self._notify_send_failed(os.path.basename(filepath), "发送队列已满")

    def abort_all(self, reason="连接已断开"):
        with self._receive_lock:
            active_id = self.current_receive_id
        if active_id:
            info = self._cleanup_receive(active_id, delete_partial=True)
            if info:
                self._emit_progress("receive", active_id, info, "cancelled", force=True, message=reason)

        with self._outbound_lock:
            for state in self._outbound.values():
                state["error"] = reason
                state["accepted"].set()
                state["completed"].set()

        while True:
            try:
                queued = self._send_queue.get_nowait()
            except queue.Empty:
                break
            if queued is not None:
                self._notify_send_failed(os.path.basename(queued), reason)

    def stop(self):
        self._stop_event.set()
        if self._fast_server:
            self._fast_server.stop()
        self.abort_all("服务正在退出")
        try:
            self._send_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker is not threading.current_thread():
            self._worker.join(timeout=3)

    def _start_receive(self, file_id, name, size, http_token=None):
        if not isinstance(file_id, str) or not file_id or len(file_id) > 128:
            self._send_error(file_id, "无效的文件 ID")
            return
        if not isinstance(name, str) or not name or len(name) > 255 or "\x00" in name:
            self._send_error(file_id, "无效的文件名")
            return
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_FILE_SIZE:
            self._send_error(file_id, "文件大小超出限制")
            return

        with self._receive_lock:
            if self.current_receive_id is not None:
                self._send_error(file_id, "接收端正忙")
                return

            safe_name = os.path.basename(name.replace("\\", "/"))
            if safe_name in ("", ".", ".."):
                self._send_error(file_id, "无效的文件名")
                return

            try:
                final_path, temp_path = self._unique_paths(safe_name)
                handle = open(temp_path, "xb")
            except (OSError, ValueError) as exc:
                logging.error("无法创建接收文件 %s: %s", safe_name, exc)
                self._send_error(file_id, "无法创建接收文件")
                return

            use_http = (
                self._fast_server is not None
                and isinstance(http_token, str)
                and 32 <= len(http_token) <= 256
            )
            self.receiving_files[file_id] = {
                "handle": handle,
                "name": os.path.basename(final_path),
                "path": final_path,
                "temp_path": temp_path,
                "size": size,
                "received": 0,
                "bytes_since_ack": 0,
                "hasher": hashlib.sha256(),
                "started_at": time.monotonic(),
                "last_progress_at": 0.0,
                "last_progress_bytes": 0,
                "speed_bps": 0.0,
                "transport": "http" if use_http else "websocket",
                "http_token": http_token if use_http else None,
            }
            self.current_receive_id = file_id
            info = self.receiving_files[file_id]

        accept = {"type": "FILE_ACCEPT", "file_id": file_id}
        if info["transport"] == "http":
            accept.update({"transport": "http", "port": self._fast_server.port})
        self._send_json(accept)
        self._emit_progress("receive", file_id, info, "receiving", force=True)
        logging.info("开始接收文件: %s (%d bytes)", safe_name, size)

    def _write_chunk(self, file_id, raw_data):
        if not isinstance(raw_data, (bytes, bytearray, memoryview)):
            self._fail_receive(file_id, "无效的二进制数据")
            return

        with self._receive_lock:
            info = self.receiving_files.get(file_id)
            if not info:
                return

            chunk_size = len(raw_data)
            if info["received"] + chunk_size > info["size"]:
                self._fail_receive(file_id, "接收数据超过声明大小")
                return

            try:
                info["handle"].write(raw_data)
                info["hasher"].update(raw_data)
                info["received"] += chunk_size
                info["bytes_since_ack"] += chunk_size
                should_ack = (
                    info.get("transport") != "http"
                    and info["bytes_since_ack"] >= self.ack_threshold
                )
                if should_ack:
                    info["bytes_since_ack"] = 0
                    received = info["received"]
            except OSError as exc:
                logging.error("写入文件失败: %s", exc)
                self._fail_receive(file_id, "磁盘写入失败")
                return

        self._emit_progress("receive", file_id, info, "receiving")
        if should_ack:
            self._send_json({"type": "FILE_ACK", "file_id": file_id, "received": received})

    def _finish_receive(self, file_id, declared_size, expected_hash):
        with self._receive_lock:
            info = self.receiving_files.get(file_id)
            if not info:
                self._send_error(file_id, "没有对应的接收任务")
                return
            actual_size = info["received"]
            actual_hash = info["hasher"].hexdigest()

        self._emit_progress("receive", file_id, info, "verifying", force=True)

        if declared_size != info["size"] or actual_size != info["size"]:
            self._fail_receive(file_id, "文件大小校验失败")
            return
        if not isinstance(expected_hash, str) or not expected_hash or expected_hash.lower() != actual_hash:
            self._fail_receive(file_id, "文件哈希校验失败")
            return

        try:
            info["handle"].flush()
            info["handle"].close()
            os.replace(info["temp_path"], info["path"])
        except OSError as exc:
            logging.error("完成接收文件失败: %s", exc)
            self._fail_receive(file_id, "保存文件失败")
            return

        with self._receive_lock:
            self.receiving_files.pop(file_id, None)
            if self.current_receive_id == file_id:
                self.current_receive_id = None

        try:
            self._send_json(
                {"type": "FILE_COMPLETE", "file_id": file_id, "size": actual_size, "sha256": actual_hash}
            )
        except RuntimeError as exc:
            logging.warning("文件已保存，但无法发送完成确认: %s", exc)
        logging.info("文件接收完成: %s", info["name"])
        self._emit_progress("receive", file_id, info, "completed", force=True)
        if self.on_receive_complete:
            self.on_receive_complete(info["path"])

    def _send_loop(self):
        while not self._stop_event.is_set():
            filepath = self._send_queue.get()
            if filepath is None:
                break
            self._send_worker(filepath)

    def _send_worker(self, filepath):
        filename = os.path.basename(filepath)
        if not os.path.isfile(filepath):
            self._notify_send_failed(filename, "文件不存在")
            return

        size = os.path.getsize(filepath)
        if size > MAX_FILE_SIZE:
            self._notify_send_failed(filename, "文件大小超出限制")
            return

        file_id = str(uuid.uuid4())
        state = {
            "accepted": threading.Event(),
            "completed": threading.Event(),
            "error": None,
            "expected_size": size,
            "expected_hash": None,
            "name": filename,
            "size": size,
            "transferred": 0,
            "started_at": time.monotonic(),
            "last_progress_at": 0.0,
            "last_progress_bytes": 0,
            "speed_bps": 0.0,
            "transport": "websocket",
            "http_token": secrets.token_urlsafe(32),
            "http_host": None,
            "http_port": None,
        }
        with self._outbound_lock:
            self._outbound[file_id] = state

        try:
            self._emit_progress("send", file_id, state, "waiting", force=True)
            self._send_json(
                {
                    "type": "FILE_OFFER",
                    "file_id": file_id,
                    "name": filename,
                    "size": size,
                    "http_token": state["http_token"],
                },
                wait=True,
            )
            if not state["accepted"].wait(timeout=15):
                raise TimeoutError("等待接收端响应超时")
            if state["error"]:
                raise RuntimeError(state["error"])

            state["started_at"] = time.monotonic()
            state["last_progress_at"] = 0.0
            state["last_progress_bytes"] = 0
            state["speed_bps"] = 0.0
            self._emit_progress("send", file_id, state, "sending", force=True)

            hasher = hashlib.sha256()
            connection = None
            try:
                if state["transport"] == "http":
                    connection = http.client.HTTPConnection(
                        state["http_host"], state["http_port"], timeout=60
                    )
                    connection.putrequest("POST", FAST_TRANSFER_PATH)
                    connection.putheader("Content-Length", str(size))
                    connection.putheader("Content-Type", "application/octet-stream")
                    connection.putheader("X-Phone2PC-File-ID", file_id)
                    connection.putheader("X-Phone2PC-Token", state["http_token"])
                    connection.putheader("Connection", "close")
                    connection.endheaders()
                    if connection.sock:
                        try:
                            connection.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                            connection.sock.setsockopt(
                                socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024
                            )
                        except OSError:
                            # Socket tuning is optional; never sacrifice the
                            # transfer if an OS rejects a buffer hint.
                            pass

                with open(filepath, "rb") as handle:
                    while not self._stop_event.is_set():
                        chunk = handle.read(self.chunk_size)
                        if not chunk:
                            break
                        if state["error"]:
                            raise RuntimeError(state["error"])
                        hasher.update(chunk)
                        if connection:
                            connection.send(chunk)
                        else:
                            # WebSocket compatibility path for older clients.
                            self._send_raw(chunk, wait=True)
                        state["transferred"] += len(chunk)
                        self._emit_progress("send", file_id, state, "sending")

                if connection:
                    response = connection.getresponse()
                    response.read(4096)
                    if response.status != 200:
                        raise RuntimeError(f"HTTP 高速传输失败 ({response.status})")
            finally:
                if connection:
                    connection.close()

            if self._stop_event.is_set():
                raise RuntimeError("发送已取消")

            digest = hasher.hexdigest()
            state["expected_hash"] = digest
            self._emit_progress("send", file_id, state, "verifying", force=True)
            self._send_json(
                {"type": "FILE_END", "file_id": file_id, "size": size, "sha256": digest},
                wait=True,
            )
            if not state["completed"].wait(timeout=60):
                raise TimeoutError("等待接收完成确认超时")
            if state["error"]:
                raise RuntimeError(state["error"])

            logging.info("文件发送并校验完成: %s", filename)
            self._emit_progress("send", file_id, state, "completed", force=True)
            if self.on_send_complete:
                self.on_send_complete(filename)
        except Exception as exc:
            logging.error("发送文件失败 %s: %s", filepath, exc)
            self._emit_progress("send", file_id, state, "failed", force=True, message=str(exc))
            self._notify_send_failed(filename, str(exc))
        finally:
            with self._outbound_lock:
                self._outbound.pop(file_id, None)

    def _set_outbound_event(self, file_id, event_name):
        with self._outbound_lock:
            state = self._outbound.get(file_id)
            if state:
                state[event_name].set()

    def _accept_outbound(self, file_id, data):
        with self._outbound_lock:
            state = self._outbound.get(file_id)
            if not state:
                return
            if data.get("transport") == "http":
                port = data.get("port")
                try:
                    host = self.peer_host_callback() if self.peer_host_callback else None
                except Exception:
                    host = None
                if isinstance(port, int) and 0 < port <= 65535 and isinstance(host, str) and host:
                    state["transport"] = "http"
                    state["http_host"] = host
                    state["http_port"] = port
            state["accepted"].set()

    def _complete_outbound(self, file_id, data):
        with self._outbound_lock:
            state = self._outbound.get(file_id)
            if not state:
                return
            if data.get("size") != state["expected_size"] or data.get("sha256") != state["expected_hash"]:
                state["error"] = "接收端完成确认校验失败"
            state["completed"].set()

    def _set_outbound_error(self, file_id, message):
        with self._outbound_lock:
            state = self._outbound.get(file_id)
            if state:
                state["error"] = message
                state["accepted"].set()
                state["completed"].set()

    def _send_json(self, payload, wait=False):
        self._send_raw(json.dumps(payload, ensure_ascii=False), wait=wait)

    def _send_raw(self, data, wait=False):
        if not self.send_callback:
            raise RuntimeError("发送通道未初始化")
        return self.send_callback(data, wait=wait)

    def _send_error(self, file_id, message):
        self._send_json({"type": "FILE_ERROR", "file_id": file_id, "message": message})

    def _handle_http_upload(self, file_id, token, stream, content_length):
        with self._receive_lock:
            info = self.receiving_files.get(file_id)
            valid = (
                info is not None
                and self.current_receive_id == file_id
                and info.get("transport") == "http"
                and isinstance(token, str)
                and secrets.compare_digest(token, info.get("http_token") or "")
                and content_length == info["size"]
            )
        if not valid:
            return False

        remaining = content_length
        while remaining and not self._stop_event.is_set():
            chunk = stream.read(min(self.chunk_size, remaining))
            if not chunk:
                self._fail_receive(file_id, "HTTP 文件流提前结束")
                return False
            self._write_chunk(file_id, chunk)
            remaining -= len(chunk)
            with self._receive_lock:
                if file_id not in self.receiving_files:
                    return False

        return remaining == 0 and not self._stop_event.is_set()

    def _fail_receive(self, file_id, message):
        info = self._cleanup_receive(file_id, delete_partial=True)
        if info:
            self._emit_progress("receive", file_id, info, "failed", force=True, message=message)
        self._send_error(file_id, message)

    def _cleanup_receive(self, file_id, delete_partial=False):
        with self._receive_lock:
            info = self.receiving_files.pop(file_id, None)
            if self.current_receive_id == file_id:
                self.current_receive_id = None
        if not info:
            return None
        try:
            info["handle"].close()
        except OSError:
            pass
        if delete_partial:
            try:
                os.remove(info["temp_path"])
            except FileNotFoundError:
                pass
            except OSError as exc:
                logging.warning("无法删除未完成文件 %s: %s", info["temp_path"], exc)
        return info

    def _unique_paths(self, safe_name):
        base, ext = os.path.splitext(safe_name)
        counter = 0
        while True:
            suffix = "" if counter == 0 else f"_{counter}"
            final_path = os.path.abspath(os.path.join(self.save_dir, f"{base}{suffix}{ext}"))
            if os.path.commonpath((self.save_dir, final_path)) != self.save_dir:
                raise ValueError("接收路径超出目标目录")
            temp_path = final_path + ".part"
            if not os.path.exists(final_path) and not os.path.exists(temp_path):
                return final_path, temp_path
            counter += 1

    def _notify_send_failed(self, filename, message):
        if self.on_send_failed:
            self.on_send_failed(filename, message)

    def _emit_progress(self, direction, file_id, state, status, force=False, message=None):
        """Emit lightweight UI progress snapshots at most four times per second."""
        if not self.on_transfer_progress:
            return

        now = time.monotonic()
        transferred_key = "received" if direction == "receive" else "transferred"
        total_key = "size"
        transferred = int(state.get(transferred_key, 0))
        total = int(state.get(total_key, 0))
        last_at = float(state.get("last_progress_at", 0.0))

        if not force and last_at and now - last_at < 0.25:
            return

        last_bytes = int(state.get("last_progress_bytes", 0))
        if last_at and now > last_at and transferred >= last_bytes:
            instant_speed = (transferred - last_bytes) / (now - last_at)
            previous_speed = float(state.get("speed_bps", 0.0))
            state["speed_bps"] = instant_speed if previous_speed <= 0 else previous_speed * 0.7 + instant_speed * 0.3

        state["last_progress_at"] = now
        state["last_progress_bytes"] = transferred
        event = {
            "direction": direction,
            "file_id": file_id,
            "name": state.get("name", ""),
            "status": status,
            "transferred": transferred,
            "total": total,
            "speed_bps": float(state.get("speed_bps", 0.0)),
            "transport": state.get("transport", "websocket"),
        }
        if message:
            event["message"] = message
        try:
            self.on_transfer_progress(event)
        except Exception:
            logging.exception("更新文件传输进度失败")

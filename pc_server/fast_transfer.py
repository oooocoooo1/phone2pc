import http.server
import logging
import socket
import threading


FAST_TRANSFER_PORT = 8766
FAST_TRANSFER_PATH = "/phone2pc/upload"


class _ThreadingUploadServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        # SO_REUSEADDR on Windows permits two live servers to bind the same
        # address, which can route uploads to the wrong Phone2PC instance.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,
                1,
            )
            self.socket.bind(self.server_address)
            self.server_address = self.socket.getsockname()
            return
        super().server_bind()


class FastUploadServer:
    """Small raw HTTP upload endpoint used only after WebSocket negotiation."""

    def __init__(self, upload_callback, host="0.0.0.0", port=FAST_TRANSFER_PORT):
        self.upload_callback = upload_callback
        self.host = host
        self.requested_port = port
        self._server = None
        self._thread = None

    @property
    def port(self):
        return self._server.server_address[1] if self._server else None

    def start(self):
        if self._server:
            return self.port

        owner = self

        class UploadHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.connection.settimeout(60)
                if self.path != FAST_TRANSFER_PATH:
                    self._respond(404)
                    return

                file_id = self.headers.get("X-Phone2PC-File-ID", "")
                token = self.headers.get("X-Phone2PC-Token", "")
                raw_length = self.headers.get("Content-Length")
                try:
                    content_length = int(raw_length)
                except (TypeError, ValueError):
                    self._respond(411)
                    return

                try:
                    accepted = owner.upload_callback(file_id, token, self.rfile, content_length)
                except Exception:
                    logging.exception("HTTP 高速文件接收失败")
                    accepted = False
                self._respond(200 if accepted else 409)

            def _respond(self, status):
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

            def log_message(self, fmt, *args):
                logging.debug("HTTP 文件通道: " + fmt, *args)

        try:
            self._server = _ThreadingUploadServer((self.host, self.requested_port), UploadHandler)
        except OSError:
            # Preserve the fast path if the preferred port is occupied. The
            # negotiated FILE_ACCEPT message carries the actual port.
            self._server = _ThreadingUploadServer((self.host, 0), UploadHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="fast-upload-server",
            daemon=True,
        )
        self._thread.start()
        logging.info("HTTP 高速文件通道监听于 %s:%d", self.host, self.port)
        return self.port

    def stop(self):
        server = self._server
        if not server:
            return
        self._server = None
        server.shutdown()
        server.server_close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None

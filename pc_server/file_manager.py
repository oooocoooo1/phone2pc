import os
import json
import threading
import uuid
import logging
import hashlib

class FileManager:
    def __init__(self, save_dir="received_files", send_callback=None, on_receive_complete=None, on_send_complete=None):
        self.save_dir = save_dir
        self.send_callback = send_callback # func(data) - str for JSON, bytes for binary
        self.on_receive_complete = on_receive_complete
        self.on_send_complete = on_send_complete
        
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)
            
        self.receiving_files = {}
        self.current_receive_id = None  # v5.0: Single file assumption for binary routing
        self.chunk_size = 64 * 1024  # 64KB (v5.0 Binary Mode)
        
        # Flow Control (Send)
        self.ack_event = threading.Event()
        self.ack_event.set()  # Initially unblocked
        self.window_size = 2 * 1024 * 1024  # 2MB Window
        
        # Flow Control (Receive)
        self.bytes_since_last_ack = 0
        self.ack_threshold = 2 * 1024 * 1024  # 2MB

    def handle_binary(self, data):
        """处理接收到的二进制文件数据 (v5.0)"""
        if not self.current_receive_id:
            logging.warning("Received binary data but no active file transfer!")
            return
        self._write_chunk_binary(self.current_receive_id, data)

    def handle_message(self, data):
        """处理接收到的 JSON 信令"""
        msg_type = data.get("type")
        file_id = data.get("file_id")
        
        if msg_type == "FILE_OFFER":
            name = data.get("name")
            size = data.get("size")
            self._start_receive(file_id, name, size)
            
        elif msg_type == "FILE_DATA":
            # 兼容旧版 Base64 模式 (来自 Android v4.x)
            import base64
            b64_data = data.get("data")
            is_last = data.get("last", False)
            if b64_data:
                raw = base64.b64decode(b64_data)
                self._write_chunk_binary(file_id, raw, is_last)
            
        elif msg_type == "ACK":
            # Flow Control: Client finished writing/processing
            logging.info("收到 ACK，继续发送...")
            self.ack_event.set()

    def _start_receive(self, file_id, name, size):
        try:
            safe_name = os.path.basename(name)
            path = os.path.join(self.save_dir, safe_name)
            
            base, ext = os.path.splitext(safe_name)
            counter = 1
            while os.path.exists(path):
                path = os.path.join(self.save_dir, f"{base}_{counter}{ext}")
                counter += 1
                
            f = open(path, 'wb')
            self.receiving_files[file_id] = {
                "handle": f,
                "name": os.path.basename(path),
                "path": path,
                "size": size,
                "received": 0
            }
            self.current_receive_id = file_id
            self.bytes_since_last_ack = 0
            logging.info(f"开始接收文件 (Binary): {name} -> {path}")
        except Exception as e:
            logging.error(f"无法创建文件 {name}: {e}")

    def _write_chunk_binary(self, file_id, raw_data, is_last_override=None):
        info = self.receiving_files.get(file_id)
        if not info: return
        
        try:
            info["handle"].write(raw_data)
            info["received"] += len(raw_data)
            
            # Flow Control: Send ACK?
            self.bytes_since_last_ack += len(raw_data)
            if self.bytes_since_last_ack >= self.ack_threshold:
                ack_msg = {"type": "ACK", "file_id": file_id, "received": info["received"]}
                self.send_callback(json.dumps(ack_msg))
                self.bytes_since_last_ack = 0
            
            # Check EOF (for legacy mode with is_last, or size-based)
            is_done = is_last_override if is_last_override is not None else (info["received"] >= info["size"])
            if is_done:
                info["handle"].close()
                filename = info["name"]
                path = info["path"]
                del self.receiving_files[file_id]
                self.current_receive_id = None
                
                # Final ACK
                ack_msg = {"type": "ACK", "file_id": file_id, "received": info["received"]}
                self.send_callback(json.dumps(ack_msg))
                
                logging.info(f"文件接收完成: {filename}")
                if self.on_receive_complete:
                    self.on_receive_complete(path)
                    
        except Exception as e:
            logging.error(f"写入文件出错: {e}")
            self._cleanup_receive(file_id)

    def _cleanup_receive(self, file_id):
        info = self.receiving_files.get(file_id)
        if info:
            if info.get("handle"): info["handle"].close()
            del self.receiving_files[file_id]
        if self.current_receive_id == file_id:
            self.current_receive_id = None

    def send_file_thread(self, filepath):
        """在独立线程中发送文件"""
        threading.Thread(target=self._send_worker, args=(filepath,), daemon=True).start()

    def _send_worker(self, filepath):
        if not os.path.exists(filepath): return
        
        file_id = str(uuid.uuid4())
        filename = os.path.basename(filepath)
        size = os.path.getsize(filepath)
        
        # 1. FILE_OFFER (JSON)
        offer = {
            "type": "FILE_OFFER",
            "file_id": file_id,
            "name": filename,
            "size": size
        }
        self.send_callback(json.dumps(offer))
        logging.info(f"发送 FILE_OFFER: {filename}, {size} bytes")
        
        # Small delay to let receiver prepare
        import time
        time.sleep(0.2)
        
        # 2. Binary Data Loop with simple throttling (no ACK dependency)
        try:
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(self.chunk_size)
                    if not chunk: break
                    
                    # Send Binary Frame
                    self.send_callback(chunk)
                    
                    # Simple throttle: 1ms per 64KB ≈ 64MB/s max
                    time.sleep(0.001)
                    
            logging.info(f"文件发送完毕: {filename}")
            if self.on_send_complete:
                self.on_send_complete(filename)

        except Exception as e:
            logging.error(f"发送文件中断: {filepath}, {e}")

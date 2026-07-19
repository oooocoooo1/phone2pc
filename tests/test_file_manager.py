import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_server"))

from constants import FILE_CHUNK_SIZE, MAX_FILE_SIZE
from file_manager import FileManager
from fast_transfer import FAST_TRANSFER_PATH, FastUploadServer


class FileManagerReceiveTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.messages = []
        self.completed = []
        self.progress = []

        def send(data, wait=False):
            if isinstance(data, str):
                self.messages.append(json.loads(data))

        self.manager = FileManager(
            save_dir=self.temp_dir.name,
            send_callback=send,
            on_receive_complete=self.completed.append,
            on_transfer_progress=self.progress.append,
        )

    def tearDown(self):
        self.manager.stop()
        self.temp_dir.cleanup()

    def test_zero_byte_file_completes(self):
        file_id = "zero-file"
        self.manager.handle_message({"type": "FILE_OFFER", "file_id": file_id, "name": "empty.txt", "size": 0})
        self.manager.handle_message(
            {
                "type": "FILE_END",
                "file_id": file_id,
                "size": 0,
                "sha256": hashlib.sha256(b"").hexdigest(),
            }
        )

        self.assertEqual([message["type"] for message in self.messages], ["FILE_ACCEPT", "FILE_COMPLETE"])
        self.assertEqual(len(self.completed), 1)
        self.assertEqual(Path(self.completed[0]).read_bytes(), b"")
        self.assertEqual([event["status"] for event in self.progress], ["receiving", "verifying", "completed"])
        self.assertEqual(self.progress[-1]["transferred"], 0)
        self.assertEqual(self.progress[-1]["total"], 0)

    def test_filename_is_confined_and_hash_is_verified(self):
        payload = b"phone2pc" * 1024
        file_id = "safe-file"
        self.manager.handle_message(
            {"type": "FILE_OFFER", "file_id": file_id, "name": "../outside.txt", "size": len(payload)}
        )
        self.manager.handle_binary(payload)
        self.manager.handle_message(
            {
                "type": "FILE_END",
                "file_id": file_id,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

        path = Path(self.completed[0]).resolve()
        self.assertEqual(path.parent, Path(self.temp_dir.name).resolve())
        self.assertEqual(path.name, "outside.txt")
        self.assertEqual(path.read_bytes(), payload)

    def test_hash_mismatch_removes_partial_file(self):
        payload = b"corrupt"
        file_id = "bad-hash"
        self.manager.handle_message(
            {"type": "FILE_OFFER", "file_id": file_id, "name": "bad.bin", "size": len(payload)}
        )
        self.manager.handle_binary(payload)
        self.manager.handle_message(
            {"type": "FILE_END", "file_id": file_id, "size": len(payload), "sha256": "0" * 64}
        )

        self.assertEqual(self.messages[-1]["type"], "FILE_ERROR")
        self.assertFalse(list(Path(self.temp_dir.name).glob("*.part")))
        self.assertFalse((Path(self.temp_dir.name) / "bad.bin").exists())

    def test_oversized_offer_is_rejected(self):
        self.manager.handle_message(
            {"type": "FILE_OFFER", "file_id": "too-large", "name": "large.bin", "size": MAX_FILE_SIZE + 1}
        )
        self.assertEqual(self.messages[-1]["type"], "FILE_ERROR")


class FileManagerSendTests(unittest.TestCase):
    def test_batch_send_is_serial_and_chunked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(b"a" * (700 * 1024))
            second.write_bytes(b"b" * (500 * 1024))

            events = []
            progress = []
            completed = []
            done = threading.Event()
            manager = None

            def on_complete(name):
                completed.append(name)
                if len(completed) == 2:
                    done.set()

            def send(data, wait=False):
                if isinstance(data, bytes):
                    events.append(("DATA", len(data)))
                    return
                message = json.loads(data)
                events.append((message["type"], message.get("name")))
                if message["type"] == "FILE_OFFER":
                    manager.handle_message({"type": "FILE_ACCEPT", "file_id": message["file_id"]})
                elif message["type"] == "FILE_END":
                    manager.handle_message({
                        "type": "FILE_COMPLETE",
                        "file_id": message["file_id"],
                        "size": message["size"],
                        "sha256": message["sha256"],
                    })

            manager = FileManager(
                save_dir=root / "received",
                send_callback=send,
                on_send_complete=on_complete,
                on_transfer_progress=progress.append,
            )
            try:
                manager.send_file_thread(first)
                manager.send_file_thread(second)
                self.assertTrue(done.wait(5), "queued sends did not complete")
                self.assertEqual(completed, ["first.bin", "second.bin"])

                offers = [index for index, event in enumerate(events) if event[0] == "FILE_OFFER"]
                ends = [index for index, event in enumerate(events) if event[0] == "FILE_END"]
                self.assertEqual(len(offers), 2)
                self.assertEqual(len(ends), 2)
                self.assertLess(offers[0], ends[0])
                self.assertLess(ends[0], offers[1])
                self.assertLess(offers[1], ends[1])
                self.assertTrue(all(size <= FILE_CHUNK_SIZE for kind, size in events if kind == "DATA"))
                completed_progress = [event for event in progress if event["status"] == "completed"]
                self.assertEqual([event["name"] for event in completed_progress], ["first.bin", "second.bin"])
                self.assertTrue(all(event["transferred"] == event["total"] for event in completed_progress))
                self.assertTrue(any(event["status"] == "sending" for event in progress))
                self.assertTrue(all(event["speed_bps"] >= 0 for event in progress))
            finally:
                manager.stop()

    def test_http_fast_path_sends_raw_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fast.bin"
            payload = b"fast-http-path" * (300 * 1024)
            source.write_bytes(payload)
            received = bytearray()
            done = threading.Event()
            websocket_binary = []
            manager = None

            def receive_upload(file_id, token, stream, content_length):
                received.extend(stream.read(content_length))
                return len(received) == content_length

            upload_server = FastUploadServer(receive_upload, host="127.0.0.1", port=0)
            upload_server.start()

            def send(data, wait=False):
                if isinstance(data, bytes):
                    websocket_binary.append(data)
                    return
                message = json.loads(data)
                if message["type"] == "FILE_OFFER":
                    manager.handle_message({
                        "type": "FILE_ACCEPT",
                        "file_id": message["file_id"],
                        "transport": "http",
                        "port": upload_server.port,
                    })
                elif message["type"] == "FILE_END":
                    manager.handle_message({
                        "type": "FILE_COMPLETE",
                        "file_id": message["file_id"],
                        "size": message["size"],
                        "sha256": message["sha256"],
                    })

            manager = FileManager(
                save_dir=root / "received",
                send_callback=send,
                on_send_complete=lambda _: done.set(),
                peer_host_callback=lambda: "127.0.0.1",
            )
            try:
                manager.send_file_thread(source)
                self.assertTrue(done.wait(10), "HTTP fast-path send did not complete")
                self.assertEqual(bytes(received), payload)
                self.assertEqual(websocket_binary, [])
            finally:
                manager.stop()
                upload_server.stop()

    def test_http_fast_path_receives_raw_stream(self):
        import http.client

        with tempfile.TemporaryDirectory() as directory:
            payload = b"phone-to-pc" * (300 * 1024)
            token = "t" * 48
            messages = []
            completed = []

            def send(data, wait=False):
                if isinstance(data, str):
                    messages.append(json.loads(data))

            manager = FileManager(
                save_dir=directory,
                send_callback=send,
                on_receive_complete=completed.append,
            )
            try:
                manager.handle_message({
                    "type": "FILE_OFFER",
                    "file_id": "http-receive",
                    "name": "received.bin",
                    "size": len(payload),
                    "http_token": token,
                })
                accept = messages[-1]
                self.assertEqual(accept["transport"], "http")

                rejected = http.client.HTTPConnection("127.0.0.1", accept["port"], timeout=10)
                rejected.request(
                    "POST",
                    FAST_TRANSFER_PATH,
                    body=b"",
                    headers={
                        "X-Phone2PC-File-ID": "http-receive",
                        "X-Phone2PC-Token": "wrong-token",
                    },
                )
                rejected_response = rejected.getresponse()
                rejected_response.read()
                rejected.close()
                self.assertEqual(rejected_response.status, 409)
                self.assertEqual(manager.receiving_files["http-receive"]["received"], 0)

                connection = http.client.HTTPConnection("127.0.0.1", accept["port"], timeout=10)
                connection.request(
                    "POST",
                    FAST_TRANSFER_PATH,
                    body=payload,
                    headers={
                        "X-Phone2PC-File-ID": "http-receive",
                        "X-Phone2PC-Token": token,
                        "Content-Type": "application/octet-stream",
                    },
                )
                response = connection.getresponse()
                response.read()
                connection.close()
                self.assertEqual(response.status, 200)

                manager.handle_message({
                    "type": "FILE_END",
                    "file_id": "http-receive",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                })
                self.assertEqual(Path(completed[0]).read_bytes(), payload)
            finally:
                manager.stop()


if __name__ == "__main__":
    unittest.main()

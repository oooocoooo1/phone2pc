import asyncio
import websockets
import logging
import inspect

from constants import MAX_MESSAGE_SIZE

class WebSocketServer:
    def __init__(self, host="0.0.0.0", port=8765, on_message_callback=None, on_connect_callback=None, on_disconnect_callback=None):
        """
        初始化 WebSocket 服务器
        :param on_message_callback: 收到消息时的回调函数 (func(text, websocket))
        :param on_connect_callback: 连接建立时的回调函数 (func(websocket))
        :param on_disconnect_callback: 连接断开时的回调函数 (func(websocket))
        """
        self.host = host
        self.port = port
        self.on_message_callback = on_message_callback
        self.on_connect_callback = on_connect_callback
        self.on_disconnect_callback = on_disconnect_callback
        self.clients = set()
        self._stop_event = None

    async def register(self, websocket):
        self.clients.add(websocket)
        logging.info(f"新客户端连接: {websocket.remote_address}")

    async def unregister(self, websocket):
        self.clients.discard(websocket)
        logging.info(f"客户端断开: {websocket.remote_address}")
        if self.on_disconnect_callback:
            try:
                result = self.on_disconnect_callback(websocket)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logging.error(f"Disconnect callback failed: {e}")

    async def handle_client(self, websocket):
        if len(self.clients) >= 8:
            await websocket.close(code=1013, reason="too many connections")
            return
        await self.register(websocket)
        # 新连接建立，触发回调 (例如发送当前剪贴板)
        if self.on_connect_callback:
            try:
                result = self.on_connect_callback(websocket)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logging.error(f"Connect callback failed: {e}")

        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # Binary Frame (File Data)
                    if self.on_message_callback:
                        result = self.on_message_callback(message, websocket)
                        if inspect.isawaitable(result):
                            await result
                    continue

                # Never log clipboard or remote-input contents.
                logging.debug("收到文本消息: %d bytes", len(message.encode("utf-8")))
                
                if self.on_message_callback:
                    result = self.on_message_callback(message, websocket)
                    if inspect.isawaitable(result):
                        await result
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            logging.exception("处理客户端消息失败")
        finally:
            await self.unregister(websocket)

    async def broadcast_activation(self):
        """向所有连接的客户端发送激活信号"""
        if not self.clients:
            logging.warning("没有连接的客户端，无法发送激活信号")
            return
        
        message = "ACTIVATE"
        logging.info(f"广播消息: {message} 给 {len(self.clients)} 个客户端")
        # websockets.broadcast 需要 iterable
        # 注意：在 websockets 10.x+ broadcast 是同步方法还是异步方法需确认，通常 send 是 awaitable
        # 这里为了稳健，逐个发送
        for client in self.clients:
            try:
                await client.send(message)
            except Exception as e:
                logging.error(f"发送消息失败: {e}")

    async def start(self):
        logging.info(f"启动 WebSocket 服务器于 ws://{self.host}:{self.port}")
        self._stop_event = asyncio.Event()
        async with websockets.serve(
            self.handle_client, 
            self.host, 
            self.port,
            ping_interval=20,
            ping_timeout=20,
            max_size=MAX_MESSAGE_SIZE,
            max_queue=16,
            write_limit=2 * 1024 * 1024,
            # File data is already compressed in most real workloads. Deflate
            # costs CPU and was the main throughput bottleneck.
            compression=None,
            origins=[None],
        ):
            await self._stop_event.wait()

    async def stop(self):
        if self._stop_event is None:
            return
        clients = list(self.clients)
        if clients:
            await asyncio.gather(
                *(client.close(code=1001, reason="server shutdown") for client in clients),
                return_exceptions=True,
            )
        self._stop_event.set()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = WebSocketServer()
    asyncio.run(server.start())

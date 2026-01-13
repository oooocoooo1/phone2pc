import asyncio
import websockets
import logging

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

    async def register(self, websocket):
        self.clients.add(websocket)
        logging.info(f"新客户端连接: {websocket.remote_address}")

    async def unregister(self, websocket):
        self.clients.remove(websocket)
        logging.info(f"客户端断开: {websocket.remote_address}")
        if self.on_disconnect_callback:
            try:
                import inspect
                if inspect.iscoroutinefunction(self.on_disconnect_callback):
                    await self.on_disconnect_callback(websocket)
                else:
                    self.on_disconnect_callback(websocket)
            except Exception as e:
                logging.error(f"Disconnect callback failed: {e}")

    async def handle_client(self, websocket):
        await self.register(websocket)
        # 新连接建立，触发回调 (例如发送当前剪贴板)
        if self.on_connect_callback:
            try:
                import inspect
                if inspect.iscoroutinefunction(self.on_connect_callback):
                    await self.on_connect_callback(websocket)
                else:
                    self.on_connect_callback(websocket)
            except Exception as e:
                logging.error(f"Connect callback failed: {e}")

        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # Binary Frame (File Data)
                    if self.on_message_callback:
                         # Compat with async/sync
                        import inspect
                        if inspect.iscoroutinefunction(self.on_message_callback):
                            await self.on_message_callback(message, websocket)
                        else:
                            res = self.on_message_callback(message, websocket)
                            if inspect.isawaitable(res):
                                await res
                    continue

                # Text Frame (JSON)
                # 简单判断：如果以 '{' 开头认为是 JSON，否则作为普通文本输入
                if message.startswith('{') and '"type":' in message:
                    # 尝试解析 type 以优化日志
                    try:
                         # 快速检查是否是 FILE_DATA (避免解析大 JSON)
                         if '"type": "FILE_DATA"' in message or '"type":"FILE_DATA"' in message:
                             logging.info("收到文件数据块...")
                         else:
                             logging.info(f"收到消息: {message[:50]}...")
                    except:
                        logging.info(f"收到消息: {message[:50]}...")
                else:
                    logging.info(f"收到消息: {message[:50]}...") # 避免日志过长
                
                if self.on_message_callback:
                    # 兼容同步和异步回调
                    import inspect
                    if inspect.iscoroutinefunction(self.on_message_callback):
                        await self.on_message_callback(message, websocket)
                    else:
                        res = self.on_message_callback(message, websocket)
                        if inspect.isawaitable(res):
                            await res
        except websockets.exceptions.ConnectionClosed:
            pass
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
        # ping_interval=None: 禁用服务端主动 Ping，避免在传输大量数据阻塞时因未及时 Ping 而断连
        # ping_timeout=None: 禁用超时检测
        # max_size=None: 取消单条消息大小限制 (默认是 1MB，传 Base64 图片很容易超)
        async with websockets.serve(
            self.handle_client, 
            self.host, 
            self.port,
            ping_interval=None, 
            ping_timeout=None,
            max_size=None 
        ):
            await asyncio.Future()  # run forever

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = WebSocketServer()
    asyncio.run(server.start())

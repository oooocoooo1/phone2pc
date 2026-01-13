import asyncio
import websockets

async def test_connection():
    uri = "ws://localhost:8765"
    try:
        async with websockets.connect(uri) as websocket:
            print(f"成功连接到 {uri}")
            await websocket.send("TEST_MSG")
            print("发送测试消息成功")
    except Exception as e:
        print(f"连接失败: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())

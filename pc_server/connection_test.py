import argparse
import asyncio
import json
import uuid

import websockets


async def test_connection(host, code):
    uri = f"ws://{host}:8765"
    device_id = f"connection-test-{uuid.uuid4()}"
    try:
        async with websockets.connect(uri, max_size=1024 * 1024) as websocket:
            challenge = json.loads(await asyncio.wait_for(websocket.recv(), timeout=5))
            if challenge.get("type") != "AUTH_REQUIRED":
                raise RuntimeError("服务端未返回鉴权请求")
            if not code:
                print(f"端口可访问，协议 v{challenge.get('protocol')}；请用 --code 输入 PC 配对码完成测试")
                return

            await websocket.send(json.dumps({
                "type": "PAIR",
                "device_id": device_id,
                "code": code,
            }))
            response = json.loads(await asyncio.wait_for(websocket.recv(), timeout=5))
            if response.get("type") != "PAIR_SUCCESS":
                raise RuntimeError(response.get("message", "配对失败"))
            print(f"连接和配对成功：{uri}，协议 v{response.get('protocol')}")
    except Exception as exc:
        print(f"连接失败: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phone2PC authenticated connection check")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--code", help="PC 窗口显示的 6 位配对码")
    args = parser.parse_args()
    asyncio.run(test_connection(args.host, args.code))

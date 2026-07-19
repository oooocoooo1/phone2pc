import webSocket from '@ohos.net.webSocket';
import promptAction from '@ohos.promptAction';

export class WebSocketService {
    private static instance: WebSocketService;
    private ws: webSocket.WebSocket | null = null;
    private isConnected: boolean = false;
    private onMessageCallback: (msg: string | ArrayBuffer) => void = () => { };
    private onStatusChange: (status: string, connected: boolean) => void = () => { };
    private reconnectTimer: number = -1;

    private constructor() { }

    public static getInstance(): WebSocketService {
        if (!WebSocketService.instance) {
            WebSocketService.instance = new WebSocketService();
        }
        return WebSocketService.instance;
    }

    public setCallbacks(
        onMessage: (msg: string | ArrayBuffer) => void,
        onStatus: (status: string, connected: boolean) => void
    ) {
        this.onMessageCallback = onMessage;
        this.onStatusChange = onStatus;
    }

    public connect(ip: string) {
        if (this.isConnected) return;

        this.ws = webSocket.createWebSocket();
        this.onStatusChange(`正在连接 ${ip}...`, false);

        this.ws.connect(`ws://${ip}:8765`, (err, value) => {
            if (!err) {
                this.isConnected = true;
                this.onStatusChange(`已连接到 ${ip}`, true);

                this.ws?.on('message', (err, value) => {
                    if (!err) {
                        this.onMessageCallback(value);
                    }
                });

                this.ws?.on('close', (err, value) => {
                    this.handleDisconnect("连接关闭");
                });

                this.ws?.on('error', (err) => {
                    this.handleDisconnect("连接错误");
                });
            } else {
                this.handleDisconnect(`连接失败: ${err.message}`);
            }
        });
    }

    public send(data: string | ArrayBuffer) {
        if (this.isConnected && this.ws) {
            this.ws.send(data, (err, value) => {
                if (err) {
                    // Send failed
                }
            });
        } else {
            promptAction.showToast({ message: '未连接' });
        }
    }

    public close() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.isConnected = false;
        this.onStatusChange("已断开", false);
    }

    private handleDisconnect(reason: string) {
        this.isConnected = false;
        this.ws = null;
        this.onStatusChange(reason, false);
    }
}

import ConnectionTab from './ConnectionTab';
import TextTab from './TextTab';
import { WebSocketService } from '../model/WebSocketService';
import { ClipboardManager } from '../model/ClipboardManager';
import promptAction from '@ohos.promptAction';

@Entry
@Component
struct Index {
  @State currentIndex: number = 0;
  @State isConnected: boolean = false;
  @State statusText: string = "未连接";

  private controller: TabsController = new TabsController();

  aboutToAppear() {
    // Init WebSocket Callbacks
    WebSocketService.getInstance().setCallbacks(
      (msg) => {
         this.handleMessage(msg);
      },
      (status, connected) => {
        this.statusText = status;
        this.isConnected = connected;
      }
    );

    // Init Clipboard Listener
    ClipboardManager.getInstance().startListening((text) => {
       if (this.isConnected) {
         const json = JSON.stringify({ type: "CLIPBOARD_SYNC", source: "PHONE", content: text });
         WebSocketService.getInstance().send(json);
       }
    });
  }

  handleMessage(msg: string | ArrayBuffer) {
    if (typeof msg === 'string') {
      try {
         const data = JSON.parse(msg);
         if (data.type === 'CLIPBOARD_SYNC' && data.source === 'PC') {
             ClipboardManager.getInstance().setSystemClipboard(data.content);
         }
      } catch (e) {
         // Plain text
      }
    }
  }

  build() {
    Column() {
      Tabs({ barPosition: BarPosition.End, controller: this.controller }) {
        TabContent() {
           TextTab()
        }
        .tabBar(this.TabBuilder(0, '输入', $r('app.media.icon')))

        TabContent() {
           ConnectionTab({ isConnected: $isConnected, statusText: $statusText })
        }
        .tabBar(this.TabBuilder(1, '连接', $r('app.media.icon')))
      }
      .vertical(false)
      .scrollable(false)
      .onChange((index: number) => {
        this.currentIndex = index;
      })
    }
    .width('100%')
    .height('100%')
  }

  @Builder TabBuilder(index: number, name: string, icon: Resource) {
    Column() {
      Text(name)
        .fontColor(this.currentIndex === index ? '#2196F3' : '#999')
        .fontSize(16)
        .margin({ top: 5 })
    }
    .width('100%')
    .height(50)
    .justifyContent(FlexAlign.Center)
  }
}

import { WebSocketService } from '../model/WebSocketService';
import promptAction from '@ohos.promptAction';

@Component
export default struct ConnectionTab {
  @Link isConnected: boolean;
  @Link statusText: string;
  @State ipInput: string = '';
  @State ipHistory: string[] = [];

  aboutToAppear() {
    this.startAutoConnect();
  }

  startAutoConnect() {
     // TODO: Load history from preferences and auto-connect
  }

  build() {
    Column() {
      // Status Bar
      Row() {
        Text(this.statusText)
          .fontColor(this.isConnected ? Color.White : Color.Black)
          .fontSize(16)
      }
      .width('100%')
      .padding(12)
      .backgroundColor(this.isConnected ? '#4CAF50' : '#FFCDD2')
      .justifyContent(FlexAlign.Center)

      // Input Area
      if (!this.isConnected) {
        Column() {
           TextInput({ placeholder: '输入 PC IP 地址', text: this.ipInput })
            .onChange((value: string) => {
              this.ipInput = value;
            })
            .width('80%')
            .height(50)
            .margin({ top: 20 })
            .backgroundColor(Color.White)
            .border({ width: 1, color: '#ccc', radius: 8 })

          Button('连接')
            .width('80%')
            .height(50)
            .margin({ top: 20 })
            .onClick(() => {
              if (this.ipInput.length > 0) {
                WebSocketService.getInstance().connect(this.ipInput);
                // Save history
              } else {
                 promptAction.showToast({ message: '请输入 IP' });
              }
            })
        }
        .width('100%')
        .padding(20)
      } else {
        // Connected View
        Column() {
           Icon({name: 'link', size: 60, color: '#2196F3'}) // Conceptual icon
           Text("已连接")
             .fontSize(24)
             .fontColor('#2196F3')
             .margin({ top: 10 })

           Button('断开连接')
             .backgroundColor(Color.Red)
             .margin({ top: 30 })
             .onClick(() => {
                WebSocketService.getInstance().close();
                this.isConnected = false;
             })
        }
        .width('100%')
        .height('100%')
        .justifyContent(FlexAlign.Center)
      }
    }
    .width('100%')
    .height('100%')
  }
}

// Mock Icon component if System Resource not ready
@Component
struct Icon {
  prop name: string;
  prop size: number;
  prop color: string;
  build() {
     Text("🔗") // Fallback
       .fontSize(this.size)
       .fontColor(this.color)
  }
}

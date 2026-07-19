import { WebSocketService } from '../model/WebSocketService';

@Component
export default struct TextTab {
  @State textInput: string = '';
  @State enterToSend: boolean = false;

  build() {
    Column() {
      TextArea({ placeholder: '在此输入文字发送给 PC...', text: this.textInput })
        .onChange((value: string) => {
          this.textInput = value;
          if (this.enterToSend && value.endsWith('\n')) {
             this.sendMessage();
          }
        })
        .height('40%')
        .width('90%')
        .margin({ top: 20 })
        .backgroundColor(Color.White)
        .border({ width: 1, color: '#ddd', radius: 8 })

      Row() {
        Toggle({ type: ToggleType.Switch, isOn: this.enterToSend })
          .onChange((isOn: boolean) => {
            this.enterToSend = isOn;
          })
        Text(" 回车发送")
          .fontSize(16)
          .margin({ left: 5 })

        Blank()

        Button('发送')
          .width(100)
          .onClick(() => {
            this.sendMessage();
          })
      }
      .width('90%')
      .margin({ top: 20 })
      .padding(10)
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#F5F5F5')
  }

  sendMessage() {
    if (this.textInput.trim().length > 0) {
       WebSocketService.getInstance().send(this.textInput);
       this.textInput = '';
    }
  }
}

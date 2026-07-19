import pasteboard from '@ohos.pasteboard';
import promptAction from '@ohos.promptAction';

export class ClipboardManager {
    private static instance: ClipboardManager;
    private lastContent: string = "";

    private constructor() { }

    public static getInstance(): ClipboardManager {
        if (!ClipboardManager.instance) {
            ClipboardManager.instance = new ClipboardManager();
        }
        return ClipboardManager.instance;
    }

    public async getSystemClipboard(): Promise<string> {
        try {
            const systemPasteboard = pasteboard.getSystemPasteboard();
            const data = await systemPasteboard.getData();
            if (data && data.getPrimaryText()) {
                return data.getPrimaryText();
            }
        } catch (e) {
            console.error('Failed to get clipboard', e);
        }
        return "";
    }

    public async setSystemClipboard(text: string) {
        try {
            if (text === this.lastContent) return;

            const systemPasteboard = pasteboard.getSystemPasteboard();
            const pasteData = pasteboard.createData(pasteboard.MIMETYPE_TEXT_PLAIN, text);
            await systemPasteboard.setData(pasteData);
            this.lastContent = text;

            promptAction.showToast({ message: '已复制到剪贴板' });
        } catch (e) {
            console.error('Failed to set clipboard', e);
        }
    }

    public startListening(onChange: (text: string) => void) {
        const systemPasteboard = pasteboard.getSystemPasteboard();
        systemPasteboard.on('update', async () => {
            const text = await this.getSystemClipboard();
            if (text && text !== this.lastContent) {
                this.lastContent = text;
                onChange(text);
            }
        });
    }
}

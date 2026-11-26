import { abilityAccessCtrl, Permissions } from '@kit.AbilityKit';
import { preferences } from '@kit.ArkData';
import { url } from '@kit.ArkTS';
import { webview } from '@kit.ArkWeb';

const DEFAULT_ORIGIN = 'https://eva-comm-utc-newly.trycloudflare.com';
const SERVER_KEY = 'server_origin';
const SURFACE = '#14171A';
const TEXT = '#F4F6F5';
const MUTED = '#AFB9B3';
const ACCENT = '#C9F59E';

function parseServerAddress(input: string): string {
  try {
    const parsed = url.URL.parseURL(input.trim());
    if (parsed.protocol === 'https:' && parsed.hostname && !parsed.username && !parsed.password &&
      parsed.pathname === '/' && !parsed.search && !parsed.hash) {
      return parsed.origin;
    }
  } catch {
    // The caller displays the input format when parsing fails.
  }
  return '';
}

@Entry
@Component
struct Index {
  @State private serverOrigin: string = DEFAULT_ORIGIN;
  @State private serverDraft: string = DEFAULT_ORIGIN;
  @State private editing: boolean = false;
  @State private addressError: string = '';
  @State private loading: boolean = true;
  @State private loadError: string = '';
  @State private permissionError: string = '';
  @State private showWeb: boolean = false;
  @State private fullscreen: boolean = false;
  private controller: webview.WebviewController = new webview.WebviewController();
  private fullscreenHandler?: FullScreenExitHandler;
  private store?: preferences.Preferences;
  private loadTimer: number = -1;
  private generation: number = 0;

  aboutToAppear(): void {
    this.restoreServer();
  }

  aboutToDisappear(): void {
    this.generation++;
    clearTimeout(this.loadTimer);
  }

  onBackPress(): boolean {
    if (this.editing) {
      this.editing = false;
      return true;
    }
    if (this.fullscreenHandler) {
      this.fullscreenHandler.exitFullScreen();
      return true;
    }
    return false;
  }

  private async restoreServer(): Promise<void> {
    const context = this.getUIContext().getHostContext();
    if (context) {
      try {
        this.store = await preferences.getPreferences(context, 'visual_call_settings');
        const saved = await this.store.get(SERVER_KEY, DEFAULT_ORIGIN);
        if (typeof saved === 'string') {
          this.serverOrigin = parseServerAddress(saved) || DEFAULT_ORIGIN;
        }
      } catch {
        // An unavailable preferences store does not prevent the current trial.
      }
    }
    this.connect();
  }

  private connect(): void {
    const generation = ++this.generation;
    this.loading = true;
    this.showWeb = false;
    this.loadError = '';
    this.permissionError = '';
    this.fullscreen = false;
    this.fullscreenHandler = undefined;
    clearTimeout(this.loadTimer);
    // Create a fresh Web component so retry also recovers a terminated renderer.
    setTimeout(() => {
      if (generation !== this.generation) return;
      this.controller = new webview.WebviewController();
      this.showWeb = true;
    }, 50);
    this.loadTimer = setTimeout(() => {
      if (generation === this.generation && this.loading) {
        this.loadingFailed('连接超时。请确认电脑上的试用服务和隧道正在运行。');
      }
    }, 18000);
  }

  private loadingFailed(message: string): void {
    clearTimeout(this.loadTimer);
    this.loading = false;
    this.loadError = message;
  }

  private editServer(): void {
    this.serverDraft = this.serverOrigin;
    this.addressError = '';
    this.editing = true;
  }

  private async saveServer(): Promise<void> {
    const next = parseServerAddress(this.serverDraft);
    if (!next) {
      this.addressError = '请输入试用服务的完整 HTTPS 地址。';
      return;
    }
    this.editing = false;
    if (next === this.serverOrigin && !this.loadError) return;
    this.serverOrigin = next;
    this.connect();
    try {
      if (this.store) {
        await this.store.put(SERVER_KEY, next);
        await this.store.flush();
      }
    } catch {
      this.permissionError = '当前地址已生效，关闭应用后可能需要再次填写。';
    }
  }

  private sameServer(address: string): boolean {
    try {
      const parsed = url.URL.parseURL(address);
      return parsed.origin === this.serverOrigin && !parsed.username && !parsed.password;
    } catch {
      return false;
    }
  }

  private async requestMedia(request: PermissionRequest): Promise<void> {
    if (!this.sameServer(request.getOrigin())) {
      request.deny();
      return;
    }
    const resources = request.getAccessibleResource();
    const permissions: Permissions[] = [];
    for (const resource of resources) {
      if (resource === ProtectedResourceType.AUDIO_CAPTURE) {
        permissions.push('ohos.permission.MICROPHONE');
      } else if (resource === ProtectedResourceType.VIDEO_CAPTURE) {
        permissions.push('ohos.permission.CAMERA');
      } else {
        request.deny();
        return;
      }
    }
    const context = this.getUIContext().getHostContext();
    if (!context || permissions.length === 0) {
      request.deny();
      return;
    }
    const generation = this.generation;
    let granted = false;
    try {
      const result = await abilityAccessCtrl.createAtManager().requestPermissionsFromUser(context, permissions);
      granted = result.authResults.length === permissions.length &&
        result.authResults.every(value => value === abilityAccessCtrl.GrantStatus.PERMISSION_GRANTED);
    } catch {
      granted = false;
    }
    if (generation !== this.generation) {
      request.deny();
      return;
    }
    if (granted) {
      this.permissionError = '';
      request.grant(resources);
    } else {
      request.deny();
      this.permissionError = '请在系统设置中允许麦克风或摄像头，然后在通话页重试。';
    }
  }

  @Builder
  private serverSettings(): void {
    Column({ space: 14 }) {
      Text('服务地址').fontSize(22).fontWeight(FontWeight.Medium).fontColor(TEXT)
      Text('填写发给你的 HTTPS 试用地址。更换地址会结束当前通话。').fontSize(14).fontColor(MUTED)
      TextInput({ text: this.serverDraft, placeholder: 'https://...' })
        .type(InputType.URL)
        .fontColor(TEXT)
        .backgroundColor('#272C2D')
        .onChange((value: string) => {
          this.serverDraft = value;
          this.addressError = '';
        })
      if (this.addressError) {
        Text(this.addressError).fontSize(13).fontColor('#FFC2AC')
      }
      Row({ space: 12 }) {
        Button('返回').backgroundColor('#343A3B').fontColor(TEXT).onClick(() => { this.editing = false; })
        Button('连接').backgroundColor(ACCENT).fontColor('#1B2415').onClick(() => { this.saveServer(); })
      }.justifyContent(FlexAlign.End).width('100%')
    }.padding(22).width('92%').constraintSize({ maxWidth: 460 }).borderRadius(18).backgroundColor('#212627')
  }

  build() {
    Stack() {
      Column() {
        if (!this.fullscreen) {
          Row() {
            Text('视觉通话试用').fontSize(17).fontWeight(FontWeight.Medium).fontColor(TEXT).layoutWeight(1)
            Button('服务地址')
              .fontSize(13)
              .height(34)
              .backgroundColor('#272C2D')
              .fontColor(ACCENT)
              .onClick(() => { this.editServer(); })
          }.padding({ left: 16, right: 16 }).height(52).width('100%')
        }
        if (this.permissionError) {
          Text(this.permissionError).fontSize(13).fontColor('#FFC2AC').padding(10).width('100%')
        }
        Stack() {
          if (this.showWeb) {
            Web({ src: this.serverOrigin + '/', controller: this.controller })
              .javaScriptAccess(true)
              .domStorageAccess(true)
              .fileAccess(false)
              .mixedMode(MixedMode.None)
              .mediaPlayGestureAccess(true)
              .onLoadIntercept((event) => {
                return event.data.isMainFrame() && !this.sameServer(event.data.getRequestUrl());
              })
              .onPermissionRequest((event) => { this.requestMedia(event.request); })
              .onPageEnd(() => {
                this.loading = false;
                clearTimeout(this.loadTimer);
              })
              .onErrorReceive((event) => {
                if (event.request.isMainFrame()) {
                  this.loadingFailed('无法打开试用页。请检查网络和 HTTPS 服务地址。');
                }
              })
              .onHttpErrorReceive((event) => {
                if (event.request.isMainFrame()) {
                  this.loadingFailed(`试用服务暂不可用（HTTP ${event.response.getResponseCode()}）。`);
                }
              })
              .onRenderExited(() => { this.loadingFailed('通话页面已关闭，可以重新连接。'); })
              .onFullScreenEnter((event) => {
                this.fullscreenHandler = event.handler;
                this.fullscreen = true;
              })
              .onFullScreenExit(() => {
                this.fullscreenHandler = undefined;
                this.fullscreen = false;
              })
              .width('100%').height('100%')
          }
          if (this.loading || this.loadError) {
            Column({ space: 16 }) {
              if (this.loading) LoadingProgress().width(32).height(32).color(ACCENT)
              Text(this.loading ? '正在连接试用服务…' : this.loadError)
                .fontSize(15).fontColor(TEXT).textAlign(TextAlign.Center)
              if (!this.loading) {
                Button('重试连接').backgroundColor(ACCENT).fontColor('#1B2415').onClick(() => { this.connect(); })
              }
            }.justifyContent(FlexAlign.Center).padding(28).width('100%').height('100%').backgroundColor(SURFACE)
          }
        }.layoutWeight(1).width('100%')
      }.width('100%').height('100%').backgroundColor(SURFACE)
      if (this.editing) {
        Column() { this.serverSettings() }.justifyContent(FlexAlign.Center)
          .width('100%').height('100%').backgroundColor('#C5090D0B')
      }
    }.width('100%').height('100%').backgroundColor(SURFACE)
  }
}

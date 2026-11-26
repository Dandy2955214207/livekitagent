import { UIAbility } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { hilog } from '@kit.PerformanceAnalysisKit';

export default class EntryAbility extends UIAbility {
  onWindowStageCreate(stage: window.WindowStage): void {
    try {
      stage.loadContent('pages/Index').catch(() => this.reportLoadError());
    } catch {
      this.reportLoadError();
    }
  }

  private reportLoadError(): void {
    hilog.error(0x0000, 'VisualCallDemo', 'Unable to load the entry page');
  }
}

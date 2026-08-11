"use strict";

export class PollingController {
  constructor(refresh) {
    this.refresh = refresh;
    this.timer = null;
    this.inFlight = false;
    this.intervalMs = 2000;
    this.onVisibility = () => this.sync();
    document.addEventListener("visibilitychange", this.onVisibility);
  }

  configure({ enabled, intervalMs = 2000 }) {
    this.enabled = Boolean(enabled);
    this.intervalMs = Math.max(1000, intervalMs);
    this.sync();
  }

  sync() {
    this.stop();
    if (!this.enabled || document.hidden) return;
    this.timer = window.setTimeout(() => this.tick(), this.intervalMs);
  }

  async tick() {
    if (!this.enabled || document.hidden || this.inFlight) return this.sync();
    this.inFlight = true;
    try {
      await this.refresh({ quiet: true });
    } catch {
      // 后续写操作或手动重试会显示可处理的持久错误。
    } finally {
      this.inFlight = false;
      this.sync();
    }
  }

  stop() {
    if (this.timer) window.clearTimeout(this.timer);
    this.timer = null;
  }

  destroy() {
    this.stop();
    document.removeEventListener("visibilitychange", this.onVisibility);
  }
}

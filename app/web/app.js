"use strict";

import { strategicApi } from "./api.js";
import { PollingController } from "./polling.js";
import {
  clearError,
  renderSnapshot,
  setBusy,
  showError,
  toast,
  ui,
} from "./render.js";

const STORAGE_KEY = "journey.strategicSessionId";

const state = {
  sessionId: localStorage.getItem(STORAGE_KEY) || "",
  snapshot: null,
  trace: false,
  hidden: false,
  busy: false,
  refreshSequence: 0,
};

const polling = new PollingController(refreshSnapshot);

async function bootstrap() {
  try {
    const health = await strategicApi.health();
    ui.healthDot.classList.toggle("online", health.status === "ok");
    if (!state.sessionId) {
      await resetScenario({ automatic: true });
      return;
    }
    await refreshSnapshot();
  } catch (error) {
    if (error.status === 404 && state.sessionId) {
      localStorage.removeItem(STORAGE_KEY);
      state.sessionId = "";
      await resetScenario({ automatic: true });
      return;
    }
    showError(error, "无法打开战略控制台");
  }
}

async function refreshSnapshot({ quiet = false } = {}) {
  if (!state.sessionId) return;
  const sequence = ++state.refreshSequence;
  try {
    const snapshot = await strategicApi.snapshot(state.sessionId, {
      trace: state.trace,
      hidden: state.hidden,
    });
    if (sequence !== state.refreshSequence) return;
    state.snapshot = snapshot;
    renderSnapshot(snapshot, state);
    clearError();
    polling.configure({
      enabled: snapshot.polling?.recommended && !state.busy,
      intervalMs: snapshot.polling?.interval_ms || 2000,
    });
  } catch (error) {
    if (!quiet) showError(error, "状态快照同步失败");
    throw error;
  }
}

async function withWrite(label, operation, successMessage) {
  if (state.busy) return;
  state.busy = true;
  polling.stop();
  clearError();
  setBusy(true, label);
  try {
    await operation();
    await refreshSnapshot();
    toast(successMessage);
  } catch (error) {
    showError(error);
    toast(error.message || "操作失败", true);
    try {
      await refreshSnapshot({ quiet: true });
    } catch {
      // The persistent error banner already explains the actionable failure.
    }
  } finally {
    state.busy = false;
    setBusy(false);
    if (state.snapshot) {
      renderSnapshot(state.snapshot, state);
      polling.configure({
        enabled: state.snapshot.polling?.recommended,
        intervalMs: state.snapshot.polling?.interval_ms || 2000,
      });
    }
  }
}

async function resetScenario({ automatic = false } = {}) {
  if (
    !automatic &&
    state.snapshot?.task &&
    !["SUCCEEDED", "FAILED", "BLOCKED"].includes(state.snapshot.task.status) &&
    !window.confirm("当前军令尚未结束。确定建立一个新的星火战略场景吗？")
  ) {
    return;
  }
  await withWrite(
    "正在建立全新的战略场景…",
    async () => {
      const result = await strategicApi.reset();
      state.sessionId = result.session_id;
      state.snapshot = null;
      state.trace = false;
      state.hidden = false;
      localStorage.setItem(STORAGE_KEY, state.sessionId);
    },
    "新的星火战略场景已经建立。"
  );
}

async function issueCommand(event) {
  event.preventDefault();
  const command = ui.commandInput.value.trim();
  if (!command || !state.sessionId) return;
  await withWrite(
    "沈策正在制定方案，部下将执行到下一个安全暂停点…",
    () => strategicApi.command(state.sessionId, command),
    "军令已送达，行动推进到下一个等待点。"
  );
}

async function resolveDecision(optionId) {
  const task = state.snapshot?.task;
  const decision = state.snapshot?.active_decision;
  if (!task || !decision || !state.sessionId) return;
  await withWrite(
    "正在记录主公决断并恢复执行…",
    () => strategicApi.decision(task.id, decision.id, state.sessionId, optionId),
    `决断“${optionId === "APPROVE" ? "批准" : "拒绝"}”已记录，部下已继续执行。`
  );
}

async function resolveWorldEvent() {
  const task = state.snapshot?.task;
  const operation = state.snapshot?.pending_world_event;
  if (!task || !operation || !state.sessionId) return;
  await withWrite(
    "游戏规则服务正在确定性结算，并恢复后续方案…",
    () => strategicApi.worldEvent(task.id, operation.id, state.sessionId),
    "世界事件已结算，军令已推进到下一个等待点。"
  );
}

async function toggleTrace() {
  state.trace = !state.trace;
  try {
    await refreshSnapshot();
  } catch {
    state.trace = !state.trace;
  }
}

async function toggleHidden() {
  state.hidden = !state.hidden;
  ui.toggleHidden.textContent = state.hidden ? "隐藏世界真相" : "显示隐藏世界真相";
  try {
    await refreshSnapshot();
  } catch {
    state.hidden = !state.hidden;
    ui.toggleHidden.textContent = state.hidden ? "隐藏世界真相" : "显示隐藏世界真相";
  }
}

ui.commandForm.addEventListener("submit", issueCommand);
ui.resetScenario.addEventListener("click", () => resetScenario());
ui.resolveWorldEvent.addEventListener("click", resolveWorldEvent);
ui.toggleTrace.addEventListener("click", toggleTrace);
ui.toggleHidden.addEventListener("click", toggleHidden);
ui.retryButton.addEventListener("click", () => {
  if (state.sessionId) refreshSnapshot();
  else resetScenario({ automatic: true });
});
ui.decisionOptions.addEventListener("click", (event) => {
  const button = event.target.closest("[data-option-id]");
  if (button && !button.disabled) resolveDecision(button.dataset.optionId);
});
ui.commandInput.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    ui.commandForm.requestSubmit();
  }
});

bootstrap();

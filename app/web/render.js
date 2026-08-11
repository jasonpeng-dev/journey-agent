"use strict";

export const ui = {
  healthDot: document.querySelector("#health-dot"),
  runtimeLabel: document.querySelector("#runtime-label"),
  providerBadge: document.querySelector("#provider-badge"),
  commandOwner: document.querySelector("#command-owner"),
  snapshotVersion: document.querySelector("#snapshot-version"),
  headlineStatus: document.querySelector("#headline-status"),
  resourceGrid: document.querySelector("#resource-grid"),
  worldFacts: document.querySelector("#world-facts"),
  officerRoster: document.querySelector("#officer-roster"),
  hiddenTruthPanel: document.querySelector("#hidden-truth-panel"),
  hiddenTruthContent: document.querySelector("#hidden-truth-content"),
  sessionBadge: document.querySelector("#session-badge"),
  timeline: document.querySelector("#timeline"),
  commandForm: document.querySelector("#command-form"),
  commandInput: document.querySelector("#command-input"),
  issueCommand: document.querySelector("#issue-command"),
  planBadge: document.querySelector("#plan-badge"),
  taskSummary: document.querySelector("#task-summary"),
  waitingCard: document.querySelector("#waiting-card"),
  planHistory: document.querySelector("#plan-history"),
  decisionPanel: document.querySelector("#decision-panel"),
  decisionSummary: document.querySelector("#decision-summary"),
  decisionOfficer: document.querySelector("#decision-officer"),
  decisionTool: document.querySelector("#decision-tool"),
  decisionArgs: document.querySelector("#decision-args"),
  decisionOptions: document.querySelector("#decision-options"),
  resolveWorldEvent: document.querySelector("#resolve-world-event"),
  resetScenario: document.querySelector("#reset-scenario"),
  toggleTrace: document.querySelector("#toggle-trace"),
  toggleHidden: document.querySelector("#toggle-hidden"),
  tracePanel: document.querySelector("#trace-panel"),
  traceList: document.querySelector("#trace-list"),
  rawSnapshot: document.querySelector("#raw-snapshot"),
  errorBanner: document.querySelector("#error-banner"),
  errorTitle: document.querySelector("#error-title"),
  errorMessage: document.querySelector("#error-message"),
  retryButton: document.querySelector("#retry-button"),
  busyOverlay: document.querySelector("#busy-overlay"),
  busyLabel: document.querySelector("#busy-label"),
  toast: document.querySelector("#toast"),
};

const labels = {
  ACTIVE: "执行中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  BLOCKED: "受阻",
  SUPERSEDED: "已替换",
  PENDING: "待执行",
  IN_PROGRESS: "执行中",
  REQUIRES_PLAYER_DECISION: "等待主公决断",
  WAITING_FOR_PLAYER_ACTION: "等待玩家行动",
  WAITING_FOR_WORLD_EVENT: "等待世界结算",
  UNKNOWN: "未知",
  INCOMPLETE: "未完成",
  PARTIAL: "部分掌握",
  COMPLETE: "已掌握",
  UNSAFE: "不安全",
  SAFE: "安全",
  DISCOVERED: "已发现",
  CLEARED: "已肃清",
  NONE: "无",
  GUIDE: "向导支援",
  SUPPLIES: "物资支援",
  DAMAGED: "受损",
  OPERATIONAL: "可运作",
  RESTORED: "已修复",
  CLOSED: "关闭",
  OPEN: "开放",
  DISRUPTED: "已切断",
  APPROVED: "已批准",
  REJECTED: "已拒绝",
  RESOLVED: "已结算",
  COMPLETED: "已完成",
  PASSED: "通过",
  VALID: "有效",
  INVALID: "无效",
  ALLOW: "允许",
  DENY: "拒绝",
  MEDIUM: "中等",
  LOW: "低",
  HIGH: "高",
  TOOL: "工具执行",
  WAIT_FOR_WORLD_EVENT: "等待世界事件",
  WAIT_FOR_PLAYER_ACTION: "等待玩家行动",
  WAIT_FOR_USER: "等待用户",
  PLAN: "制定方案",
  REPLAN: "重新制定方案",
  STEP: "执行步骤",
  WAIT_CHECK: "等待条件检查",
  "N/A": "不适用",
  FINAL_RESPONSE: "正常结束",
  SECURITY_REJECTION: "安全策略拒绝",
  INTERNAL_ERROR: "内部错误",
  MODEL_TIMEOUT: "模型超时",
  PROVIDER_ERROR: "模型服务错误",
  RECORDED: "已记录",
  SKIPPED: "已跳过",
  CANCELLED: "已取消",
  VICTORY: "胜利",
  DEFEAT: "失败",
  PARTIAL_SUCCESS: "部分成功",
};

const toolLabels = {
  inspect_command_state: "核验战略状态",
  start_recon_operation: "发起侦察行动",
  start_military_operation: "发起军事行动",
  negotiate_village_support: "协商村落支援",
  start_outpost_repair: "启动前哨修复",
  start_trade_route_test: "启动商路测试",
  create_task_plan: "创建军令方案",
  replan_task: "重新制定军令方案",
};

const operationLabels = {
  RECONNAISSANCE: "侦察行动",
  MILITARY: "军事行动",
  CONSTRUCTION: "建设行动",
  TRADE_TEST: "商路测试",
};

const targetLabels = {
  valley_entrance: "山谷入口",
  ambush_valley: "伏击谷",
  enemy_north_supply_route: "敌军北方补给线",
  starfire_outpost: "星火前哨",
  northern_trade_route: "北方商路",
};

const factDefinitions = [
  ["village_relation", "北境村落", "合作与支援"],
  ["valley_intelligence", "山谷情报", "侦察掌握程度"],
  ["valley_security", "山谷安全", "军事控制状态"],
  ["ambush_status", "伏击威胁", "仅显示已知情报"],
  ["enemy_supply_route", "敌军补给线", "未知时不泄露真相"],
  ["starfire_outpost_status", "星火前哨", "修复与运作状态"],
  ["northern_trade_route_status", "北方商路", "最终战略目标"],
];

export function renderSnapshot(snapshot, flags) {
  ui.toggleHidden.textContent = flags.hidden ? "隐藏世界真相" : "显示隐藏世界真相";
  renderHeader(snapshot);
  renderResources(snapshot.resources || {});
  renderWorldFacts(snapshot.known_world_state || {});
  renderOfficers(snapshot.officers || []);
  renderHiddenTruth(snapshot.hidden_world_truth, flags.hidden);
  renderTimeline(snapshot.timeline || []);
  renderTask(snapshot.task, snapshot.plan_history || []);
  renderWaiting(snapshot);
  renderDecision(snapshot.active_decision);
  renderTrace(snapshot.recent_traces || [], snapshot, flags.trace);
  syncCapabilities(snapshot.capabilities || {});
}

function renderHeader(snapshot) {
  const officer = snapshot.session?.commanding_officer;
  ui.commandOwner.textContent = officer
    ? `${officerName(officer)} · ${roleName(officer.role)}`
    : "沈策 · 军师";
  ui.sessionBadge.textContent = officer ? `${officerName(officer)}会话` : "沈策会话";
  ui.snapshotVersion.textContent = snapshot.snapshot_version || "—";
  const status = snapshot.task?.status || "READY";
  ui.headlineStatus.textContent = status === "READY" ? "等待军令" : label(status);
  ui.headlineStatus.className = `status-pill ${statusClass(status)}`;
  ui.providerBadge.textContent = `${providerName(snapshot.runtime?.provider)} · ${
    modelName(snapshot.runtime?.model)
  }`;
  ui.runtimeLabel.textContent = `${environmentName(snapshot.runtime?.environment)} · API 正常`;
  ui.healthDot.classList.add("online");
}

function renderResources(resources) {
  const soldiersAvailable = resources.soldiers_available ?? "—";
  const resourceDefinitions = [
    ["兵力", soldiersAvailable, `总兵力 ${resources.soldiers_total ?? "—"}`, "兵"],
    ["粮草", resources.food ?? "—", "军政共用储备", "粮"],
    ["金库", resources.gold ?? "—", "建设与商路资金", "金"],
    ["士气", resources.morale ?? "—", "0—100", "气"],
  ];
  ui.resourceGrid.replaceChildren(
    ...resourceDefinitions.map(([name, value, caption, mark]) => {
      const card = el("article", "resource-card");
      const icon = el("span", "resource-mark", mark);
      const copy = el("div");
      copy.append(el("span", "resource-name", name), el("strong", "", String(value)));
      copy.appendChild(el("small", "", caption));
      card.append(icon, copy);
      return card;
    })
  );
}

function renderWorldFacts(facts) {
  ui.worldFacts.replaceChildren(
    ...factDefinitions.map(([key, title, caption]) => {
      const row = el("div", "fact-row");
      const copy = el("div");
      copy.append(el("strong", "", title), el("span", "", caption));
      const value = String(facts[key] ?? "UNKNOWN");
      row.append(copy, el("span", `fact-value ${statusClass(value)}`, factLabel(key, value)));
      return row;
    })
  );
}

function renderOfficers(officers) {
  if (!officers.length) {
    ui.officerRoster.replaceChildren(el("p", "empty-copy", "尚未载入部下编制。"));
    return;
  }
  ui.officerRoster.replaceChildren(
    ...officers.map((officer) => {
      const card = el("details", "officer-card");
      const summary = el("summary");
      const avatar = el(
        "span",
        `officer-avatar role-${String(officer.role).toLowerCase()}`,
        officer.name.slice(0, 1)
      );
      const identity = el("div", "officer-identity");
      identity.append(
        el("strong", "", officerName(officer)),
        el("span", "", roleName(officer.role))
      );
      summary.append(
        avatar,
        identity,
        el("span", `policy-dot ${officer.authority_policy_status === "VALID" ? "pass" : "fail"}`)
      );
      const details = el("div", "officer-details");
      details.append(
        keyValue("决策倾向", localizeObject(officer.doctrine || {})),
        keyValue("自主权限", localizeObject(officer.authority_limits || {})),
        keyValue("可用工具", (officer.permissions || []).map(toolName))
      );
      card.append(summary, details);
      return card;
    })
  );
}

function renderHiddenTruth(truth, visible) {
  ui.hiddenTruthPanel.hidden = !visible;
  if (!visible) return;
  ui.hiddenTruthContent.replaceChildren(
    truth
      ? jsonBlock(localizeObject(truth))
      : el("p", "empty-copy", "服务端没有返回隐藏世界真相。")
  );
}

function renderTimeline(items) {
  if (!items.length) {
    const welcome = el("div", "welcome-card");
    welcome.append(
      el("span", "welcome-mark", "策"),
      (() => {
        const copy = el("div");
        copy.append(
          el("strong", "", "议事厅已经就绪"),
          el("p", "", "向沈策下达高层目标，部下将在各自权限范围内行动。")
        );
        return copy;
      })()
    );
    ui.timeline.replaceChildren(welcome);
    return;
  }
  ui.timeline.replaceChildren(
    ...items.map((item) => {
      const actor = item.actor || {};
      const row = el("article", `timeline-item kind-${slug(item.kind)}`);
      const marker = el("div", "timeline-marker", markerText(item.kind, actor));
      const body = el("div", "timeline-body");
      const meta = el("div", "timeline-meta");
      meta.append(
        el("strong", "", officerName(actor)),
        el("span", "", timelineKind(item.kind)),
        el("time", "", formatTime(item.created_at))
      );
      body.append(meta, el("p", "timeline-content", translateText(item.content) || "状态已更新。"));
      const chips = el("div", "timeline-chips");
      if (item.plan_version) chips.appendChild(el("span", "mini-chip", `方案 v${item.plan_version}`));
      if (item.step_sequence) chips.appendChild(el("span", "mini-chip", `步骤 ${item.step_sequence}`));
      if (item.status) {
        chips.appendChild(el("span", `mini-chip ${statusClass(item.status)}`, label(item.status)));
      }
      if (item.failure_code) {
        chips.appendChild(el("code", "failure-chip", failureName(item.failure_code)));
      }
      if (chips.childElementCount) body.appendChild(chips);
      if (item.result) {
        const result = el("details", "result-details");
        result.append(
          el("summary", "", "查看确定性结果"),
          jsonBlock(localizeObject(item.result))
        );
        body.appendChild(result);
      }
      row.append(marker, body);
      return row;
    })
  );
  ui.timeline.scrollTop = ui.timeline.scrollHeight;
}

function renderTask(task, plans) {
  if (!task) {
    ui.planBadge.textContent = "方案 —";
    ui.taskSummary.replaceChildren(el("p", "empty-copy", "等待玩家下达第一道军令。"));
    ui.planHistory.replaceChildren(el("p", "empty-copy", "尚无行动方案。"));
    return;
  }
  ui.planBadge.textContent = `方案 v${task.current_plan_version}`;
  const title = el("div", "task-title");
  title.append(
    el("span", `task-icon ${statusClass(task.status)}`, "令"),
    (() => {
      const copy = el("div");
      copy.append(
        el("strong", "", translateText(task.goal_description)),
        el("span", "", `军令 ${shortId(task.id)}`)
      );
      return copy;
    })()
  );
  const stats = el("div", "task-stats");
  [
    ["状态", label(task.status)],
    ["当前方案", `v${task.current_plan_version}`],
    ["调整次数", String(task.replan_count)],
    ["统筹者", officerName(task.owner_officer)],
  ].forEach(([name, value]) => {
    const stat = el("div");
    stat.append(el("span", "", name), el("strong", "", value));
    stats.appendChild(stat);
  });
  ui.taskSummary.replaceChildren(title, stats);

  if (!plans.length) {
    ui.planHistory.replaceChildren(el("p", "empty-copy", "规划尚未通过后端验证。"));
    return;
  }
  ui.planHistory.replaceChildren(
    ...[...plans].reverse().map((plan) => {
      const details = el("details", "plan-card");
      details.open = plan.version === task.current_plan_version;
      const summary = el("summary");
      const version = el("div", "plan-version");
      version.append(
        el("span", "plan-number", `v${plan.version}`),
        (() => {
          const copy = el("div");
          copy.append(
            el("strong", "", `方案 v${plan.version}`),
            el("span", "", officerName(plan.created_by_officer))
          );
          return copy;
        })()
      );
      summary.append(
        version,
        el("span", `plan-status ${statusClass(plan.status)}`, label(plan.status))
      );
      const body = el("div", "plan-body");
      body.appendChild(el("p", "strategy-copy", translateText(plan.strategy_summary) || "—"));
      if (plan.replan_reason) {
        const reason = el("div", "replan-reason");
        reason.append(
          el("span", "", "调整原因"),
          el("code", "", failureName(plan.replan_reason))
        );
        body.appendChild(reason);
      }
      const validation = el("div", "validation-line");
      validation.append(
        el("span", "", "方案校验"),
        el(
          "strong",
          plan.validation_status === "PASSED" ? "pass-text" : "fail-text",
          label(plan.validation_status || "N/A")
        )
      );
      body.appendChild(validation);
      const steps = el("div", "step-list");
      (plan.steps || []).forEach((step) => steps.appendChild(renderStep(step)));
      body.appendChild(steps);
      details.append(summary, body);
      return details;
    })
  );
}

function renderStep(step) {
  const row = el("div", `step-row ${statusClass(step.status)}`);
  const sequence = el("span", "step-sequence", String(step.sequence).padStart(2, "0"));
  const body = el("div", "step-copy");
  body.append(el("strong", "", translateText(step.description)));
  const meta = el("div", "step-meta");
  meta.append(
    el("span", "officer-chip", officerName(step.assigned_officer, "未分配")),
    el("span", "", label(step.execution_type)),
    el("span", "", step.selected_tool_name ? toolName(step.selected_tool_name) : "等待条件")
  );
  body.appendChild(meta);
  if (step.failure_code) {
    body.appendChild(el("code", "failure-chip", failureName(step.failure_code)));
  }
  row.append(sequence, body, el("span", "step-status", label(step.status)));
  return row;
}

function renderWaiting(snapshot) {
  const task = snapshot.task;
  if (!task) {
    ui.waitingCard.replaceChildren();
    return;
  }
  let title = "";
  let copy = "";
  let tone = "info";
  if (snapshot.active_decision) {
    title = "等待主公决断";
    copy = "越权或重大选择已经冻结，任何资源尚未消耗。";
    tone = "decision";
  } else if (snapshot.pending_player_action) {
    title = "等待玩家亲自行动";
    copy = snapshot.pending_player_action.description || "请在游戏系统完成指定操作。";
    tone = "player";
  } else if (snapshot.pending_world_event) {
    title = "等待游戏规则服务结算";
    copy = operationName(snapshot.pending_world_event);
    tone = "world";
  } else if (task.status === "ACTIVE") {
    title = "部下正在执行";
    copy = "后端正在推进当前行动方案，页面会按服务端建议刷新。";
  } else {
    ui.waitingCard.replaceChildren();
    return;
  }
  const card = el("div", `waiting-card ${tone}`);
  card.append(el("strong", "", title), el("p", "", copy));
  ui.waitingCard.replaceChildren(card);
}

function renderDecision(decision) {
  ui.decisionPanel.hidden = !decision;
  if (!decision) {
    ui.decisionOptions.replaceChildren();
    return;
  }
  ui.decisionSummary.textContent =
    translateText(decision.summary) || "该行动需要主公授权。";
  ui.decisionOfficer.textContent = officerName(decision.requested_by_officer, "未知部下");
  ui.decisionTool.textContent = decision.action_tool_name
    ? `${toolName(decision.action_tool_name)}（${decision.action_tool_name}）`
    : "—";
  ui.decisionArgs.textContent = JSON.stringify(
    localizeObject(decision.action_arguments || {}),
    null,
    2
  );
  ui.decisionOptions.replaceChildren(
    ...(decision.options || []).map((option) => {
      const card = el("article", "decision-option");
      const copy = el("div");
      const optionText = decisionOptionLabel(option);
      copy.append(
        el("strong", "", optionText),
        el("p", "", translateText(option.summary || decision.summary))
      );
      const risk = el("div", "risk-row");
      risk.append(
        metricChip("代价", compact(localizeObject(option.expected_cost ?? "—"))),
        metricChip("风险", label(option.expected_risk || "—")),
        metricChip("不可逆", option.irreversible ? "是" : "否")
      );
      copy.appendChild(risk);
      const button = el(
        "button",
        `button ${option.id === "APPROVE" ? "primary" : "secondary"}`,
        optionText
      );
      button.type = "button";
      button.dataset.optionId = option.id;
      button.dataset.write = "";
      card.append(copy, button);
      return card;
    })
  );
}

function renderTrace(runs, snapshot, visible) {
  ui.tracePanel.hidden = !visible;
  ui.toggleTrace.textContent = visible ? "收起开发者审计" : "展开开发者审计";
  ui.rawSnapshot.textContent = JSON.stringify(snapshot, null, 2);
  if (!visible) return;
  if (!runs.length) {
    ui.traceList.replaceChildren(el("p", "empty-copy", "当前军令尚无智能体运行记录。"));
    return;
  }
  ui.traceList.replaceChildren(
    ...[...runs].reverse().map((run) => {
      const details = el("details", "trace-run");
      const summary = el("summary");
      const title = el("div");
      title.append(
        el("strong", "", `${officerName(run.actor, "系统")} · ${label(run.purpose)}`),
        el("span", "", `${run.model} · ${run.token_usage} 令牌`)
      );
      summary.append(title, el("span", `trace-status ${statusClass(run.status)}`, label(run.status)));
      const body = el("div", "trace-body");
      body.append(
        traceMeta("智能体运行 ID", run.id),
        traceMeta("执行角色 ID", run.actor_npc_id || "—"),
        traceMeta("终止原因", label(run.termination_reason || "—")),
        traceMeta("方案校验", label(run.plan_validation?.status || "N/A"))
      );
      if (run.plan_validation?.errors?.length) {
        body.appendChild(keyValue("校验错误", localizeObject(run.plan_validation.errors)));
      }
      body.appendChild(
        keyValue("模型响应摘要", localizeObject(run.provider_response_summary || {}))
      );
      (run.tools || []).forEach((tool) => body.appendChild(renderToolTrace(tool)));
      const raw = el("details", "nested-raw");
      raw.append(el("summary", "", "模型原始数据"), jsonBlock(run.raw || {}));
      body.appendChild(raw);
      details.append(summary, body);
      return details;
    })
  );
}

function renderToolTrace(tool) {
  const card = el("article", "tool-trace");
  const header = el("div", "tool-trace-header");
  header.append(
    el("code", "", `${toolName(tool.tool_name)}（${tool.tool_name}）`),
    el("span", "", `${tool.duration_ms ?? 0} ms`)
  );
  const checks = el("div", "trace-checks");
  [
    ["参数校验", tool.validation],
    ["权限校验", tool.authority],
    ["业务规则", tool.business_rule],
    ["执行结果", tool.execution],
  ].forEach(([name, value]) => {
    const check = el("div");
    check.append(el("span", "", name), el("strong", statusClass(value), label(value)));
    checks.appendChild(check);
  });
  const data = el("div", "trace-data");
  data.append(
    keyValue("调用参数", localizeObject(tool.arguments || {})),
    keyValue("权限详情", localizeObject(tool.authority_details || {})),
    keyValue("执行前状态", localizeObject(tool.before_state || {})),
    keyValue("执行后状态 / 结果", localizeObject(tool.after_state ?? tool.result ?? {}))
  );
  card.append(header, checks);
  if (tool.failure_code) {
    card.appendChild(el("code", "failure-chip", failureName(tool.failure_code)));
  }
  card.appendChild(data);
  return card;
}

function syncCapabilities(capabilities) {
  ui.commandInput.disabled = !capabilities.can_issue_command;
  ui.issueCommand.disabled = !capabilities.can_issue_command;
  ui.resolveWorldEvent.disabled = !capabilities.can_resolve_world_event;
}

export function setBusy(busy, labelText = "正在同步战略状态…") {
  ui.busyOverlay.hidden = !busy;
  ui.busyLabel.textContent = labelText;
  document.querySelectorAll("[data-write]").forEach((button) => {
    button.disabled = busy || button.dataset.capabilityDisabled === "true";
  });
}

export function showError(error, title = "操作未完成") {
  ui.errorTitle.textContent = error.code ? `${title} · ${error.code}` : title;
  ui.errorMessage.textContent = `${error.message || String(error)}${
    error.requestId ? `（请求 ${error.requestId}）` : ""
  }`;
  ui.errorBanner.hidden = false;
}

export function clearError() {
  ui.errorBanner.hidden = true;
  ui.errorMessage.textContent = "";
}

export function toast(message, isError = false) {
  window.clearTimeout(toast.timer);
  ui.toast.textContent = message;
  ui.toast.className = `toast show${isError ? " error" : ""}`;
  toast.timer = window.setTimeout(() => {
    ui.toast.className = "toast";
  }, 3600);
}

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null && text !== "") node.textContent = String(text);
  return node;
}

function jsonBlock(value) {
  const pre = el("pre", "json-block");
  pre.textContent = JSON.stringify(value, null, 2);
  return pre;
}

function keyValue(name, value) {
  const details = el("details", "key-value");
  details.append(el("summary", "", name), jsonBlock(value));
  return details;
}

function traceMeta(name, value) {
  const row = el("div", "trace-meta");
  row.append(el("span", "", name), el("code", "", String(value)));
  return row;
}

function metricChip(name, value) {
  const chip = el("span", "risk-chip");
  chip.append(el("small", "", name), el("strong", "", String(value)));
  return chip;
}

function label(value) {
  return labels[String(value)] || String(value || "—").replaceAll("_", " ");
}

function factLabel(key, value) {
  if (key === "enemy_supply_route" && value === "ACTIVE") return "仍在运作";
  return label(value);
}

function decisionOptionLabel(option) {
  return {
    APPROVE: "批准此项行动",
    REJECT: "拒绝并要求调整方案",
    "Approve this exact action": "批准此项精确行动",
    "Reject and request replanning": "拒绝并要求重新制定方案",
  }[option.label] || {
    APPROVE: "批准此项行动",
    REJECT: "拒绝并要求调整方案",
  }[option.id] || option.label || option.id;
}

function statusClass(value) {
  const normalized = slug(value);
  if (["succeeded", "safe", "open", "operational", "restored", "resolved", "approved", "passed", "valid", "cleared", "complete", "disrupted"].includes(normalized)) return "success";
  if (["failed", "blocked", "unsafe", "rejected", "invalid", "denied"].includes(normalized)) return "danger";
  if (["requires-player-decision", "waiting-for-player-action", "waiting-for-world-event", "pending", "partial", "discovered", "active"].includes(normalized)) return "warning";
  return "neutral";
}

function roleName(role) {
  return {
    STRATEGIST: "军师 · 规划统筹",
    GENERAL: "武将 · 军事执行",
    STEWARD: "内政官 · 资源建设",
  }[role] || role;
}

function timelineKind(kind) {
  return {
    PLAYER_COMMAND: "玩家军令",
    STRATEGIST_REPORT: "军师回复",
    PLAN_CREATED: "提出方案",
    REPLAN: "调整方案",
    OFFICER_ACTION: "行动汇报",
    FINAL_REPORT: "最终汇报",
    DECISION_REQUEST: "请示主公",
    WORLD_EVENT: "世界结算",
  }[kind] || String(kind || "记录").replaceAll("_", " ");
}

function markerText(kind, actor) {
  if (kind === "WORLD_EVENT") return "世";
  if (kind === "PLAYER_COMMAND") return "主";
  if (kind === "DECISION_REQUEST") return "决";
  return officerName(actor, "录").slice(0, 1);
}

function compact(value) {
  if (typeof value === "string" || typeof value === "number") return String(value);
  return JSON.stringify(value);
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function shortId(value) {
  return value ? String(value).slice(0, 8) : "—";
}

function slug(value) {
  return String(value || "neutral").toLowerCase().replaceAll("_", "-");
}

function officerName(officer, fallback = "沈策") {
  if (!officer) return fallback;
  return {
    shen_ce: "沈策",
    han_lie: "韩烈",
    lu_ning: "陆宁",
    game_service: "游戏规则服务",
    player: "主公",
  }[officer.key] || officer.name || fallback;
}

function providerName(provider) {
  return {
    mock: "模拟模型",
    openai_compatible: "兼容模型接口",
  }[provider] || provider || "模型";
}

function environmentName(environment) {
  return {
    development: "开发环境",
    test: "测试环境",
    production: "生产环境",
  }[environment] || environment || "开发环境";
}

function modelName(model) {
  return {
    "mock-model": "模拟模型",
  }[model] || model || "模型";
}

function toolName(name) {
  return toolLabels[name] || name || "未知工具";
}

function operationName(operation) {
  return `${operationLabels[operation.operation_type] || operation.operation_type} · ${
    targetLabels[operation.target_key] || operation.target_key
  }`;
}

function failureName(code) {
  return {
    ENCOUNTER_DEFEAT: "战斗失败（ENCOUNTER_DEFEAT）",
    PLAYER_DECISION_REJECTED: "玩家拒绝方案（PLAYER_DECISION_REJECTED）",
    WORLD_STATE_CHANGED: "世界状态已变化（WORLD_STATE_CHANGED）",
    REPLAN_LIMIT_REACHED: "已达到方案调整上限（REPLAN_LIMIT_REACHED）",
  }[code] || code || "—";
}

const textTranslations = {
  "Restore Starfire Outpost and reopen the northern trade route.":
    "修复星火前哨，并重新打通北方商路。",
  "Shen Ce assesses domain resources and public Starfire facts":
    "沈策核验领地资源和星火前哨公开情报",
  "Han Lie starts cautious reconnaissance at the valley entrance":
    "韩烈在山谷入口发起谨慎侦察",
  "Han Lie waits for verified reconnaissance results":
    "韩烈等待侦察结果由游戏世界结算",
  "Han Lie starts a bounded operation to clear Ambush Valley":
    "韩烈发起有限兵力行动清剿伏击谷",
  "Han Lie waits for the valley-clearance operation to resolve":
    "韩烈等待伏击谷清剿行动结算",
  "Lu Ning starts a temporary repair after the valley is safe":
    "山谷安全后，陆宁启动星火前哨临时修复",
  "Lu Ning waits for construction completion": "陆宁等待前哨建设完成",
  "Lu Ning starts a northern trade-route test": "陆宁启动北方商路通行测试",
  "Lu Ning waits for the trade-route test to complete": "陆宁等待北方商路测试结算",
  "Lu Ning offers food for village guides without using coercion":
    "陆宁在不使用强制手段的前提下，用粮草换取村落向导支援",
  "Han Lie starts a limited operation against the enemy supply route":
    "韩烈发起有限兵力行动切断敌军补给线",
  "Han Lie waits for the supply-disruption operation": "韩烈等待补给线破袭行动结算",
  "Han Lie retries valley clearance after enemy supplies are disrupted":
    "敌军补给被切断后，韩烈再次清剿伏击谷",
  "Han Lie waits for verified valley security": "韩烈等待游戏世界确认山谷安全",
  "Lu Ning starts a resource-bounded temporary outpost repair":
    "陆宁按资源限额启动星火前哨临时修复",
  "Lu Ning starts a verified northern trade-route test":
    "陆宁启动经过前置条件核验的北方商路测试",
  "Lu Ning waits for the trade route to reopen": "陆宁等待北方商路重新开放",
  "Shen Ce verifies the restored corridor and reports the command result to the lord":
    "沈策核验恢复后的安全通道，并向主公汇报军令结果",
  "Shen Ce coordinates verified reconnaissance, a bounded valley-clearance operation, repair by Lu Ning, and a deterministic northern trade-route test.":
    "沈策先核验领地资源与公开情报，再由韩烈侦察并清剿山谷，山谷安全后交由陆宁修复前哨并进行北方商路通行测试。",
  "Shen Ce uses the newly discovered supply route, obtains village guidance, orders Han Lie to disrupt supplies before a second clearance operation, then hands the safe corridor to Lu Ning for repair and trade recovery.":
    "沈策根据新发现的敌军补给线调整方案：先由陆宁争取村落向导，再命韩烈切断补给并重新清剿山谷，最后将安全通道交由陆宁修复前哨并恢复商路。",
};

function translateText(value) {
  if (!value) return "";
  const text = String(value);
  if (textTranslations[text]) return textTranslations[text];
  const operation = text.match(/^(RECONNAISSANCE|MILITARY|CONSTRUCTION|TRADE_TEST): (.+)$/);
  if (operation) {
    return `${operationLabels[operation[1]] || operation[1]}：${
      targetLabels[operation[2]] || operation[2]
    }`;
  }
  const authority = text.match(
    /^Lu Ning requests (\d+) for food_offer; the autonomous limit is (\d+)\.$/
  );
  if (authority) {
    return `陆宁请求使用 ${authority[1]} 粮草，超过自主权限上限 ${authority[2]}。`;
  }
  return text;
}

function localizeObject(value) {
  if (Array.isArray(value)) return value.map(localizeObject);
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === null || value === undefined) return "无";
  if (typeof value !== "object") {
    const raw = String(value);
    const valueLabels = {
      DEVELOPER_ONLY: "仅开发者可见",
      ACTIVE: "活跃",
      DEFEAT_UNTIL_SUPPLY_DISRUPTED: "补给线切断前，首次清剿必然失败",
      DETERMINED_BY_GAME_SERVICE: "由游戏规则服务确定",
      INTELLIGENCE: "情报优先",
      LOW_CASUALTIES: "减少伤亡",
      COORDINATION: "协同配合",
      MOMENTUM: "保持攻势",
      MORALE: "重视士气",
      DECISIVE_ACTION: "果断行动",
      RESOURCE_EFFICIENCY: "资源效率",
      PUBLIC_SUPPORT: "民众支持",
      LONG_TERM_TRADE: "长期商贸",
      CAUTIOUS: "谨慎",
      STANDARD: "稳健",
      AGGRESSIVE: "激进",
      TEMPORARY: "临时修复",
      FULL: "全面修复",
      CLEAR_VALLEY: "清剿山谷",
      DISRUPT_SUPPLY: "切断补给",
      ESCORT: "护送",
      DEFEND: "防守",
      "Tool executed successfully": "工具执行成功",
      "Concurrent state change was rejected": "并发状态变更已被拒绝",
      "Tool execution failed safely": "工具执行已安全停止",
      "The exact action requires the player's decision before execution":
        "执行该精确行动前需要主公决断",
      valley_intelligence: "山谷情报",
      valley_security: "山谷安全",
      village_support: "村落支援",
      enemy_supply_route: "敌军补给线",
      starfire_outpost_status: "星火前哨状态",
      northern_trade_route_status: "北方商路状态",
    };
    return valueLabels[raw] || toolLabels[raw] || operationLabels[raw] ||
      targetLabels[raw] || label(raw);
  }
  const keyLabels = {
    classification: "数据级别",
    ambush_status: "伏击状态",
    enemy_supply_route: "敌军补给线",
    resolution_rules: "结算规则",
    first_clear_attempt: "第一次清剿",
    world_outcomes: "世界结果来源",
    max_troops: "最大自主投入兵力",
    max_food: "最大自主粮草投入",
    max_gold: "最大自主金钱投入",
    max_intelligence_gold: "最大自主情报金钱投入",
    risk_preference: "风险偏好",
    priorities: "优先事项",
    troop_count: "投入兵力",
    food_offer: "粮草提议",
    gold_cost: "金钱投入",
    gold_budget: "金钱预算",
    target_key: "目标",
    operation_type: "行动类型",
    operation_id: "行动编号",
    operation_status: "行动状态",
    status: "状态",
    mission_type: "任务类型",
    outcome: "结算结果",
    result: "结果",
    success: "是否成功",
    ok: "是否成功",
    code: "结果代码",
    message: "说明",
    retryable: "是否可重试",
    data: "数据",
    casualties: "伤亡",
    facts_changed: "已改变事实",
    facts_discovered: "已发现事实",
    invalidated_prerequisites: "已失效前置条件",
    resources: "资源",
    world: "世界状态",
    soldiers_total: "总兵力",
    soldiers_available: "可用兵力",
    soldiers_committed: "已投入兵力",
    food: "粮草",
    gold: "金钱",
    morale: "士气",
    version: "版本",
    village_support: "村落支援",
    village_relation: "村落关系",
    valley_intelligence: "山谷情报",
    valley_security: "山谷安全",
    starfire_outpost_status: "星火前哨状态",
    northern_trade_route_status: "北方商路状态",
    previous_status: "原状态",
    current_status: "当前状态",
    authority_policy_version: "权限策略版本",
    authority_limit: "自主权限上限",
    requested: "请求值",
    limit: "权限上限",
    field: "参数",
    violations: "越权项",
    risk_flags: "风险标记",
    rounds: "总轮次",
    model_rounds: "模型轮次",
    structured_output_present: "包含结构化输出",
    force_allowed: "允许使用强制手段",
    use_force: "使用强制手段",
    report_scope: "汇报范围",
  };
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      keyLabels[key] || key,
      localizeObject(item),
    ])
  );
}

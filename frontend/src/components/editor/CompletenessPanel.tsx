import type { CompletenessItem, CompletenessResult, InitializationPreview, ValidationResult } from "../../types";
import type { JsonObject } from "../../editor";
import { completenessLocatorHref, findingsForObject } from "./completeness-navigation";
import { AuthoringActionButton, FieldActionRow } from "./AuthoringActionButton";
import { projectAuthoringIssues, type AuthoringIssue } from "./authoring-issues";

type Props = { result: CompletenessResult; scenarioId: string; onOwnerNavigate?: (item: CompletenessItem) => void };

function levelLabel(level: CompletenessItem["level"]): string {
  if (level === "INCOMPLETE_REQUIRED") return "必须处理";
  if (level === "VALID_BUT_UNCONFIGURED") return "待配置";
  if (level === "OPTIONAL_ENHANCEMENT") return "推荐完善";
  if (level === "LEGACY_FALLBACK") return "兼容与回退提示";
  return "已完成";
}

function levelIcon(level: CompletenessItem["level"]): string {
  if (level === "INCOMPLETE_REQUIRED") return "⚠";
  if (level === "VALID_BUT_UNCONFIGURED") return "○";
  if (level === "OPTIONAL_ENHANCEMENT") return "·";
  if (level === "LEGACY_FALLBACK") return "↳";
  return "✓";
}

function GuidanceItem({ item, scenarioId, onOwnerNavigate }: { item: CompletenessItem; scenarioId: string; onOwnerNavigate?: (item: CompletenessItem) => void }) {
  const href = completenessLocatorHref(item.locator, scenarioId);
  const nodeTypeOwnerHref = /^node:[^:]+:node-type$/.test(item.key) ? `/scenarios/${scenarioId}/edit/node-types` : null;
  const canNavigateToOwner = item.action === "CREATE" && onOwnerNavigate && !nodeTypeOwnerHref;
  return <li className={`completeness-item completeness-${item.level.toLowerCase()}`}>
    <div className="completeness-item-icon" aria-hidden="true">{levelIcon(item.level)}</div>
    <div className="completeness-item-content"><strong>{item.title}</strong><span>{item.message}</span><small>{levelLabel(item.level)}</small></div>
    {nodeTypeOwnerHref
      ? <FieldActionRow><AuthoringActionButton className="completeness-item-link" intent="navigate" to={nodeTypeOwnerHref}>前往节点类型</AuthoringActionButton></FieldActionRow>
      : canNavigateToOwner
      ? <FieldActionRow><AuthoringActionButton className="completeness-item-link" intent="navigate" onPress={() => onOwnerNavigate(item)}>前往所属对象</AuthoringActionButton></FieldActionRow>
      : href && item.action !== "NONE" && <FieldActionRow><AuthoringActionButton className="completeness-item-link" intent="navigate" to={href}>前往</AuthoringActionButton></FieldActionRow>}
  </li>;
}

function activeItems(result: CompletenessResult): CompletenessItem[] {
  return result.items.filter((item) => item.level !== "COMPLETE");
}

export function CompletenessSummary({ result, scenarioId }: Pick<Props, "result" | "scenarioId">) {
  const items = activeItems(result);
  const blockers = items.filter((item) => item.level === "INCOMPLETE_REQUIRED").length;
  const pending = items.filter((item) => item.level === "VALID_BUT_UNCONFIGURED").length;
  const recommendations = items.filter((item) => item.level === "OPTIONAL_ENHANCEMENT").length;
  return <section className="completeness-summary-card" aria-label="配置检查摘要">
    <div><strong>{blockers ? `${blockers} 项必须处理` : "没有必须处理项"}</strong><span>待配置 {pending} · 推荐完善 {recommendations}</span></div>
    <FieldActionRow><AuthoringActionButton intent="navigate" to={`/scenarios/${scenarioId}/edit/configuration-check`}>查看配置检查</AuthoringActionButton></FieldActionRow>
  </section>;
}

export function ContextualCompleteness({ result, scenarioId, kind, objectKey, onOwnerNavigate }: Props & { kind: string; objectKey: string }) {
  const items = findingsForObject(result, kind, objectKey).filter((item) => !(kind === "node" && item.key === `node:${objectKey}:node-type`));
  if (items.length === 0) return null;
  return <section className="completeness-context" aria-label="当前对象配置提示">
    <h4>当前对象配置提示 <span>{items.length}</span></h4>
    <ul>{items.map((item) => <GuidanceItem item={item} scenarioId={scenarioId} onOwnerNavigate={onOwnerNavigate} key={item.key} />)}</ul>
  </section>;
}

function issueSourceLabel(source: AuthoringIssue["sources"][number]): string {
  if (source === "completeness") return "配置完整度";
  if (source === "reference") return "引用检查";
  if (source === "validation") return "结构与发布检查";
  return "开局预览";
}

function GlobalIssueRow({ issue }: { issue: AuthoringIssue }) {
  return <article className={`global-issue ${issue.severity.toLowerCase()}`}>
    <div className="global-issue-content">
      <strong>{issue.title}</strong>
      <p>{issue.message}</p>
      <small>{issue.severity === "ERROR" ? "必须处理" : "推荐完善"}</small>
      <small>{[...new Set(issue.sources.map(issueSourceLabel))].join(" · ")}</small>
      {!issue.href && <small className="global-issue-unlocated">此项暂时无法精确定位，请检查相关场景配置。</small>}
    </div>
    {(issue.href || issue.ownerHref) && <FieldActionRow className="global-issue-actions">
      {issue.href && issue.actionLabel && <AuthoringActionButton intent="navigate" to={issue.href}>{issue.actionLabel}</AuthoringActionButton>}
      {issue.ownerHref && issue.ownerActionLabel && <AuthoringActionButton intent="navigate" to={issue.ownerHref}>{issue.ownerActionLabel}</AuthoringActionButton>}
    </FieldActionRow>}
  </article>;
}

export function CompletenessPanel({ result, scenarioId, document, initializationPreview, initializationError, currentValidation }: Props & {
  document: JsonObject;
  initializationPreview?: InitializationPreview | null;
  initializationError?: unknown;
  currentValidation?: ValidationResult | null;
}) {
  const issues = projectAuthoringIssues({ result, scenarioId, document, initializationPreview, initializationError, currentValidation });
  const domains = [...new Set(issues.map((issue) => issue.domain))];
  const blockers = issues.filter((issue) => issue.severity === "ERROR").length;
  const warnings = issues.length - blockers;
  return <section className="completeness-panel" aria-label="配置检查">
    <header className="completeness-heading"><div><p className="panel-kicker">当前工作副本</p><h3>配置检查</h3><p className="muted">按概念域汇总可操作提示；检查不会修改草稿。</p></div></header>
    <div className="global-issue-summary" aria-live="polite"><strong>{issues.length === 0 ? "当前工作副本没有待处理的配置提示。" : `当前工作副本有 ${issues.length} 项待处理配置`}</strong>{issues.length > 0 && <span>阻止检查或发布 {blockers} · 提醒 {warnings}</span>}</div>
    {issues.length === 0 && <p className="completeness-complete">✓ 当前工作副本没有待处理的配置提示。</p>}
    {domains.map((domain) => {
      const domainItems = issues.filter((item) => item.domain === domain);
      return <details className="completeness-domain" open key={domain}><summary>{domain}<span>{domainItems.length}</span></summary>
        <div className="global-issue-list">{domainItems.map((issue) => <GlobalIssueRow issue={issue} key={issue.id} />)}</div>
      </details>;
    })}
  </section>;
}

import type { CompletenessItem, CompletenessResult } from "../../types";
import { completenessLocatorHref, findingsForObject } from "./completeness-navigation";

type Props = { result: CompletenessResult; scenarioId: string; onCreate?: (item: CompletenessItem) => void };

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

function findingDomain(item: CompletenessItem): string {
  const kind = item.locator?.object_kind ?? "";
  if (["node", "node_type", "relation", "relation_type", "resource"].includes(kind)) return "世界模型";
  if (["role", "actor"].includes(kind)) return "参与者";
  if (["interaction", "action", "rule"].includes(kind) || kind.startsWith("action_")) return "行为系统";
  if (["derived_state", "goal_resolution"].includes(kind)) return "目标系统";
  if (kind === "planning") return "规划策略";
  if (kind === "initialization") return "初始化";
  if (["public_knowledge", "public_reference"].includes(kind)) return "公开信息";
  if (kind === "metadata") return "概览";
  return "场景结构";
}

function GuidanceItem({ item, scenarioId, onCreate }: { item: CompletenessItem; scenarioId: string; onCreate?: (item: CompletenessItem) => void }) {
  const href = completenessLocatorHref(item.locator, scenarioId);
  const nodeTypeOwnerHref = /^node:[^:]+:node-type$/.test(item.key) ? `/scenarios/${scenarioId}/edit/node-types` : null;
  const canCreate = item.action === "CREATE" && onCreate && !nodeTypeOwnerHref;
  return <li className={`completeness-item completeness-${item.level.toLowerCase()}`}>
    <div className="completeness-item-icon" aria-hidden="true">{levelIcon(item.level)}</div>
    <div className="completeness-item-content"><strong>{item.title}</strong><span>{item.message}</span><small>{levelLabel(item.level)}</small></div>
    {nodeTypeOwnerHref
      ? <a className="editor-button editor-button-secondary completeness-item-link" href={nodeTypeOwnerHref}>前往节点类型</a>
      : canCreate
      ? <button type="button" className="editor-button editor-button-secondary completeness-item-link" onClick={() => onCreate(item)}>创建并关联</button>
      : href && item.action !== "NONE" && <a className="editor-button editor-button-secondary completeness-item-link" href={href}>前往</a>}
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
    <a className="editor-button editor-button-secondary" href={`/scenarios/${scenarioId}/edit/configuration-check`}>查看配置检查</a>
  </section>;
}

export function ContextualCompleteness({ result, scenarioId, kind, objectKey, onCreate }: Props & { kind: string; objectKey: string }) {
  const items = findingsForObject(result, kind, objectKey).filter((item) => !(kind === "node" && item.key === `node:${objectKey}:node-type`));
  if (items.length === 0) return null;
  return <section className="completeness-context" aria-label="当前对象配置提示">
    <h4>当前对象配置提示 <span>{items.length}</span></h4>
    <ul>{items.map((item) => <GuidanceItem item={item} scenarioId={scenarioId} onCreate={onCreate} key={item.key} />)}</ul>
  </section>;
}

export function CompletenessPanel({ result, scenarioId, onCreate }: Props) {
  const items = activeItems(result);
  const domains = [...new Set(items.map(findingDomain))];
  return <section className="completeness-panel" aria-label="配置检查">
    <header className="completeness-heading"><div><p className="panel-kicker">当前工作副本</p><h3>配置检查</h3><p className="muted">按概念域汇总可操作提示；检查不会修改草稿。</p></div></header>
    {items.length === 0 && <p className="completeness-complete">✓ 当前工作副本没有待处理的配置提示。</p>}
    {domains.map((domain) => {
      const domainItems = items.filter((item) => findingDomain(item) === domain);
      const levels = ["INCOMPLETE_REQUIRED", "VALID_BUT_UNCONFIGURED", "OPTIONAL_ENHANCEMENT", "LEGACY_FALLBACK"] as const;
      return <details className="completeness-domain" open key={domain}><summary>{domain}<span>{domainItems.length}</span></summary>
        {levels.map((level) => {
          const levelItems = domainItems.filter((item) => item.level === level);
          return levelItems.length > 0 && <section className="completeness-group" key={level}><h4>{levelLabel(level)}<span>{levelItems.length}</span></h4><ul>{levelItems.map((item) => <GuidanceItem item={item} scenarioId={scenarioId} onCreate={onCreate} key={item.key} />)}</ul></section>;
        })}
      </details>;
    })}
    {result.validation_issue_count > 0 && <p className="completeness-validation-note">当前工作副本另有 {result.validation_issue_count} 项结构或发布校验问题；请在“验证与发布”中查看。</p>}
  </section>;
}

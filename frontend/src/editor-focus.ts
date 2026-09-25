import { useEffect, useState } from "react";

import { objectByKindAndKey, type EntityKind, type JsonObject } from "./editor";

type RecordValue = Record<string, unknown>;
const entityKinds = new Set<EntityKind>([
  "node_type", "node", "relation_type", "relation", "resource", "role", "actor",
  "interaction", "action", "rule", "derived_state", "public_reference",
]);

function record(value: unknown): RecordValue | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : null;
}

function rowIdentity(collection: string, row: RecordValue): string | null {
  const direct: Record<string, string> = {
    facts: "key", parameters: "key", expected_outcomes: "code", operation_bindings: "role",
    doctrine: "key", autonomous_limits: "parameter_key", approval_required_values: "parameter_key",
    nodes: "key", node_types: "key", relation_types: "key", relations: "key", resources: "key",
    roles: "key", actor_profiles: "key", actions: "key", interactions: "key", rules: "key",
    derived_states: "key", public_references: "term", resource_pools: "pool_key",
    recovery_hints: "failure_code", region_resource_knowledge: "region_key",
  };
  if (collection === "target_actor_roles") {
    return typeof row.target_key === "string" && typeof row.required_actor_role_key === "string"
      ? `${row.target_key}:${row.required_actor_role_key}` : null;
  }
  if (collection === "resource_initial_states") {
    return typeof row.resource_key === "string"
      ? `${row.resource_key}:${typeof row.scope_node_key === "string" ? row.scope_node_key : "global"}` : null;
  }
  if (collection === "relations") {
    if (typeof row.key === "string" && row.key) return row.key;
    if (typeof row.source_node_key === "string" && typeof row.relation_type_key === "string" && typeof row.target_node_key === "string") {
      return [row.source_node_key, row.relation_type_key, row.target_node_key].join("__");
    }
    return null;
  }
  if (collection === "dependencies") {
    const kind = typeof row.kind === "string" ? row.kind : "";
    if (kind === "FACT" && typeof row.node_key === "string" && typeof row.fact_key === "string") return `FACT:${row.node_key}:${row.fact_key}`;
    if (kind === "RESOURCE_AT_LEAST" && typeof row.region_key === "string" && typeof row.resource_key === "string") return `RESOURCE_AT_LEAST:${row.region_key}:${row.resource_key}`;
    if (kind === "DERIVED_STATE" && typeof row.derived_key === "string") return `DERIVED_STATE:${row.derived_key}`;
    return null;
  }
  const field = direct[collection];
  const value = field ? row[field] : null;
  return typeof value === "string" && value ? value : null;
}

/** Translate rendered array paths to stable authored identities when one exists. */
export function stableEditorFocusPath(path: string, document: JsonObject): string {
  const parts = path.split(".").filter(Boolean);
  if (parts.length === 0) return path;
  let current: unknown = document;
  let offset = 0;
  const maybeKind = parts[0] as EntityKind;
  if (entityKinds.has(maybeKind) && parts.length > 1) {
    const entity = objectByKindAndKey(document, maybeKind, parts[1]);
    if (entity) {
      current = entity.value;
      offset = 2;
    }
  }

  const stable: string[] = parts.slice(0, offset);
  for (let index = offset; index < parts.length; index += 1) {
    const part = parts[index];
    if (Array.isArray(current) && /^\d+$/.test(part)) {
      const row = record(current[Number(part)]);
      const identity = row ? rowIdentity(stable.at(-1) ?? "", row) : null;
      stable.push(identity ?? part);
      current = current[Number(part)];
      continue;
    }
    stable.push(part);
    current = record(current)?.[part];
  }
  return stable.join(".");
}

function pathMatches(candidate: string, requested: string, document: JsonObject): boolean {
  const candidateStable = stableEditorFocusPath(candidate, document);
  const requestedStable = stableEditorFocusPath(requested, document);
  return candidateStable === requestedStable || candidateStable.endsWith(`.${requestedStable}`);
}

function focusElement(element: HTMLElement, highlightMs: number): void {
  const toggle = element.matches(".nested-editor")
    ? element.querySelector<HTMLButtonElement>(".nested-card-toggle[aria-expanded='false']")
    : element.closest<HTMLElement>(".nested-editor")?.querySelector<HTMLButtonElement>(".nested-card-toggle[aria-expanded='false']");
  if (toggle) toggle.click();
  for (let parent = element.parentElement; parent; parent = parent.parentElement) {
    if (parent instanceof HTMLDetailsElement) parent.open = true;
  }
  const target = element.matches("input,select,textarea,button")
    ? element
    : element.matches(".nested-list")
      ? element
      : element.querySelector<HTMLElement>("input:not([type=hidden]),select,textarea")
        ?? element.querySelector<HTMLElement>("button")
        ?? element;
  if (!target.hasAttribute("tabindex") && !target.matches("input,select,textarea,button")) target.tabIndex = -1;
  target.focus();
  target.scrollIntoView?.({ block: "center", behavior: "smooth" });
  element.classList.add("is-focus-highlighted");
  window.setTimeout(() => element.classList.remove("is-focus-highlighted"), highlightMs);
}

export type FocusActivationResult = "activated" | "expanded" | "missing";

export function activateEditorFocusTarget(
  root: ParentNode,
  path: string,
  document: JsonObject = {},
  highlightMs = 1800,
): FocusActivationResult {
  const candidates = Array.from(root.querySelectorAll<HTMLElement>("[data-focus-path], [data-field-path]"));
  const readPath = (element: HTMLElement) => element.dataset.focusPath ?? element.dataset.fieldPath ?? "";
  const exact = candidates.find((element) => readPath(element) && pathMatches(readPath(element), path, document));
  if (exact) {
    const isCollapsedCard = exact.matches(".nested-editor")
      && Boolean(exact.querySelector(".nested-card-toggle[aria-expanded='false']"));
    focusElement(exact, highlightMs);
    return isCollapsedCard ? "expanded" : "activated";
  }

  const requested = stableEditorFocusPath(path, document);
  const ancestors = candidates
    .map((element) => ({ element, path: readPath(element), stable: stableEditorFocusPath(readPath(element), document) }))
    .filter((item) => item.stable && requested.startsWith(`${item.stable}.`))
    .sort((left, right) => right.stable.length - left.stable.length);
  const collapsed = ancestors.find(({ element }) => element.matches(".nested-editor")
    && Boolean(element.querySelector(".nested-card-toggle[aria-expanded='false']")));
  if (collapsed) {
    focusElement(collapsed.element, highlightMs);
    return "expanded";
  }
  return "missing";
}

/** Shared route/query focus activation for all editor renderers. */
export function useEditorFocusActivation(
  path: string | null,
  trigger: string,
  document: JsonObject,
  enabled = true,
  scopeSelector = "[data-editor-focus-scope]",
): "" | "stale" {
  const [status, setStatus] = useState<"" | "stale">("");
  useEffect(() => {
    if (!enabled || !path) {
      setStatus("");
      return undefined;
    }
    setStatus("");
    let attempts = 0;
    let timer = 0;
    let cancelled = false;
    const activate = () => {
      if (cancelled) return;
      const result = activateEditorFocusTarget(window.document, path, document);
      if (result === "activated") return;
      attempts += 1;
      if (attempts < 12) {
        timer = window.setTimeout(activate, result === "expanded" ? 35 : 50);
        return;
      }
      const fallback = window.document.querySelector<HTMLElement>(scopeSelector);
      if (fallback) {
        fallback.tabIndex = -1;
        fallback.focus();
        fallback.scrollIntoView?.({ block: "start", behavior: "smooth" });
        setStatus("stale");
      }
    };
    timer = window.setTimeout(activate, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [document, enabled, path, scopeSelector, trigger]);
  return status;
}

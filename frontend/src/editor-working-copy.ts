import type { Draft } from "./types";

export type WorkingCopySaveState = "UNCHANGED" | "DIRTY" | "SAVING" | "CONFLICT" | "ERROR";

export function cloneWorkingDocument(document: Record<string, unknown>): Record<string, unknown> {
  return structuredClone(document) as Record<string, unknown>;
}

export function workingDocumentsEqual(left: Record<string, unknown> | null, right: Record<string, unknown> | null): boolean {
  if (left === right) return true;
  if (!left || !right) return false;
  return JSON.stringify(left) === JSON.stringify(right);
}

export function workingCopyIsDirty(serverDraft: Draft | null, workingDocument: Record<string, unknown> | null): boolean {
  return Boolean(serverDraft && workingDocument && !workingDocumentsEqual(serverDraft.definition_document, workingDocument));
}

export function deriveWorkingCopySaveState(
  serverDraft: Draft | null,
  workingDocument: Record<string, unknown> | null,
): Extract<WorkingCopySaveState, "UNCHANGED" | "DIRTY"> {
  return workingCopyIsDirty(serverDraft, workingDocument) ? "DIRTY" : "UNCHANGED";
}

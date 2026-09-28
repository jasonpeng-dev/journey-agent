import { describe, expect, it } from "vitest";

import { cloneWorkingDocument, deriveWorkingCopySaveState, workingCopyIsDirty, workingDocumentsEqual } from "./editor-working-copy";
import type { Draft } from "./types";

const draft = { definition_document: { metadata: { name: "Original" } } } as unknown as Draft;

describe("editor working-copy lifecycle helpers", () => {
  it("clones documents before local editing", () => {
    const copy = cloneWorkingDocument(draft.definition_document);
    (copy.metadata as Record<string, unknown>).name = "Edited";
    expect(draft.definition_document).toEqual({ metadata: { name: "Original" } });
  });

  it("distinguishes unchanged and dirty working copies", () => {
    expect(workingDocumentsEqual(draft.definition_document, { metadata: { name: "Original" } })).toBe(true);
    expect(workingCopyIsDirty(draft, { metadata: { name: "Edited" } })).toBe(true);
    expect(deriveWorkingCopySaveState(draft, draft.definition_document)).toBe("UNCHANGED");
    expect(deriveWorkingCopySaveState(draft, { metadata: { name: "Edited" } })).toBe("DIRTY");
  });

  it("treats a missing server baseline as not saveable", () => {
    expect(workingCopyIsDirty(null, { metadata: { name: "Edited" } })).toBe(false);
    expect(deriveWorkingCopySaveState(null, null)).toBe("UNCHANGED");
  });
});

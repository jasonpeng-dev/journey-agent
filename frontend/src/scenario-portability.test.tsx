import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { api, ApiError } from "./api";
import { ScenarioArtifactImportDialog } from "./components/ScenarioArtifactImportDialog";
import type { NewScenarioArtifactPreview, ScenarioArtifactImportResult } from "./types";

const artifact = { artifact_type: "journey_scenario", artifact_version: 1, content_type: "draft", schema_version: 3, scenario: { key: "portable_source", name: "Portable source", description: "" }, definition: {} };
const draftPreview: NewScenarioArtifactPreview = {
  artifact: { artifact_type: "journey_scenario", artifact_version: 1, content_type: "draft", schema_version: 3, scenario: artifact.scenario, content_hash: "a".repeat(64) },
  candidate_target_key: "portable_source",
  key_conflict: false,
  content_hash: "a".repeat(64),
  validation: { structurally_valid: true, publish_ready: false, issue_count: 0, issues: [] },
  what_import_will_create: { records: ["Scenario", "Draft"], published_version_number: null, game_created: false },
};
const importResult: ScenarioArtifactImportResult = {
  status: "IMPORTED",
  artifact: { artifact_type: "journey_scenario", artifact_version: 1, content_type: "draft", schema_version: 3, content_hash: "a".repeat(64) },
  scenario: { id: "scenario-imported", key: "portable_source", name: "Portable source", status: "DRAFT" },
  draft: { revision: 1, validation_status: "INVALID" },
  published_version: null,
  game_created: false,
};

function renderDialog(onCancel = vi.fn()) {
  return render(<MemoryRouter><ScenarioArtifactImportDialog onCancel={onCancel} onImported={vi.fn()} /></MemoryRouter>);
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Scenario artifact import UX", () => {
  it("previews and imports a Draft as a new Scenario without a published version", async () => {
    vi.spyOn(api, "previewNewArtifact").mockResolvedValue(draftPreview);
    vi.spyOn(api, "importNewArtifact").mockResolvedValue(importResult);
    const onImported = vi.fn();
    render(<MemoryRouter><ScenarioArtifactImportDialog onCancel={vi.fn()} onImported={onImported} /></MemoryRouter>);
    const file = new File([JSON.stringify(artifact)], "portable.draft.scenario.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("选择 Scenario artifact"), { target: { files: [file] } });
    await screen.findByText("Portable source");
    expect(screen.getByText("将创建新的场景和可编辑草稿。")).toBeVisible();
    expect(screen.queryByText(/Published Version v1/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(importResult));
  });

  it("always exposes a target key and never offers existing-Draft import", async () => {
    const conflict = { ...draftPreview, key_conflict: true };
    vi.spyOn(api, "previewNewArtifact").mockResolvedValueOnce(conflict).mockResolvedValueOnce(draftPreview);
    renderDialog();
    const file = new File([JSON.stringify(artifact)], "portable.scenario.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("选择 Scenario artifact"), { target: { files: [file] } });
    await screen.findByText("该场景 key 已存在。导入始终创建新的场景，请指定一个未使用的 key。");
    expect(screen.getByLabelText("目标 Scenario key")).toBeVisible();
    expect(screen.queryByText(/导入到已有场景草稿/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("new_scenario_key"), { target: { value: "portable_target" } });
    fireEvent.click(screen.getByRole("button", { name: "检查目标 key" }));
    await waitFor(() => expect(api.previewNewArtifact).toHaveBeenLastCalledWith(artifact, "portable_target"));
  });

  it("keeps confirmation disabled while the edited target key has not been re-previewed", async () => {
    vi.spyOn(api, "previewNewArtifact").mockResolvedValue(draftPreview);
    renderDialog();
    const file = new File([JSON.stringify(artifact)], "portable.scenario.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("选择 Scenario artifact"), { target: { files: [file] } });
    await screen.findByText("Portable source");
    fireEvent.change(screen.getByLabelText("目标 Scenario key"), { target: { value: "another_key" } });
    expect(screen.getByRole("button", { name: "确认导入" })).toBeDisabled();
  });

  it("presents typed artifact errors without raw validation paths", async () => {
    vi.spyOn(api, "previewNewArtifact").mockRejectedValue(new ApiError("bad", 422, "ARTIFACT_INTEGRITY_MISMATCH", { errors: [{ path: "definition.world", message: "raw" }] }));
    renderDialog();
    const file = new File([JSON.stringify(artifact)], "portable.scenario.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("选择 Scenario artifact"), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("文件内容校验失败"));
    expect(within(screen.getByRole("alert")).getByRole("strong")).not.toHaveTextContent("definition.world");
  });
});

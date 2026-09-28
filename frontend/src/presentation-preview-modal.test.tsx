import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GamePresentationSettingsModal } from "./pages/GamePage";
import { defaultPresentationProfile, resolvePresentationProfilePreview } from "./presentationPolicy";
import type { PresentationProfileDocument, PresentationProfileResponse } from "./types";

const profile: PresentationProfileDocument = {
  schema_version: 1,
  template: "standard",
  family_overrides: [],
  semantic_overrides: [],
};

function PreviewHarness({
  save = () => undefined,
  initialProfile = profile,
}: {
  save?: (profile: PresentationProfileDocument) => void;
  initialProfile?: PresentationProfileDocument;
}) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<PresentationProfileResponse>({
    scenario_id: "scenario-1",
    revision: 2,
    profile: initialProfile,
    updated_at: "2026-09-19T00:00:00Z",
  });
  const [working, setWorking] = useState<PresentationProfileDocument>(initialProfile);
  const dirty = JSON.stringify(saved.profile) !== JSON.stringify(working);
  return (
    <>
      <output data-testid="resolved-preview-template">{resolvePresentationProfilePreview(working).template}</output>
      {dirty && !open && <span data-testid="preview-indicator">预览中</span>}
      <button type="button" onClick={() => setOpen(true)}>界面设置</button>
      {open && (
        <GamePresentationSettingsModal
          scenarioName="临江市"
          savedProfile={saved}
          workingProfile={working}
          saveState={dirty ? "DIRTY" : "CLEAN"}
          loading={false}
          loadError={false}
          saveError={false}
          disabled={false}
          onChange={setWorking}
          onReturnPreview={() => setOpen(false)}
          onRestoreDefault={() => setWorking(defaultPresentationProfile())}
          onDiscard={() => {
            setWorking(saved.profile);
          }}
          onSave={() => {
            save(working);
            setSaved({ ...saved, revision: saved.revision + 1, profile: working });
            setOpen(false);
          }}
          onReload={() => undefined}
        />
      )}
    </>
  );
}

afterEach(cleanup);

describe("Game presentation preview modal", () => {
  it("uses a centered modal shell with bottom scope notes and centered footer actions", () => {
    render(<PreviewHarness />);
    fireEvent.click(screen.getByRole("button", { name: "界面设置" }));

    expect(screen.getByRole("dialog", { name: "界面设置" })).toBeVisible();
    expect(document.body).toHaveStyle({ overflow: "hidden" });
    expect(screen.queryByTestId("game-presentation-drawer")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "关闭" })).not.toBeInTheDocument();
    const notes = screen.getByTestId("game-presentation-bottom-notes");
    expect(notes).toHaveTextContent("只调整安全信息的排列与密度，不修改场景、知识或 Agent 行为。");
    expect(notes).toHaveTextContent("保存后的界面设置适用于此场景的所有版本和游戏实例。");
    for (const name of ["返回预览", "恢复默认设置", "放弃修改", "保存并应用"]) {
      expect(screen.getByRole("button", { name })).toHaveClass("action-button-centered");
    }
    expect(screen.getByRole("button", { name: "放弃修改" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存并应用" })).toBeDisabled();
  });

  it("keeps the same local draft across return preview, Escape, and reopen without saving", () => {
    const save = vi.fn();
    render(<PreviewHarness save={save} />);
    fireEvent.click(screen.getByRole("button", { name: "界面设置" }));
    fireEvent.change(screen.getByTestId("presentation-template"), { target: { value: "compact" } });
    fireEvent.click(screen.getByRole("button", { name: "返回预览" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
    expect(screen.getByTestId("preview-indicator")).toBeVisible();
    expect(save).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "界面设置" }));
    expect(screen.getByTestId("presentation-template")).toHaveValue("compact");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("preview-indicator")).toBeVisible();
  });

  it("discard restores the saved profile while save commits the scenario-wide draft", () => {
    const save = vi.fn();
    render(<PreviewHarness save={save} />);
    fireEvent.click(screen.getByRole("button", { name: "界面设置" }));
    fireEvent.change(screen.getByTestId("presentation-template"), { target: { value: "compact" } });
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(screen.getByRole("dialog", { name: "界面设置" })).toBeVisible();
    expect(screen.queryByTestId("preview-indicator")).not.toBeInTheDocument();
    expect(screen.getByTestId("presentation-template")).toHaveValue("standard");
    expect(screen.getByTestId("resolved-preview-template")).toHaveTextContent("standard");
    expect(screen.getByTestId("game-presentation-save-state")).toHaveTextContent("CLEAN");
    fireEvent.change(screen.getByTestId("presentation-template"), { target: { value: "detailed" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并应用" }));
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ template: "detailed" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByTestId("preview-indicator")).not.toBeInTheDocument();
  });

  it("previews canonical defaults locally, retains them across return, and discards back to saved", () => {
    const save = vi.fn();
    const customized = { ...profile, template: "detailed" as const };
    render(<PreviewHarness save={save} initialProfile={customized} />);
    fireEvent.click(screen.getByRole("button", { name: "界面设置" }));
    fireEvent.click(screen.getByRole("button", { name: "恢复默认设置" }));

    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByTestId("presentation-template")).toHaveValue("standard");
    expect(screen.getByTestId("game-presentation-save-state")).toHaveTextContent("DIRTY");
    expect(save).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "返回预览" }));
    expect(screen.getByTestId("preview-indicator")).toBeVisible();
    expect(screen.getByTestId("resolved-preview-template")).toHaveTextContent("standard");
    fireEvent.click(screen.getByRole("button", { name: "界面设置" }));
    expect(screen.getByTestId("presentation-template")).toHaveValue("standard");
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(screen.getByRole("dialog", { name: "界面设置" })).toBeVisible();
    expect(screen.queryByTestId("preview-indicator")).not.toBeInTheDocument();
    expect(screen.getByTestId("resolved-preview-template")).toHaveTextContent("detailed");
    expect(screen.getByTestId("presentation-template")).toHaveValue("detailed");
    expect(screen.getByTestId("game-presentation-save-state")).toHaveTextContent("CLEAN");
    expect(save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "返回预览" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("saves canonical defaults only after Save and keeps canonical-on-canonical clean", () => {
    const save = vi.fn();
    const { unmount } = render(<PreviewHarness save={save} initialProfile={{ ...profile, template: "compact" }} />);
    fireEvent.click(screen.getByRole("button", { name: "界面设置" }));
    fireEvent.click(screen.getByRole("button", { name: "恢复默认设置" }));
    fireEvent.click(screen.getByRole("button", { name: "保存并应用" }));
    expect(save).toHaveBeenCalledWith(defaultPresentationProfile());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    unmount();
    render(<PreviewHarness />);
    fireEvent.click(screen.getByRole("button", { name: "界面设置" }));
    fireEvent.click(screen.getByRole("button", { name: "恢复默认设置" }));
    expect(screen.getByTestId("game-presentation-save-state")).toHaveTextContent("CLEAN");
    expect(screen.getByRole("button", { name: "保存并应用" })).toBeDisabled();
  });

  it("keeps the modal open when a save error is presented", () => {
    render(
      <GamePresentationSettingsModal
        scenarioName="临江市"
        savedProfile={{ scenario_id: "scenario-1", revision: 2, profile, updated_at: "" }}
        workingProfile={{ ...profile, template: "compact" }}
        saveState="ERROR"
        loading={false}
        loadError={false}
        saveError
        disabled={false}
        onChange={() => undefined}
        onReturnPreview={() => undefined}
        onRestoreDefault={() => undefined}
        onDiscard={() => undefined}
        onSave={() => undefined}
        onReload={() => undefined}
      />,
    );
    expect(screen.getByRole("dialog", { name: "界面设置" })).toBeVisible();
    expect(screen.getByText("保存失败，请稍后重试。")).toBeVisible();
  });
});

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GamePresentationSettingsModal } from "./pages/GamePage";
import type { PresentationProfileDocument, PresentationProfileResponse } from "./types";

const profile: PresentationProfileDocument = {
  schema_version: 1,
  template: "standard",
  family_overrides: [],
  semantic_overrides: [],
};

function PreviewHarness({ save = () => undefined }: { save?: (profile: PresentationProfileDocument) => void }) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<PresentationProfileResponse>({
    scenario_id: "scenario-1",
    revision: 2,
    profile,
    updated_at: "2026-09-19T00:00:00Z",
  });
  const [working, setWorking] = useState<PresentationProfileDocument>(profile);
  const dirty = JSON.stringify(saved.profile) !== JSON.stringify(working);
  return (
    <>
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
          onDiscard={() => {
            setWorking(saved.profile);
            setOpen(false);
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
  it("uses a centered modal shell with one footer exit and a scenario-wide scope notice", () => {
    render(<PreviewHarness />);
    fireEvent.click(screen.getByRole("button", { name: "界面设置" }));

    expect(screen.getByRole("dialog", { name: "界面设置" })).toBeVisible();
    expect(screen.queryByTestId("game-presentation-drawer")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "关闭" })).not.toBeInTheDocument();
    expect(screen.getByText("保存后将应用于此场景的所有版本和游戏实例。")).toBeVisible();
    expect(screen.getByRole("button", { name: "返回预览" })).toBeVisible();
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
    expect(screen.queryByTestId("preview-indicator")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "界面设置" }));
    expect(screen.getByTestId("presentation-template")).toHaveValue("standard");
    fireEvent.change(screen.getByTestId("presentation-template"), { target: { value: "detailed" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并应用" }));
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ template: "detailed" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByTestId("preview-indicator")).not.toBeInTheDocument();
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
        onDiscard={() => undefined}
        onSave={() => undefined}
        onReload={() => undefined}
      />,
    );
    expect(screen.getByRole("dialog", { name: "界面设置" })).toBeVisible();
    expect(screen.getByText("保存失败，请稍后重试。")).toBeVisible();
  });
});

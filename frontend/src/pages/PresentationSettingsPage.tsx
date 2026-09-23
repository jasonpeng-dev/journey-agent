import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api";
import { PresentationSettingsPanel } from "../components/PresentationSettingsPanel";
import { EditorConfirmDialog } from "../components/editor/EditorConfirmDialog";
import {
  clonePresentationProfile,
  defaultPresentationProfile,
  PRESENTATION_PROFILE_REFRESH_INTERVAL_MS,
  resolvePresentationProfilePreview,
} from "../presentationPolicy";
import type {
  PresentationProfileDocument,
  PresentationProfileResponse,
} from "../types";
import { uiLabel } from "../ui";
import { useUnsavedChangesGuard } from "../useUnsavedChangesGuard";

type SaveState = "CLEAN" | "DIRTY" | "SAVING" | "CONFLICT" | "ERROR";

const presentationStatusLabels: Record<string, string> = {
  compact: "紧凑",
  standard: "标准",
  detailed: "详细",
  COMPACT: "紧凑",
  STANDARD: "标准",
  DETAILED: "详细",
};

const saveStateLabels: Record<SaveState, string> = {
  CLEAN: "未修改",
  DIRTY: "有未保存修改",
  SAVING: "保存中",
  CONFLICT: "存在冲突",
  ERROR: "保存失败",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请稍后重试。";
}

export function PresentationSettingsPage() {
  const { scenarioId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const scenario = useQuery({
    queryKey: ["scenario", scenarioId],
    queryFn: () => api.scenario(scenarioId),
    enabled: Boolean(scenarioId),
  });
  const presentation = useQuery({
    queryKey: ["presentation", scenarioId],
    queryFn: () => api.presentation(scenarioId),
    enabled: Boolean(scenarioId),
    refetchOnWindowFocus: false,
  });
  const presentationRevision = useQuery({
    queryKey: ["presentation-revision", scenarioId],
    queryFn: () => api.presentationRevision(scenarioId),
    enabled: Boolean(scenarioId),
    refetchOnWindowFocus: true,
    refetchInterval: PRESENTATION_PROFILE_REFRESH_INTERVAL_MS,
  });
  const history = useQuery({
    queryKey: ["presentation-history", scenarioId],
    queryFn: () => api.presentationHistory(scenarioId),
    enabled: Boolean(scenarioId),
  });
  const [savedProfile, setSavedProfile] = useState<PresentationProfileResponse | null>(null);
  const [workingProfile, setWorkingProfile] = useState<PresentationProfileDocument | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("CLEAN");
  const [pendingNavigation, setPendingNavigation] = useState<string | null>(null);

  useEffect(() => {
    const next = presentation.data;
    if (!next) return;
    if (!savedProfile) {
      setSavedProfile(next);
      setWorkingProfile(clonePresentationProfile(next.profile));
      setSaveState("CLEAN");
      return;
    }
    if (next.revision <= savedProfile.revision) return;
    const localDirty = Boolean(
      workingProfile
      && JSON.stringify(savedProfile.profile) !== JSON.stringify(workingProfile),
    );
    setSavedProfile(next);
    if (localDirty) {
      setSaveState("CONFLICT");
      return;
    }
    setWorkingProfile(clonePresentationProfile(next.profile));
    setSaveState("CLEAN");
  }, [presentation.data, savedProfile, workingProfile]);

  useEffect(() => {
    const nextRevision = presentationRevision.data?.revision;
    if (!savedProfile || nextRevision == null || nextRevision <= savedProfile.revision) return;
    const localDirty = Boolean(
      workingProfile
      && JSON.stringify(savedProfile.profile) !== JSON.stringify(workingProfile),
    );
    if (localDirty) {
      setSaveState("CONFLICT");
      return;
    }
    void presentation.refetch();
  }, [presentation, presentationRevision.data?.revision, savedProfile, workingProfile]);

  const dirty = Boolean(
    savedProfile
    && workingProfile
    && JSON.stringify(savedProfile.profile) !== JSON.stringify(workingProfile),
  );
  const archived = scenario.data?.status === "ARCHIVED";
  const effectiveState: SaveState = saveState === "CONFLICT" || saveState === "ERROR"
    ? saveState
    : saveMutationPendingPlaceholder(saveState, dirty);

  useUnsavedChangesGuard(dirty, "存在未保存的界面设置修改，离开后将丢失。确定离开吗？", setPendingNavigation);

  const setWorking = (next: PresentationProfileDocument) => {
    setWorkingProfile(next);
    setSaveState((current) => current === "CONFLICT" ? current : "DIRTY");
  };

  const reloadServerProfile = async () => {
    const result = await presentation.refetch();
    if (!result.data) return;
    setSavedProfile(result.data);
    setWorkingProfile(clonePresentationProfile(result.data.profile));
    setSaveState("CLEAN");
  };

  const returnToScenario = () => {
    if (dirty) {
      setPendingNavigation(`/scenarios/${scenarioId}`);
      return;
    }
    navigate(`/scenarios/${scenarioId}`);
  };
  const confirmPendingNavigation = () => {
    const target = pendingNavigation;
    setPendingNavigation(null);
    if (!target) return;
    const url = new URL(target, window.location.href);
    navigate(`${url.pathname}${url.search}${url.hash}`);
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!savedProfile || !workingProfile) throw new Error("显示配置尚未加载");
      return api.savePresentation(scenarioId, savedProfile.revision, workingProfile);
    },
    onMutate: () => setSaveState("SAVING"),
    onSuccess: (next) => {
      setSavedProfile(next);
      setWorkingProfile(clonePresentationProfile(next.profile));
      setSaveState("CLEAN");
      queryClient.setQueryData(["presentation", scenarioId], next);
      void queryClient.invalidateQueries({ queryKey: ["presentation", scenarioId] });
      void queryClient.invalidateQueries({ queryKey: ["presentation-revision", scenarioId] });
      void queryClient.invalidateQueries({ queryKey: ["presentation-history", scenarioId] });
    },
    onError: (error) => setSaveState(error instanceof ApiError && error.status === 409 ? "CONFLICT" : "ERROR"),
  });

  const restoreMutation = useMutation({
    mutationFn: (revision: number) => {
      if (!savedProfile) throw new Error("显示配置尚未加载");
      return api.restorePresentation(scenarioId, savedProfile.revision, revision);
    },
    onMutate: () => setSaveState("SAVING"),
    onSuccess: (next) => {
      setSavedProfile(next);
      setWorkingProfile(clonePresentationProfile(next.profile));
      setSaveState("CLEAN");
      queryClient.setQueryData(["presentation", scenarioId], next);
      void queryClient.invalidateQueries({ queryKey: ["presentation", scenarioId] });
      void queryClient.invalidateQueries({ queryKey: ["presentation-revision", scenarioId] });
      void queryClient.invalidateQueries({ queryKey: ["presentation-history", scenarioId] });
    },
    onError: (error) => setSaveState(error instanceof ApiError && error.status === 409 ? "CONFLICT" : "ERROR"),
  });

  if (!scenario.data || !workingProfile || !savedProfile) {
    return <main className="page"><p>{scenario.error || presentation.error ? "无法加载界面设置。" : "正在加载界面设置……"}</p></main>;
  }
  const effectiveProfile = resolvePresentationProfilePreview(workingProfile, savedProfile.revision);

  return (
    <main className="page presentation-settings-page">
      {pendingNavigation && <EditorConfirmDialog title="放弃当前界面设置修改？" message="当前界面设置有未保存修改，离开后这些修改将丢失。" confirmLabel="离开" onCancel={() => setPendingNavigation(null)} onConfirm={confirmPendingNavigation} />}
      <header className="presentation-page-header">
        <div className="presentation-page-title" data-testid="presentation-page-title">
          <p className="eyebrow">场景显示配置</p>
          <h1>界面设置</h1>
        </div>
        <div className="presentation-page-summary" data-testid="presentation-page-summary">
          <span className="presentation-scenario-badge">{scenario.data.name}</span>
          <div className="presentation-page-actions">
            <div className="presentation-page-status">
              <span>当前：{presentationStatusLabels[effectiveProfile.template]} / {presentationStatusLabels[effectiveProfile.density]}</span>
              <span className="presentation-revision">修订 {savedProfile.revision}</span>
              <span className={`save-state ${effectiveState.toLowerCase()}`} data-testid="presentation-save-state">{saveStateLabels[effectiveState] ?? uiLabel(effectiveState)}</span>
            </div>
            <div className="presentation-page-action-buttons">
              <button type="button" className="primary-button action-button-centered" data-testid="presentation-save-button" disabled={!dirty || archived || saveMutation.isPending || restoreMutation.isPending || saveState === "CONFLICT"} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? "正在保存……" : "保存"}</button>
              <button type="button" className="secondary-button action-button-centered" data-testid="presentation-restore-default-button" disabled={archived || saveMutation.isPending || restoreMutation.isPending} onClick={() => setWorking(defaultPresentationProfile())}>恢复默认设置</button>
              <button type="button" className="secondary-button action-button-centered" data-testid="presentation-discard-button" disabled={!dirty || saveMutation.isPending || restoreMutation.isPending} onClick={() => { setWorkingProfile(clonePresentationProfile(savedProfile.profile)); setSaveState("CLEAN"); }}>放弃修改</button>
              <button type="button" className="secondary-button navigation-away-button action-button-centered" data-testid="presentation-return-button" disabled={saveMutation.isPending || restoreMutation.isPending} onClick={returnToScenario}>返回场景详情</button>
            </div>
          </div>
        </div>
      </header>

      {(saveMutation.error || restoreMutation.error || saveState === "CONFLICT") && (
        <div className="presentation-alert" data-testid="presentation-save-error">
          <strong>{effectiveState === "CONFLICT" ? "保存冲突" : "保存失败"}</strong>
          <span>{saveMutation.error || restoreMutation.error
            ? errorMessage(saveMutation.error ?? restoreMutation.error)
            : "服务器版本已更新，请重新加载后再保存。"}</span>
          {effectiveState === "CONFLICT" && <button type="button" onClick={() => void reloadServerProfile()}>重新加载服务器版本</button>}
        </div>
      )}
      {archived && <div className="presentation-alert"><strong>只读场景</strong><span>已归档场景不能修改显示配置。</span></div>}

      <PresentationSettingsPanel profile={workingProfile} onChange={setWorking} disabled={archived || saveMutation.isPending || restoreMutation.isPending} />

      <section className="presentation-history" data-testid="presentation-history">
        <div className="presentation-card-heading"><div><p className="eyebrow">修订历史</p><h2>历史版本</h2></div><span className="presentation-effective">恢复会生成新的当前修订</span></div>
        {history.isLoading && <p className="muted">正在加载历史……</p>}
        {history.error && <p className="error">无法加载历史版本。</p>}
        <div className="presentation-history-list">
          {history.data?.revisions.map((item) => (
            <article key={item.revision} className={`presentation-history-row${item.revision === savedProfile.revision ? " current" : ""}`}>
              <div><strong>修订 {item.revision}</strong><time>{new Date(item.created_at).toLocaleString("zh-CN")}</time>{item.revision === savedProfile.revision && <span>当前</span>}</div>
              <button type="button" disabled={archived || restoreMutation.isPending || item.revision === savedProfile.revision} onClick={() => restoreMutation.mutate(item.revision)}>恢复此版本</button>
            </article>
          ))}
        </div>
      </section>
      <div className="presentation-bottom-notes" data-testid="presentation-bottom-notes">
        <span>只调整安全信息的排列与密度，不修改场景、知识或 Agent 行为。</span>
        <span>保存后的界面设置适用于此场景的所有版本和游戏实例。</span>
      </div>
    </main>
  );
}

function saveMutationPendingPlaceholder(state: SaveState, dirty: boolean): SaveState {
  if (state === "SAVING") return state;
  return dirty ? "DIRTY" : "CLEAN";
}

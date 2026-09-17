import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api";
import { PresentationSettingsPanel } from "../components/PresentationSettingsPanel";
import { clonePresentationProfile } from "../presentationPolicy";
import type {
  PresentationProfileDocument,
  PresentationProfileResponse,
} from "../types";

type SaveState = "CLEAN" | "DIRTY" | "SAVING" | "CONFLICT" | "ERROR";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请稍后重试。";
}

export function PresentationSettingsPage() {
  const { scenarioId = "" } = useParams();
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
  });
  const history = useQuery({
    queryKey: ["presentation-history", scenarioId],
    queryFn: () => api.presentationHistory(scenarioId),
    enabled: Boolean(scenarioId),
  });
  const [savedProfile, setSavedProfile] = useState<PresentationProfileResponse | null>(null);
  const [workingProfile, setWorkingProfile] = useState<PresentationProfileDocument | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("CLEAN");

  useEffect(() => {
    const next = presentation.data;
    if (!next || savedProfile?.revision === next.revision) return;
    setSavedProfile(next);
    setWorkingProfile(clonePresentationProfile(next.profile));
    setSaveState("CLEAN");
  }, [presentation.data, savedProfile?.revision]);

  const dirty = Boolean(
    savedProfile
    && workingProfile
    && JSON.stringify(savedProfile.profile) !== JSON.stringify(workingProfile),
  );
  const archived = scenario.data?.status === "ARCHIVED";
  const effectiveState: SaveState = saveState === "CONFLICT" || saveState === "ERROR"
    ? saveState
    : saveMutationPendingPlaceholder(saveState, dirty);

  const setWorking = (next: PresentationProfileDocument) => {
    setWorkingProfile(next);
    setSaveState("DIRTY");
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!savedProfile || !workingProfile) throw new Error("PresentationProfile 尚未加载");
      return api.savePresentation(scenarioId, savedProfile.revision, workingProfile);
    },
    onMutate: () => setSaveState("SAVING"),
    onSuccess: (next) => {
      setSavedProfile(next);
      setWorkingProfile(clonePresentationProfile(next.profile));
      setSaveState("CLEAN");
      queryClient.setQueryData(["presentation", scenarioId], next);
      void queryClient.invalidateQueries({ queryKey: ["presentation-history", scenarioId] });
    },
    onError: (error) => setSaveState(error instanceof ApiError && error.status === 409 ? "CONFLICT" : "ERROR"),
  });

  const restoreMutation = useMutation({
    mutationFn: (revision: number) => {
      if (!savedProfile) throw new Error("PresentationProfile 尚未加载");
      return api.restorePresentation(scenarioId, savedProfile.revision, revision);
    },
    onMutate: () => setSaveState("SAVING"),
    onSuccess: (next) => {
      setSavedProfile(next);
      setWorkingProfile(clonePresentationProfile(next.profile));
      setSaveState("CLEAN");
      queryClient.setQueryData(["presentation", scenarioId], next);
      void queryClient.invalidateQueries({ queryKey: ["presentation-history", scenarioId] });
    },
    onError: (error) => setSaveState(error instanceof ApiError && error.status === 409 ? "CONFLICT" : "ERROR"),
  });

  if (!scenario.data || !workingProfile || !savedProfile) {
    return <main className="page"><p>{scenario.error || presentation.error ? "无法加载界面设置。" : "正在加载界面设置……"}</p></main>;
  }

  return (
    <main className="page presentation-settings-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Scenario presentation</p>
          <h1>界面设置</h1>
          <p className="muted">{scenario.data.name} · 只调整安全信息的排列与密度，不修改场景、知识或 Agent 行为。</p>
        </div>
        <div className="presentation-page-actions">
          <span className={`save-state ${effectiveState.toLowerCase()}`} data-testid="presentation-save-state">{effectiveState}</span>
          <span className="presentation-revision">当前修订号：{savedProfile.revision}</span>
        </div>
      </div>

      {(saveMutation.error || restoreMutation.error) && (
        <div className="presentation-alert" data-testid="presentation-save-error">
          <strong>{effectiveState === "CONFLICT" ? "保存冲突" : "保存失败"}</strong>
          <span>{errorMessage(saveMutation.error ?? restoreMutation.error)}</span>
          {effectiveState === "CONFLICT" && <button type="button" onClick={() => void presentation.refetch()}>重新加载服务器版本</button>}
        </div>
      )}
      {archived && <div className="presentation-alert"><strong>只读场景</strong><span>已归档场景不能修改 PresentationProfile。</span></div>}

      <PresentationSettingsPanel profile={workingProfile} onChange={setWorking} disabled={archived || saveMutation.isPending || restoreMutation.isPending} />

      <div className="presentation-workstation-footer">
        <div className="presentation-footer-links">
          <Link className="secondary-button" to={`/scenarios/${scenarioId}`}>返回场景详情</Link>
          <button type="button" className="secondary-button" disabled={!dirty || saveMutation.isPending || restoreMutation.isPending} onClick={() => { setWorkingProfile(clonePresentationProfile(savedProfile.profile)); setSaveState("CLEAN"); }}>放弃本地修改</button>
        </div>
        <button type="button" className="primary-button" data-testid="presentation-save-button" disabled={!dirty || archived || saveMutation.isPending || restoreMutation.isPending} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? "正在保存……" : "保存界面设置"}</button>
      </div>

      <section className="presentation-history" data-testid="presentation-history">
        <div className="presentation-card-heading"><div><p className="eyebrow">Revision history</p><h2>历史版本</h2></div><span className="presentation-effective">恢复会生成新的当前修订</span></div>
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
    </main>
  );
}

function saveMutationPendingPlaceholder(state: SaveState, dirty: boolean): SaveState {
  if (state === "SAVING") return state;
  return dirty ? "DIRTY" : "CLEAN";
}

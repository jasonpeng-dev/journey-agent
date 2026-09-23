import { Link } from "react-router-dom";

import { EditorDialog } from "./EditorDialog";

export type FactDeleteReference = {
  type: string;
  name: string;
  path: string;
  href: string | null;
};

export type FactDeleteDialogState =
  | {
      kind: "confirm";
      factName: string;
      nodeKey: string;
      factKey: string;
      document: Record<string, unknown>;
      transformedDocument: Record<string, unknown>;
    }
  | {
      kind: "blocked";
      factName: string;
      nodeKey: string;
      factKey: string;
      references: FactDeleteReference[];
    }
  | {
      kind: "error";
      factName: string;
      nodeKey: string;
      factKey: string;
      reason: string;
      code?: string;
    };

type FactDeleteDialogProps = {
  state: FactDeleteDialogState;
  onClose: () => void;
  onConfirm?: () => void;
};

export function FactDeleteDialog({ state, onClose, onConfirm }: FactDeleteDialogProps) {
  const title = state.kind === "blocked"
    ? `无法删除「${state.factName}」`
    : state.kind === "error"
      ? "暂时无法检查是否可以删除"
      : `删除「${state.factName}」？`;
  const titleId = "fact-delete-dialog-title";
  const referenceGroups = state.kind === "blocked"
    ? Array.from(state.references.reduce((groups, reference) => {
      const current = groups.get(reference.type) ?? [];
      current.push(reference);
      groups.set(reference.type, current);
      return groups;
    }, new Map<string, FactDeleteReference[]>()))
    : [];
  const footer = state.kind === "blocked" || state.kind === "error"
    ? <button type="button" className="editor-button editor-button-secondary" onClick={onClose}>关闭</button>
    : <><button type="button" className="editor-button editor-button-secondary" onClick={onClose}>取消</button><button type="button" className="editor-button editor-button-danger" onClick={onConfirm}>删除</button></>;
  return <EditorDialog titleId={titleId} kicker="事实操作" title={title} onClose={onClose} footer={footer}>
      {state.kind === "blocked" ? <>
        <p>以下内容仍在引用该事实：</p>
        {state.references.length > 0 ? <div className="fact-delete-reference-groups">
          {referenceGroups.map(([type, references]) => <details key={type} open={referenceGroups.length === 1}>
            <summary><strong>{type}</strong><span>{references.length} 处引用</span></summary>
            <ul className="fact-delete-reference-list">{references.map((reference, index) => <li key={`${reference.type}:${reference.name}:${reference.path}:${index}`}>
              <span><span>{reference.name}</span><code>{reference.path}</code></span>
              {reference.href && <Link to={reference.href} onClick={onClose}>前往</Link>}
            </li>)}</ul>
          </details>)}
        </div> : <p className="typed-help">引用位置暂时无法解析，请先检查当前工作副本中的引用。</p>}
        <p className="fact-delete-technical">节点：{state.nodeKey} · 事实键：{state.factKey}</p>
      </> : state.kind === "error" ? <>
        <p>无法完成「{state.factName}」的删除预检，因此没有修改当前工作副本。</p>
        <p className="fact-delete-preflight-error">{state.reason}</p>
        {state.code && <p className="fact-delete-technical">错误代码：{state.code}</p>}
        <p className="fact-delete-technical">节点：{state.nodeKey} · 事实键：{state.factKey}</p>
      </> : <>
        <p>删除后将从当前工作副本中移除。</p>
        <p className="typed-help">该操作尚未保存，可通过“放弃修改”恢复到已保存草稿。</p>
        <p className="fact-delete-technical">节点：{state.nodeKey} · 事实键：{state.factKey}</p>
      </>}
  </EditorDialog>;
}

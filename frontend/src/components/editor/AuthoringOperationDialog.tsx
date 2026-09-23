import { Link } from "react-router-dom";

import { EditorDialog } from "./EditorDialog";

export type AuthoringReference = {
  type: string;
  name: string;
  path: string;
  href: string | null;
};

export type AuthoringOperationDialogState =
  | {
      kind: "confirm";
      title: string;
      subject: string;
      detail: string;
      document: Record<string, unknown>;
      transformedDocument: Record<string, unknown>;
      onApplied?: () => void;
    }
  | {
      kind: "blocked";
      title: string;
      subject: string;
      references: AuthoringReference[];
    }
  | {
      kind: "error";
      title?: string;
      subject: string;
      reason: string;
      code?: string;
    };

type Props = {
  state: AuthoringOperationDialogState;
  onClose: () => void;
  onConfirm?: () => void;
};

export function AuthoringOperationDialog({ state, onClose, onConfirm }: Props) {
  const title = state.kind === "blocked"
    ? state.title
    : state.kind === "error"
      ? state.title ?? "暂时无法检查是否可以删除"
      : state.title;
  const titleId = "authoring-operation-dialog-title";
  const footer = state.kind === "blocked" || state.kind === "error"
    ? <button type="button" className="editor-button editor-button-secondary" onClick={onClose}>关闭</button>
    : <><button type="button" className="editor-button editor-button-secondary" onClick={onClose}>取消</button><button type="button" className="editor-button editor-button-danger" onClick={onConfirm}>删除</button></>;
  return <EditorDialog titleId={titleId} kicker="场景编辑操作" title={title} onClose={onClose} footer={footer}>
      {state.kind === "blocked" ? <>
        <p>以下内容仍在引用「{state.subject}」，请先移除这些引用：</p>
        {state.references.length > 0 ? <ul className="fact-delete-reference-list">
          {state.references.map((reference, index) => <li key={`${reference.type}:${reference.name}:${reference.path}:${index}`}>
            <span><strong>{reference.type}</strong><span> · {reference.name}</span><code>{reference.path}</code></span>
            {reference.href && <Link to={reference.href} onClick={onClose}>前往</Link>}
          </li>)}
        </ul> : <p className="typed-help">引用位置暂时无法解析；删除已安全阻断。</p>}
      </> : state.kind === "error" ? <>
        <p>无法完成「{state.subject}」的删除预检，因此没有修改当前工作副本。</p>
        <p className="fact-delete-preflight-error">{state.reason}</p>
        {state.code && <p className="fact-delete-technical">错误代码：{state.code}</p>}
      </> : <>
        <p>{state.detail}</p>
        <p className="typed-help">该操作只修改当前工作副本；保存前可通过“放弃修改”恢复。</p>
      </>}
  </EditorDialog>;
}

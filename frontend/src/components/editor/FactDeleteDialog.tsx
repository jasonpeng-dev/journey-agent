import { Link } from "react-router-dom";

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
  return <div className="fact-delete-dialog-overlay" role="presentation">
    <section className="fact-delete-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <header className="fact-delete-dialog-heading">
        <div>
          <p className="panel-kicker">事实操作</p>
          <h2 id={titleId}>{title}</h2>
        </div>
        <button type="button" className="small" aria-label="关闭" onClick={onClose}>×</button>
      </header>
      {state.kind === "blocked" ? <div className="fact-delete-dialog-body">
        <p>以下内容仍在引用该事实：</p>
        {state.references.length > 0 ? <ul className="fact-delete-reference-list">
          {state.references.map((reference, index) => <li key={`${reference.type}:${reference.name}:${reference.path}:${index}`}>
            <span><strong>{reference.type}</strong><span> · {reference.name}</span><code>{reference.path}</code></span>
            {reference.href && <Link to={reference.href} onClick={onClose}>前往</Link>}
          </li>)}
        </ul> : <p className="typed-help">引用位置暂时无法解析，请先检查当前工作副本中的引用。</p>}
        <p className="fact-delete-technical">节点：{state.nodeKey} · 事实键：{state.factKey}</p>
      </div> : state.kind === "error" ? <div className="fact-delete-dialog-body">
        <p>无法完成「{state.factName}」的删除预检，因此没有修改当前工作副本。</p>
        <p className="fact-delete-preflight-error">{state.reason}</p>
        {state.code && <p className="fact-delete-technical">错误代码：{state.code}</p>}
        <p className="fact-delete-technical">节点：{state.nodeKey} · 事实键：{state.factKey}</p>
      </div> : <div className="fact-delete-dialog-body">
        <p>删除后将从当前工作副本中移除。</p>
        <p className="typed-help">该操作尚未保存，可通过“放弃修改”恢复到已保存草稿。</p>
        <p className="fact-delete-technical">节点：{state.nodeKey} · 事实键：{state.factKey}</p>
      </div>}
      <footer className="fact-delete-dialog-footer">
        {state.kind === "blocked" || state.kind === "error" ? <button type="button" className="editor-button editor-button-secondary" onClick={onClose}>关闭</button> : <>
          <button type="button" className="editor-button editor-button-secondary" onClick={onClose}>取消</button>
          <button type="button" className="editor-button editor-button-danger" onClick={onConfirm}>删除</button>
        </>}
      </footer>
    </section>
  </div>;
}

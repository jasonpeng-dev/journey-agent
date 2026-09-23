import type { ReactNode } from "react";

type Props = {
  titleId: string;
  kicker: string;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
};

/** Shared responsive frame for editor dialogs; only the body is scrollable. */
export function EditorDialog({ titleId, kicker, title, onClose, children, footer }: Props) {
  return <div className="fact-delete-dialog-overlay" role="presentation">
    <section className="fact-delete-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <header className="fact-delete-dialog-heading">
        <div><p className="panel-kicker">{kicker}</p><h2 id={titleId}>{title}</h2></div>
        <button type="button" className="small" aria-label="关闭" onClick={onClose}>×</button>
      </header>
      <div className="fact-delete-dialog-body">{children}</div>
      <footer className="fact-delete-dialog-footer">{footer}</footer>
    </section>
  </div>;
}

import { useEffect, useRef, type ReactNode } from "react";

type Props = {
  id?: string;
  className?: string;
  titleId: string;
  kicker: string;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
};

/** Shared responsive frame for editor dialogs; only the body is scrollable. */
export function EditorDialog({ id, className = "", titleId, kicker, title, onClose, children, footer }: Props) {
  const dialogRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    const focusables = () => Array.from(dialog.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((element) => element.getAttribute("aria-hidden") !== "true");
    (dialog.querySelector<HTMLElement>("[autofocus]") ?? focusables()[0] ?? dialog).focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const controls = focusables();
      if (controls.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return <div className="fact-delete-dialog-overlay" role="presentation">
    <section ref={dialogRef} id={id} className={`fact-delete-dialog${className ? ` ${className}` : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
      <header className="fact-delete-dialog-heading">
        <div><p className="panel-kicker">{kicker}</p><h2 id={titleId}>{title}</h2></div>
        <button type="button" className="small" aria-label="关闭" onClick={onClose}>×</button>
      </header>
      <div className="fact-delete-dialog-body">{children}</div>
      <footer className="fact-delete-dialog-footer">{footer}</footer>
    </section>
  </div>;
}

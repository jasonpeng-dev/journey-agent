type Props = {
  title: string;
  message: string;
  confirmLabel?: string;
  onCancel: () => void;
  onConfirm: () => void;
};

export function EditorConfirmDialog({ title, message, confirmLabel = "确定", onCancel, onConfirm }: Props) {
  return <EditorDialog titleId="editor-confirm-dialog-title" kicker="请确认" title={title} onClose={onCancel} footer={<><button type="button" className="editor-button editor-button-secondary" onClick={onCancel}>取消</button><button type="button" className="editor-button editor-button-danger" onClick={onConfirm}>{confirmLabel}</button></>}>
    <p>{message}</p>
  </EditorDialog>;
}

type RenameProps = {
  subject: string;
  initialValue: string;
  onCancel: () => void;
  onConfirm: (value: string) => void;
};

export function EditorRenameDialog({ subject, initialValue, onCancel, onConfirm }: RenameProps) {
  return <EditorDialog titleId="editor-rename-dialog-title" kicker="重命名稳定身份" title={`重命名「${subject}」`} onClose={onCancel} footer={<><button type="button" className="editor-button editor-button-secondary" onClick={onCancel}>取消</button><button type="submit" form="editor-rename-form" className="editor-button editor-button-primary">重命名</button></>}>
      <form id="editor-rename-form" onSubmit={(event) => { event.preventDefault(); const input = new FormData(event.currentTarget).get("new-key"); onConfirm(typeof input === "string" ? input.trim() : ""); }}>
        <label className="initialization-field"><span>新的稳定键</span><input name="new-key" defaultValue={initialValue} autoFocus pattern="[a-z][a-z0-9_]{0,79}" required /></label>
        <p className="typed-help">稳定键是引用身份；重命名会原子更新草稿中的相关引用并立即验证。</p>
      </form>
  </EditorDialog>;
}
import { EditorDialog } from "./EditorDialog";

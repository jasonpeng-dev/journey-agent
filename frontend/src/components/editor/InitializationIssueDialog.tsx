import { useMemo } from "react";

import { AuthoringActionButton } from "./AuthoringActionButton";
import { EditorDialog } from "./EditorDialog";
import type { PreviewIssuePresentation } from "./initialization-preview-errors";

type Props = {
  issues: PreviewIssuePresentation[];
  onClose: () => void;
  onNavigate: () => void;
};

export function InitializationIssueDialog({ issues, onClose, onNavigate }: Props) {
  const groups = useMemo(() => {
    const grouped = new Map<string, { key: string; title: string; issues: PreviewIssuePresentation[] }>();
    for (const issue of issues) {
      const group = grouped.get(issue.groupKey) ?? { key: issue.groupKey, title: issue.groupTitle, issues: [] };
      group.issues.push(issue);
      grouped.set(issue.groupKey, group);
    }
    return [...grouped.values()];
  }, [issues]);

  return <EditorDialog
    id="initialization-issue-dialog"
    titleId="initialization-issue-dialog-title"
    kicker="开局配置"
    title="待完善配置"
    className="initialization-issue-dialog"
    onClose={onClose}
    footer={<AuthoringActionButton intent="view" onPress={onClose}>关闭</AuthoringActionButton>}
  >
    <p className="initialization-issue-summary">当前草稿还有 {issues.length} 项需要处理。</p>
    <div className="initialization-issue-groups">
      {groups.map((group) => <section className="initialization-issue-group" aria-label={group.title} key={group.key}>
        <h3>{group.title}</h3>
        <ul>{group.issues.map((issue, index) => <li className={`initialization-issue-row is-${issue.category}`} key={`${issue.groupKey}:${issue.problemLabel}:${index}`}>
          <div className="initialization-issue-copy"><strong>{issue.problemLabel}</strong><p>{issue.reason}</p></div>
          <div className="initialization-issue-actions">
            <AuthoringActionButton intent="navigate" to={issue.href} onPress={onNavigate}>前往</AuthoringActionButton>
            {issue.ownerHref && issue.ownerActionLabel && <AuthoringActionButton intent="navigate" to={issue.ownerHref} onPress={onNavigate}>{issue.ownerActionLabel}</AuthoringActionButton>}
          </div>
        </li>)}</ul>
      </section>)}
    </div>
  </EditorDialog>;
}

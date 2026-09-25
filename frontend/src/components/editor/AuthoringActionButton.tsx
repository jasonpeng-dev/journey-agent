import type { ReactNode, Ref } from "react";
import { Link, useInRouterContext, useLocation, useResolvedPath } from "react-router-dom";
import { activateEditorFocusTarget } from "../../editor-focus";
import type { JsonObject } from "../../editor";

type Intent = "add" | "navigate" | "view";

type Props = {
  children: ReactNode;
  intent?: Intent;
  to?: string;
  onPress?: () => void;
  disabled?: boolean;
  className?: string;
  id?: string;
  title?: string;
  ariaLabel?: string;
  ariaExpanded?: boolean;
  ariaControls?: string;
  type?: "button" | "submit";
  buttonRef?: Ref<HTMLButtonElement>;
};

function RoutedActionLink({ to, disabled, onPress, className, id, title, ariaLabel, ariaExpanded, ariaControls, children }: Pick<Props, "to" | "disabled" | "onPress" | "className" | "id" | "title" | "ariaLabel" | "ariaExpanded" | "ariaControls" | "children">) {
  const location = useLocation();
  const resolved = useResolvedPath(to ?? "");
  return <Link
    id={id}
    className={className}
    to={to ?? ""}
    title={title}
    aria-label={ariaLabel}
    aria-expanded={ariaExpanded}
    aria-controls={ariaControls}
    aria-disabled={disabled || undefined}
    tabIndex={disabled ? -1 : undefined}
    onClick={(event) => {
      if (disabled) {
        event.preventDefault();
        return;
      }
      const sameDestination = resolved.pathname === location.pathname && resolved.search === location.search;
      const focusPath = new URLSearchParams(resolved.search).get("focus_path");
      if (sameDestination && focusPath) {
        event.preventDefault();
        onPress?.();
        window.setTimeout(() => activateEditorFocusTarget(window.document, focusPath, {} as JsonObject), 0);
        return;
      }
      onPress?.();
    }}
  >{children}</Link>;
}

const icons: Record<Intent, string> = { add: "＋", navigate: "↗", view: "查看" };

export function FieldActionRow({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`field-action-row${className ? ` ${className}` : ""}`}>{children}</div>;
}

/** Shared button family for authoring actions and in-editor navigation. */
export function AuthoringActionButton({
  children,
  intent = "navigate",
  to,
  onPress,
  disabled = false,
  className = "",
  id,
  title,
  ariaLabel,
  ariaExpanded,
  ariaControls,
  type = "button",
  buttonRef,
}: Props) {
  const inRouter = useInRouterContext();
  const classes = `editor-button editor-button-secondary authoring-action-button${className ? ` ${className}` : ""}`;
  const content = <><span className="authoring-action-icon" aria-hidden="true">{icons[intent]}</span><span className="authoring-action-label">{children}</span></>;

  if (to && inRouter) return <RoutedActionLink to={to} disabled={disabled} onPress={onPress} className={classes} id={id} title={title} ariaLabel={ariaLabel} ariaExpanded={ariaExpanded} ariaControls={ariaControls}>{content}</RoutedActionLink>;
  if (to) return <a
    id={id}
    className={classes}
    href={disabled ? undefined : to}
    title={title}
    aria-label={ariaLabel}
    aria-expanded={ariaExpanded}
    aria-controls={ariaControls}
    aria-disabled={disabled || undefined}
    tabIndex={disabled ? -1 : undefined}
    onClick={disabled ? (event) => event.preventDefault() : onPress}
  >{content}</a>;

  return <button
    ref={buttonRef}
    id={id}
    type={type}
    className={classes}
    title={title}
    aria-label={ariaLabel}
    aria-expanded={ariaExpanded}
    aria-controls={ariaControls}
    disabled={disabled}
    onClick={onPress}
  >{content}</button>;
}

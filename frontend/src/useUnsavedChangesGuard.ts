import { useEffect, useRef } from "react";

export function useUnsavedChangesGuard(
  active: boolean,
  message: string,
  onBlockedNavigation?: (href: string) => void,
  shouldBlockNavigation?: (destination: URL) => boolean,
) {
  const onBlockedNavigationRef = useRef(onBlockedNavigation);
  const shouldBlockNavigationRef = useRef(shouldBlockNavigation);
  useEffect(() => { onBlockedNavigationRef.current = onBlockedNavigation; }, [onBlockedNavigation]);
  useEffect(() => { shouldBlockNavigationRef.current = shouldBlockNavigation; }, [shouldBlockNavigation]);
  useEffect(() => {
    if (!active) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = message;
    };
    const guardLinkNavigation = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[href]") : null;
      if (!target || target.target === "_blank" || target.hasAttribute("download")) return;
      const destination = new URL(target.href, window.location.href);
      if (destination.href === window.location.href) return;
      if (shouldBlockNavigationRef.current && !shouldBlockNavigationRef.current(destination)) return;
      event.preventDefault();
      event.stopPropagation();
      onBlockedNavigationRef.current?.(destination.href);
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", guardLinkNavigation, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", guardLinkNavigation, true);
    };
  }, [active, message]);
}

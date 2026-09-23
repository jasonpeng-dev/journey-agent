import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { Link, MemoryRouter, useLocation, useNavigate } from "react-router-dom";

import { useUnsavedChangesGuard } from "./useUnsavedChangesGuard";

function GuardHarness() {
  const navigate = useNavigate();
  const [pending, setPending] = useState<string | null>(null);
  useUnsavedChangesGuard(true, "Unsaved changes", setPending);
  return <><Link to="/next">Leave</Link><output role="status">{useLocation().pathname}</output>{pending && <div role="dialog" aria-label="Unsaved changes"><p>Unsaved changes</p><button onClick={() => setPending(null)}>Cancel</button><button onClick={() => { const target = pending; setPending(null); navigate(new URL(target).pathname); }}>Leave</button></div>}</>;
}

afterEach(cleanup);

describe("unsaved route guard", () => {
  it("protects refresh and in-app links without owning draft state", () => {
    render(<MemoryRouter initialEntries={["/current"]}><GuardHarness /></MemoryRouter>);

    const beforeUnload = new Event("beforeunload", { cancelable: true });
    expect(window.dispatchEvent(beforeUnload)).toBe(false);
    fireEvent.click(screen.getByRole("link", { name: "Leave" }));
    expect(screen.getByRole("status")).toHaveTextContent("/current");
    fireEvent.click(screen.getByRole("button", { name: "Leave" }));
    expect(screen.getByRole("status")).toHaveTextContent("/next");
  });
});

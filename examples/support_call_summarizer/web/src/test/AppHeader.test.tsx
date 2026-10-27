import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { AppHeader } from "../components/AppHeader";

describe("App header", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("shows the Bodhan.AI lockup with both theme marks", () => {
    render(<AppHeader />);
    expect(screen.getByRole("link", { name: "Bodhan.AI console" })).toBeInTheDocument();
    const marks = document.querySelectorAll<HTMLImageElement>(".lockup__mark");
    expect([...marks].map((m) => m.getAttribute("src"))).toEqual([
      "/brand/bodhan-mark-light.svg",
      "/brand/bodhan-mark-dark.svg",
    ]);
  });

  it("follows the OS by default and stores an explicit choice", async () => {
    render(<AppHeader />);
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");

    await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem("bodhan-theme")).toBe("dark");

    await userEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("restores a stored choice", () => {
    window.localStorage.setItem("bodhan-theme", "dark");
    render(<AppHeader />);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});

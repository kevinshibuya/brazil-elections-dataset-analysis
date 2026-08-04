import { render, screen } from "@testing-library/react";
import App from "./App";

describe("App shell", () => {
  it("renders the observatory heading", () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1 })).toBeDefined();
  });
});

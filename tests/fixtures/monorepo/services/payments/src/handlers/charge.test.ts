import { totalOf } from "./charge";

describe("charge", () => {
  it("sums items", () => {
    expect(totalOf([{ sku: "a", qty: 2, unitCents: 150 }])).toBe(300);
  });

  it("calls provider", async () => {
    const res = await fetch("https://provider.example.test/ping");
    expect(res.ok).toBe(true);
  });
});

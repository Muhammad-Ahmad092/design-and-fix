import { Router, Request, Response } from "express";
import { db } from "../lib/db";
import { logger } from "../lib/util";

const router = Router();
const PROVIDER_URL = process.env.PROVIDER_URL ?? "https://provider.example.test/v1/charges";

interface ChargeItem {
  sku: string;
  qty: number;
  unitCents: number;
}

router.post("/payments/charge", async (req: Request, res: Response) => {
  const { items, customerId } = req.body as { items: ChargeItem[]; customerId: string };
  const body = JSON.stringify({ customerId, total: totalOf(items) });
  const providerResponse = await fetch(PROVIDER_URL, { method: "POST", body });
  const result = await providerResponse.json();

  for (const item of items) {
    await db.query("INSERT INTO charge_items (sku, qty, unit_cents) VALUES ($1, $2, $3)", [
      item.sku,
      item.qty,
      item.unitCents,
    ]);
  }

  logger.info("charged " + customerId);
  res.status(201).json(result);
});

export function totalOf(items: ChargeItem[]): number {
  return items.reduce((sum, i) => sum + i.qty * i.unitCents, 0);
}

export interface ChargeRecord1 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeCharge1(input: Partial<ChargeRecord1>): ChargeRecord1 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for charge record 1");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface ChargeRecord2 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeCharge2(input: Partial<ChargeRecord2>): ChargeRecord2 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for charge record 2");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface ChargeRecord3 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeCharge3(input: Partial<ChargeRecord3>): ChargeRecord3 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for charge record 3");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface ChargeRecord4 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeCharge4(input: Partial<ChargeRecord4>): ChargeRecord4 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for charge record 4");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface ChargeRecord5 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeCharge5(input: Partial<ChargeRecord5>): ChargeRecord5 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for charge record 5");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface ChargeRecord6 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeCharge6(input: Partial<ChargeRecord6>): ChargeRecord6 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for charge record 6");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface ChargeRecord7 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeCharge7(input: Partial<ChargeRecord7>): ChargeRecord7 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for charge record 7");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface ChargeRecord8 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeCharge8(input: Partial<ChargeRecord8>): ChargeRecord8 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for charge record 8");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface ChargeRecord9 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeCharge9(input: Partial<ChargeRecord9>): ChargeRecord9 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for charge record 9");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface ChargeRecord10 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeCharge10(input: Partial<ChargeRecord10>): ChargeRecord10 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for charge record 10");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export default router;

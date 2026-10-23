import { Router, Request, Response } from "express";
import axios from "axios";
import { db } from "../lib/db";

const router = Router();

function requireAuth(req: Request, res: Response, next: () => void) {
  if (!req.headers.authorization) {
    res.status(401).json({ error: "unauthorized" });
    return;
  }
  next();
}

router.post("/payments/refund", requireAuth, async (req: Request, res: Response) => {
  const { chargeId } = req.body as { chargeId: string };
  const status = await axios.get("https://provider.example.test/v1/status/" + chargeId, {
    timeout: 5000,
  });
  await db.query("UPDATE charges SET refunded = true WHERE id = $1", [chargeId]);
  res.json({ ok: true, provider: status.data });
});

export interface RefundRecord1 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeRefund1(input: Partial<RefundRecord1>): RefundRecord1 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for refund record 1");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface RefundRecord2 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeRefund2(input: Partial<RefundRecord2>): RefundRecord2 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for refund record 2");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface RefundRecord3 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeRefund3(input: Partial<RefundRecord3>): RefundRecord3 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for refund record 3");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface RefundRecord4 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeRefund4(input: Partial<RefundRecord4>): RefundRecord4 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for refund record 4");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface RefundRecord5 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeRefund5(input: Partial<RefundRecord5>): RefundRecord5 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for refund record 5");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface RefundRecord6 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeRefund6(input: Partial<RefundRecord6>): RefundRecord6 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for refund record 6");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface RefundRecord7 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeRefund7(input: Partial<RefundRecord7>): RefundRecord7 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for refund record 7");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface RefundRecord8 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeRefund8(input: Partial<RefundRecord8>): RefundRecord8 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for refund record 8");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface RefundRecord9 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeRefund9(input: Partial<RefundRecord9>): RefundRecord9 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for refund record 9");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface RefundRecord10 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeRefund10(input: Partial<RefundRecord10>): RefundRecord10 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for refund record 10");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export default router;

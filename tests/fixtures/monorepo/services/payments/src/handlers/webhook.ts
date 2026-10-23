import { Router, Request, Response } from "express";
import { db } from "../lib/db";

const router = Router();

router.get("/payments/:id", async (req: Request, res: Response) => {
  try {
    const row = await db.query("SELECT id, amount_cents FROM charges WHERE id = $1", [req.params.id]);
    console.log("lookup " + req.params.id);
    res.json(row.rows[0] ?? null);
  } catch (e) {}
});

export interface WebhookRecord1 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeWebhook1(input: Partial<WebhookRecord1>): WebhookRecord1 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for webhook record 1");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface WebhookRecord2 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeWebhook2(input: Partial<WebhookRecord2>): WebhookRecord2 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for webhook record 2");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface WebhookRecord3 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeWebhook3(input: Partial<WebhookRecord3>): WebhookRecord3 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for webhook record 3");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface WebhookRecord4 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeWebhook4(input: Partial<WebhookRecord4>): WebhookRecord4 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for webhook record 4");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface WebhookRecord5 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeWebhook5(input: Partial<WebhookRecord5>): WebhookRecord5 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for webhook record 5");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface WebhookRecord6 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeWebhook6(input: Partial<WebhookRecord6>): WebhookRecord6 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for webhook record 6");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface WebhookRecord7 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeWebhook7(input: Partial<WebhookRecord7>): WebhookRecord7 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for webhook record 7");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface WebhookRecord8 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeWebhook8(input: Partial<WebhookRecord8>): WebhookRecord8 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for webhook record 8");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface WebhookRecord9 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeWebhook9(input: Partial<WebhookRecord9>): WebhookRecord9 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for webhook record 9");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export interface WebhookRecord10 {
  id: string;
  amountCents: number;
  currency: string;
  createdAt: Date;
}

export function normalizeWebhook10(input: Partial<WebhookRecord10>): WebhookRecord10 {
  const amountCents = Math.round(Number(input.amountCents ?? 0));
  if (!Number.isFinite(amountCents) || amountCents < 0) {
    throw new Error("invalid amount for webhook record 10");
  }
  return {
    id: String(input.id ?? ""),
    amountCents,
    currency: (input.currency ?? "USD").toUpperCase(),
    createdAt: input.createdAt ?? new Date(),
  };
}

export default router;

# 0005. Use a hosted payment provider

Date: 2026-10-22
Status: Accepted
> Format: 1.0.2

## Context

Customers pay by card at checkout. Handling card numbers directly brings full PCI DSS scope, which a team of 4 cannot audit or operate. Mobile networks drop responses, so clients retry checkout requests.

## Alternatives

| Option | Strength | Weakness | Why not chosen |
|---|---|---|---|
| Storing and charging cards in-house | Lowest per-transaction fee, full control | Full PCI DSS scope and fraud tooling to build | A team of 4 cannot audit or operate it |

## Decision

We will use a hosted payment provider with tokenised cards: authorize when the order is placed, capture when the restaurant accepts, and pass the order ID as the provider idempotency key.

## Consequences

- Positive: card data never touches our servers; retries cannot double-charge; rejected orders need no refund.
- Negative: per-transaction fees and dependency on one provider's uptime.
- Revisit when: fees exceed the cost of a second provider integration, or the launch expands to markets the provider does not serve.

## Assumptions

- The provider supports idempotency keys and delayed capture.
- The launch city is served by the provider.

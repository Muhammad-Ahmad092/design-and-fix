# API Design & Optimization

> Scope: Food-delivery platform (customer app, restaurant dashboard, courier app, backend)
> Context: 50k users, team of 4, Node.js preferred, cost-sensitive, single launch city

## API Style

**Chosen: REST (JSON over HTTPS), plus one WebSocket channel for live tracking**

Three first-party clients with similar needs; REST has the widest tooling and caches well, and only tracking needs server push.

## Endpoint Conventions

| Aspect | Rule | Why |
|---|---|---|
| Naming | Plural nouns, `/v1/orders/{id}`; actions as sub-resources (`/cancel`) | Predictable paths across three clients |
| Versioning | `/v1/` prefix; breaking change → `/v2/` with 6 months overlap | Mobile apps update slowly |
| Errors | RFC 9457 `application/problem+json` everywhere | One error parser per client |
| Idempotency | `Idempotency-Key` header required on `POST /v1/orders` and `/cancel` | Mobile retries on flaky networks must not double-order or double-charge |
| Pagination | Cursor (`cursor`, `limit` ≤ 50) on every list | Stable pages while new orders arrive |

## Endpoints

| Method | Path | Purpose | Auth | Request → Response |
|---|---|---|---|---|
| POST | /v1/auth/otp | Send login code | none | phone → 202 |
| POST | /v1/auth/token | Exchange code or refresh token | none | phone + code or refresh_token → access + refresh tokens |
| GET | /v1/restaurants | Nearby open restaurants | customer | lat, lng, cursor, limit → items[], next_cursor |
| GET | /v1/restaurants/{id}/menu | Menu with availability | customer | — → categories[] with items[] |
| POST | /v1/orders | Place and authorize an order | customer | Idempotency-Key, restaurant_id, items[], address_id, payment_method_id → 201 order |
| GET | /v1/orders/{id} | Order status and ETA | customer, restaurant, courier | — → order |
| GET | /v1/orders | Order history | customer | cursor, limit → items[], next_cursor |
| POST | /v1/orders/{id}/cancel | Cancel before acceptance | customer | Idempotency-Key → 200 order |
| PATCH | /v1/orders/{id}/status | Accept, ready, picked up, delivered | restaurant, courier | status → 200 order |
| POST | /v1/couriers/me/location | Location ping | courier | lat, lng, ts → 204 |
| GET | /v1/orders/{id}/tracking | Live courier position (WebSocket upgrade) | customer | — → stream of {lat, lng, eta} |
| POST | /v1/webhooks/payments | Provider events | provider signature | event → 200 |

OpenAPI skeleton for the top 4 endpoints:

```yaml
openapi: 3.1.0
info:
  title: Food Delivery API
  version: 1.0.0
paths:
  /v1/restaurants:
    get:
      summary: List nearby open restaurants
      security:
        - bearerAuth: []
      parameters:
        - { name: lat, in: query, required: true, schema: { type: number } }
        - { name: lng, in: query, required: true, schema: { type: number } }
        - { name: cursor, in: query, schema: { type: string } }
        - { name: limit, in: query, schema: { type: integer, maximum: 50 } }
      responses:
        "200":
          description: Page of restaurants
          content:
            application/json:
              schema: { $ref: "#/components/schemas/RestaurantPage" }
  /v1/orders:
    post:
      summary: Place and authorize an order
      security:
        - bearerAuth: []
      parameters:
        - { name: Idempotency-Key, in: header, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/OrderInput" }
      responses:
        "201":
          description: Order placed
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Order" }
        "402":
          description: Payment declined
          content:
            application/problem+json:
              schema: { $ref: "#/components/schemas/Problem" }
  /v1/orders/{id}:
    get:
      summary: Order status and ETA
      security:
        - bearerAuth: []
      parameters:
        - { name: id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: Order
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Order" }
  /v1/couriers/me/location:
    post:
      summary: Courier location ping
      security:
        - bearerAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/Location" }
      responses:
        "204":
          description: Accepted
components:
  securitySchemes:
    bearerAuth: { type: http, scheme: bearer, bearerFormat: JWT }
  schemas:
    OrderInput:
      type: object
      required: [restaurant_id, items, address_id, payment_method_id]
      properties:
        restaurant_id: { type: string, format: uuid }
        address_id: { type: string, format: uuid }
        payment_method_id: { type: string }
        items:
          type: array
          items:
            type: object
            required: [menu_item_id, qty]
            properties:
              menu_item_id: { type: string, format: uuid }
              qty: { type: integer, minimum: 1 }
    Order:
      type: object
      required: [id, status, total_cents]
      properties:
        id: { type: string, format: uuid }
        status: { type: string, enum: [placed, accepted, ready, picked_up, delivered, cancelled] }
        total_cents: { type: integer }
        eta_minutes: { type: [integer, "null"] }
    RestaurantPage:
      type: object
      properties:
        items: { type: array, items: { type: object } }
        next_cursor: { type: [string, "null"] }
    Location:
      type: object
      required: [lat, lng, ts]
      properties:
        lat: { type: number }
        lng: { type: number }
        ts: { type: string, format: date-time }
    Problem:
      type: object
      properties:
        type: { type: string }
        title: { type: string }
        status: { type: integer }
        detail: { type: string }
```

## Auth & Rate Limiting

| Aspect | Approach | Why |
|---|---|---|
| Auth | Phone OTP → 15 min JWT access token + rotating refresh token; role claim (customer, restaurant, courier) | Phone is the identity customers and couriers already have |
| Authorization | Role and ownership checked per route (`restaurant_id`, `courier_id` from the token) | Stops cross-tenant access |
| Rate limiting | Token bucket in Redis: 60 req/min per user, 1 location ping per 5 s per courier, 5 OTP per phone per hour | Protects SMS budget and the API at 30 peak QPS |
| Webhooks | Provider signature verified, event ID deduplicated via `Payment.provider_ref` | Providers retry deliveries |

## Caching Strategy

| What | Where | Invalidation | Why |
|---|---|---|---|
| Menu per restaurant | Redis | TTL 5 min + delete on menu update | Most-read object; changes a few times a day |
| Nearby restaurants per geohash cell | Redis | TTL 60 s | Many customers in one area send the same query |
| Latest courier position | Redis key per courier | Overwritten by each ping, expires after 5 min | Store of record for live tracking, keeps pings off PostgreSQL |
| Order status | Not cached | — | Primary-key reads are cheap and must be fresh |

## Performance Notes

- Pagination: cursor on `GET /v1/restaurants` and `GET /v1/orders` (keyset on `created_at, id`). Why: offset scans grow with history.
- Batching: order items inserted with one multi-row `INSERT` in the order transaction. Why: one round trip per checkout.
- N+1 avoidance: load a menu with one query joining categories and items; load restaurant ratings as one aggregate per page. Why: per-row queries multiply latency at peak.
- Timeouts: 5 s on the payment provider, 2 s on maps; ETA falls back to distance-based estimates. Why: external latency must not set our p95.

# API Design & Optimization

> Scope: <project name>
> Context: <scale> users, team of <size>, <key constraint>

## API Style

**Chosen: <REST / GraphQL / gRPC>**

<one-line reason>

## Endpoint Conventions

| Aspect | Rule | Why |
|---|---|---|
| Naming | <e.g. plural nouns, /resources/{id}> | <reason> |
| Versioning | <e.g. /v1/ prefix> | <reason> |
| Errors | <e.g. RFC 9457 problem+json, one format everywhere> | <reason> |
| Idempotency | <e.g. Idempotency-Key header on POST that charges or creates> | <reason> |

## Endpoints

| Method | Path | Purpose | Auth | Request → Response |
|---|---|---|---|---|
| <GET> | </v1/resources> | <list resources> | <user> | <cursor, limit → items[], next_cursor> |
| <POST> | </v1/resources> | <create resource> | <user> | <fields → 201 resource> |

Max 12 rows. OpenAPI skeleton for the top 3–5 endpoints only:

```yaml
openapi: 3.1.0
info:
  title: <API name>
  version: 1.0.0
paths:
  /v1/<resources>:
    get:
      summary: <list resources>
      security:
        - bearerAuth: []
      parameters:
        - name: cursor
          in: query
          schema: { type: string }
        - name: limit
          in: query
          schema: { type: integer, maximum: 100 }
      responses:
        "200":
          description: <page of resources>
          content:
            application/json:
              schema: { $ref: "#/components/schemas/<Resource>Page" }
    post:
      summary: <create resource>
      security:
        - bearerAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/<Resource>Input" }
      responses:
        "201":
          description: <created>
          content:
            application/json:
              schema: { $ref: "#/components/schemas/<Resource>" }
components:
  securitySchemes:
    bearerAuth: { type: http, scheme: bearer, bearerFormat: JWT }
  schemas:
    <Resource>:
      type: object
      required: [id]
      properties:
        id: { type: string }
    <Resource>Input:
      type: object
      properties:
        <field>: { type: string }
    <Resource>Page:
      type: object
      properties:
        items: { type: array, items: { $ref: "#/components/schemas/<Resource>" } }
        next_cursor: { type: [string, "null"] }
```

## Auth & Rate Limiting

| Aspect | Approach | Why |
|---|---|---|
| Auth | <e.g. JWT, OAuth2> | <reason> |
| Rate limiting | <e.g. token bucket per user, 100 req/min> | <reason> |

## Caching Strategy

| What | Where | Invalidation | Why |
|---|---|---|---|
| <e.g. menu reads> | <e.g. Redis> | <e.g. TTL 60 s + delete on write> | <reason> |

## Performance Notes

- Pagination: <cursor / offset, on which endpoints>. Why: <reason>
- Batching: <where batching applies>. Why: <reason>
- N+1 avoidance: <specific query patterns to avoid, how>. Why: <reason>

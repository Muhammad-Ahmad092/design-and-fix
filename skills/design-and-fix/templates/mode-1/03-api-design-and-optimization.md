# API Design & Optimization

> Scope: <project name>
> Context: <scale> users, team of <size>, <key constraint>

## API Style

**Chosen: <REST / GraphQL / gRPC>**

<one-line reason>

## Endpoint Conventions

| Aspect | Rule |
|---|---|
| Naming | <e.g. plural nouns, /resources/:id> |
| Versioning | <e.g. /v1/ prefix, header-based> |
| Status Codes | <e.g. 2xx success, 4xx client error, 5xx server error conventions used> |

## Auth & Rate Limiting

| Aspect | Approach | Why |
|---|---|---|
| Auth | <e.g. JWT, OAuth2> | <reason> |
| Rate Limiting | <e.g. token bucket per API key> | <reason> |

## Caching Strategy

| What | Where | Invalidation |
|---|---|---|
| <e.g. user profile reads> | <e.g. Redis> | <e.g. TTL + write-through> |

## Performance Notes

- Pagination: <cursor / offset, on which endpoints>
- Batching: <where batching applies>
- N+1 avoidance: <specific query patterns to avoid, how>

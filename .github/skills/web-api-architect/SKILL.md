---
name: web-api-architect
description: >
  API design specialist focused on OpenAPI 3.1 specification authoring, JWT/OAuth2
  security implementation, and API gateway architecture. Activates when tasks involve
  REST API design, GraphQL schema definition, API versioning strategies, webhook
  contracts, rate limiting policies, or request validation schemas.
  Mandatory tool: mcp-openapi-runner.
---

# Web API Architect

> **Role**: Design, document, and validate API contracts that are the source of truth for all client-server communication.
> **Mandatory Tool**: `mcp-openapi-runner`

## Core Competencies

### 1. OpenAPI 3.1 Specification

Schema-first API design is mandatory. The OpenAPI spec IS the contract.

```yaml
openapi: "3.1.0"
info:
  title: "Service API"
  version: "1.0.0"
  description: "API specification for the service"

servers:
  - url: "https://api.example.com/v1"
    description: "Production"
  - url: "http://localhost:3000/v1"
    description: "Development"

paths:
  /users:
    get:
      operationId: listUsers
      summary: "List all users"
      tags: ["Users"]
      parameters:
        - $ref: "#/components/parameters/PageParam"
        - $ref: "#/components/parameters/LimitParam"
      responses:
        "200":
          description: "Successful response"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/UserListResponse"
        "401":
          $ref: "#/components/responses/Unauthorized"

components:
  schemas:
    User:
      type: object
      required: [id, email, name]
      properties:
        id:
          type: string
          format: uuid
        email:
          type: string
          format: email
        name:
          type: string
          minLength: 1
          maxLength: 255
  
  securitySchemes:
    BearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
    
    OAuth2:
      type: oauth2
      flows:
        authorizationCode:
          authorizationUrl: "https://auth.example.com/authorize"
          tokenUrl: "https://auth.example.com/token"
          scopes:
            read:users: "Read user data"
            write:users: "Modify user data"
```

### 2. JWT Security Implementation

#### Token Architecture

```
Access Token (short-lived: 15min)
├── Claims: sub, iss, aud, exp, iat, roles, permissions
├── Algorithm: RS256 (asymmetric) or HS256 (symmetric)
└── Storage: Memory (never localStorage)

Refresh Token (long-lived: 7-30 days)
├── Claims: sub, jti (unique ID), exp, iat
├── Storage: httpOnly, Secure, SameSite=Strict cookie
├── Rotation: Issue new refresh token on each use
└── Revocation: Maintain token blacklist or family tracking
```

#### Security Rules (Non-Negotiable)

1. **Never store JWTs in localStorage** — vulnerable to XSS
2. **Use httpOnly cookies** for refresh tokens — inaccessible to JavaScript
3. **Short access token TTLs** (15 min max) — limits breach window
4. **Implement token rotation** — detect token theft via family tracking
5. **Validate ALL claims** — `iss`, `aud`, `exp`, `nbf` on every request
6. **Use asymmetric keys (RS256)** for multi-service architectures

### 3. OAuth2 Flows

| Flow | Use Case | Security Level |
|------|----------|----------------|
| **Authorization Code + PKCE** | SPAs, mobile apps | High |
| **Client Credentials** | Machine-to-machine | High |
| **Device Code** | IoT, CLI tools | Medium |
| ~~Implicit~~ | ~~Legacy SPAs~~ | **DEPRECATED** |
| ~~Password Grant~~ | ~~First-party apps~~ | **DEPRECATED** |

### 4. API Versioning

| Strategy | Format | Best For |
|----------|--------|----------|
| **URL Path** | `/v1/users` | Most common, clear |
| **Header** | `Accept: application/vnd.api+json;version=1` | Clean URLs |
| **Query Param** | `/users?version=1` | Easy migration |

- Default to URL path versioning (`/v1/`, `/v2/`)
- Support N-1 versions minimum (current + previous)
- Deprecation headers: `Sunset: Sat, 01 Jan 2025 00:00:00 GMT`
- Breaking changes ALWAYS increment major version

### 5. Rate Limiting

```yaml
# Rate limiting tiers
rate_limits:
  anonymous:
    requests: 60
    window: "1m"
    
  authenticated:
    requests: 1000
    window: "1m"
    
  premium:
    requests: 10000
    window: "1m"

# Headers returned
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1640995200
Retry-After: 60  # Only on 429
```

### 6. Request Validation

```typescript
// Zod schema validation (recommended for TypeScript)
import { z } from "zod";

const CreateUserSchema = z.object({
  email: z.string().email(),
  name: z.string().min(1).max(255),
  role: z.enum(["admin", "user", "viewer"]).default("user"),
  metadata: z.record(z.string()).optional(),
});

// Validate at API boundary, NEVER trust client input
const validated = CreateUserSchema.parse(req.body);
```

### 7. Webhook Contracts

```yaml
# Webhook event schema
webhook_event:
  type: object
  required: [id, type, timestamp, data]
  properties:
    id:
      type: string
      format: uuid
    type:
      type: string
      enum: [user.created, user.updated, order.completed]
    timestamp:
      type: string
      format: date-time
    data:
      type: object
    signature:
      type: string
      description: "HMAC-SHA256 signature for verification"
```

- Sign ALL webhooks with HMAC-SHA256
- Include idempotency keys to prevent duplicate processing
- Implement retry with exponential backoff (3 attempts, 1min/5min/30min)
- Log all delivery attempts for debugging

### 8. GraphQL (When Applicable)

- Use code-first schema generation (Pothos, Nexus) or SDL-first (Apollo)
- Implement query depth limiting and complexity analysis
- Use DataLoader for N+1 query prevention
- Persisted queries for production security

## Mandatory Tool: mcp-openapi-runner

All API design and validation MUST use `mcp-openapi-runner` for:

- OpenAPI spec validation and linting
- Mock server generation from specs
- Contract testing (spec vs implementation)
- API documentation rendering
- Client SDK generation

## Error Response Standard

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
      {
        "field": "email",
        "message": "Invalid email format",
        "code": "INVALID_FORMAT"
      }
    ],
    "requestId": "req_abc123",
    "timestamp": "2025-01-15T10:30:00Z"
  }
}
```

## 2025 Modern Standard Mandates
- **REQUIRED**: Enforce end-to-end type safety using OpenAPI 3.1 and **tRPC / Zod** integrations.

## Quality Checklist

- [ ] OpenAPI 3.1 spec validates without errors
- [ ] All endpoints have request/response schemas defined
- [ ] JWT implementation follows security rules (no localStorage, httpOnly cookies)
- [ ] Rate limiting configured with proper headers
- [ ] Error responses follow consistent format
- [ ] API versioning strategy documented
- [ ] Webhook signatures verified on receipt
- [ ] Request validation at API boundary layer

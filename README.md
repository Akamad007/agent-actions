# django-agent-actions

> Expose safe, auditable backend actions to LLM agents — as a Django app.

`django-agent-actions` is a reusable Django app that lets you define normal
Python functions with `@action` and expose them to AI agents through a
built-in REST API. Every call flows through a consistent pipeline: input
validation → scope check → policy → idempotency → execution → audit log.

---

## Features

| Feature | Status |
|---|---|
| `@action` decorator with typed Pydantic schemas | ✅ |
| Policy engine (allow / deny / require_approval) | ✅ |
| Required scopes per action | ✅ |
| Approval workflow (pending → approved / rejected) | ✅ |
| Approver identity captured on every decision | ✅ |
| Audit logging with sensitive field redaction | ✅ |
| Idempotency (per tenant, DB-backed duplicate suppression) | ✅ |
| Thread-safe runtime for concurrent requests | ✅ |
| Atomic approval and idempotency state transitions | ✅ |
| Django ORM models + migrations included | ✅ |
| REST API (Django views) | ✅ |
| Request context (actor_id, roles, tenant_id) | ✅ |
| Pluggable `AuthBackend` interface | ✅ |

---

## Installation

```bash
pip install django-agent-actions
```

---

## Quickstart

### 1. Add to `INSTALLED_APPS` and include URLs

```python
# settings.py
INSTALLED_APPS = [
    ...
    "django_agent_actions",
]
```

```python
# urls.py
from django.urls import include, path

urlpatterns = [
    path("agent-actions/", include("django_agent_actions.urls")),
]
```

### 2. Run migrations

```bash
python manage.py migrate
```

### 3. Define and register actions

```python
# myapp/actions.py
from django_agent_actions import action, registry
from django_agent_actions.context import RequestContext

@action(
    name="get_invoice",
    description="Fetch an invoice by ID",
    risk="low",
    required_scopes=["finance"],
)
def get_invoice(invoice_id: str, ctx: RequestContext):
    return {"invoice_id": invoice_id, "status": "open", "amount": 149.99}

@action(
    name="issue_refund",
    description="Issue a full refund — requires human approval",
    risk="high",
    approval_required=True,
    required_scopes=["finance"],
)
def issue_refund(invoice_id: str, reason: str, ctx: RequestContext):
    return {"invoice_id": invoice_id, "refunded": True, "reason": reason}

registry.register(get_invoice)
registry.register(issue_refund)
```

### 4. Load actions at startup

```python
# myapp/apps.py
from django.apps import AppConfig

class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self):
        import myapp.actions  # noqa: F401  — registers actions once
```

---

## Configuration

All keys are optional. Set `AGENT_ACTIONS` in `settings.py`:

```python
from django_agent_actions.policies import RiskBasedPolicy

AGENT_ACTIONS = {
    # App-level policy applied to actions with no per-action override.
    # Default: allow everything.
    "DEFAULT_POLICY": RiskBasedPolicy(),

    # Auth backend for inline credential validation.
    # Default: trust X-Actor-Id / X-Roles / X-Tenant-Id headers from middleware.
    "AUTH_BACKEND": None,
}
```

---

## HTTP API

All endpoints are mounted under the prefix you chose in `urls.py`
(e.g. `/agent-actions/`).

| Method | Path | Description |
|---|---|---|
| `GET` | `health/` | Liveness check |
| `GET` | `actions/` | List registered actions and their input schemas |
| `POST` | `actions/<name>/execute/` | Invoke an action |
| `GET` | `approvals/` | List approvals (`?status=pending`) |
| `POST` | `approvals/<id>/approve/` | Approve and execute a pending action |
| `POST` | `approvals/<id>/reject/` | Reject a pending action |
| `GET` | `audit-logs/` | Paginated audit log |

### Execute an action

```bash
curl -s -X POST http://localhost:8000/agent-actions/actions/get_invoice/execute/ \
  -H "Content-Type: application/json" \
  -H "X-Actor-Id: alice" \
  -H "X-Roles: finance" \
  -H "X-Tenant-Id: acme" \
  -d '{"inputs": {"invoice_id": "INV-001"}}' | jq
```

### Idempotent execution

```bash
curl -s -X POST http://localhost:8000/agent-actions/actions/issue_refund/execute/ \
  -H "X-Actor-Id: alice" \
  -H "X-Roles: finance" \
  -d '{"inputs": {"invoice_id": "INV-001", "reason": "duplicate"}, "idempotency_key": "refund-INV-001"}' | jq
```

Idempotency keys are scoped per `(action, key, tenant_id)`. Under concurrent
requests only one caller executes the action; the other receives the stored
result.

### Approve with approver identity

```bash
# Approver identity is read from X-Actor-Id on this request
curl -s -X POST http://localhost:8000/agent-actions/approvals/<id>/approve/ \
  -H "X-Actor-Id: manager-bob" | jq
```

The approver's identity is stored on the Approval row and in the audit log.

---

## Authentication and context

### Default: trust `X-*` headers (gateway / middleware pattern)

| Header | Field | Default |
|---|---|---|
| `X-Actor-Id` | `ctx.actor_id` | `"anonymous"` |
| `X-Roles` | `ctx.roles` | `[]` |
| `X-Tenant-Id` | `ctx.tenant_id` | `None` |

`ctx.authenticated` is `True` when `actor_id != "anonymous"`.

### Inline token validation with `AuthBackend`

```python
from django_agent_actions import AuthBackend

class ApiKeyBackend:
    def authenticate(self, credential: str) -> dict:
        # credential = raw "Authorization" header value
        if not credential.startswith("ApiKey "):
            raise PermissionError("Expected ApiKey scheme.")
        key = credential[len("ApiKey "):]
        identity = API_KEY_STORE.get(key)
        if identity is None:
            raise PermissionError("Unknown API key.")
        actor_id, roles, tenant_id = identity
        return {"actor_id": actor_id, "roles": roles, "tenant_id": tenant_id}
```

```python
# settings.py
AGENT_ACTIONS = {
    "AUTH_BACKEND": ApiKeyBackend(),
}
```

Invalid credentials return HTTP 401. No action code is reached.

---

## Policies

```python
from django_agent_actions import RoleBasedPolicy, RiskBasedPolicy, action

# Per-action override
@action(
    name="delete_invoice",
    description="Delete an invoice",
    risk="high",
    approval_required=True,
    required_scopes=["admin"],
    policy=RoleBasedPolicy(allowed_roles=["admin"]),
)
def delete_invoice(invoice_id: str):
    ...
```

Built-in policy rules:

| Class | Behaviour |
|---|---|
| `DefaultPolicy` | Always ALLOW |
| `DenyPolicy` | Always DENY |
| `RequireApprovalPolicy` | Always REQUIRE_APPROVAL |
| `RoleBasedPolicy(allowed_roles)` | ALLOW if actor holds a matching role, else DENY |
| `RiskBasedPolicy(risk_map)` | Map action risk level to a decision |

Implement the `PolicyRule` protocol to plug in OPA, Casbin, or any custom engine.

---

## Sensitive data redaction

All action inputs are redacted before being written to the audit log or stored
in approval records. The following field names are redacted regardless of
nesting depth:

`password`, `passwd`, `secret`, `token`, `api_key`, `access_token`,
`refresh_token`, `authorization`, `client_secret`, `private_key`,
`credential`, `ssn`, `credit_card`, `cvv`, and more.

Extend `SENSITIVE_KEYS` for application-specific fields:

```python
import django_agent_actions.redaction as r
r.SENSITIVE_KEYS = r.SENSITIVE_KEYS | {"my_secret_field"}
```

Raw `Authorization` header values are never stored on `RequestContext`.

---

## Project structure

```
django_agent_actions/
├── __init__.py        Public API
├── apps.py            Django AppConfig
├── models.py          Django ORM models (Approval, AuditLog, IdempotencyRecord)
├── migrations/        Database migrations
├── decorators.py      @action decorator
├── registry.py        ActionRegistry + ActionDef
├── context.py         RequestContext, AuthBackend, ContextResolver
├── redaction.py       Sensitive-field redaction
├── policies.py        PolicyEngine + built-in rules
├── runtime.py         ActionRuntime + module-level registry/get_runtime()
├── audit.py           AuditLogger
├── approvals.py       ApprovalService
├── idempotency.py     IdempotencyService (tenant-scoped)
├── views.py           Django views
└── urls.py            URL patterns
examples/
└── billing/           Full billing example (get, list, pay, refund)
tests/
├── settings.py        Test Django settings
├── conftest.py
├── test_registry.py
├── test_policy.py
├── test_approval.py
├── test_idempotency.py
└── test_concurrency.py
```

---

## Security model

| Concern | How it's handled |
|---|---|
| Auth | `AuthBackend` protocol; default trusts `X-*` headers from middleware |
| Anonymous access | Allowed by default; restrict with `RoleBasedPolicy` or `required_scopes` |
| Scope enforcement | `required_scopes` on `ActionDef`; checked before policy, audited on deny |
| Authorization | `PolicyRule` per action or app-level default |
| Approval safety | `pending → approved/rejected`; approver identity stored; no replay |
| Audit completeness | Every invoke path (allow/deny/approval/error) writes an audit record |
| Secret leakage | `redact_dict` applied to all inputs before storage |
| Tenant isolation | `tenant_id` in all audit/approval/idempotency records |
| Concurrent duplicate execution | DB-backed idempotency — same-key races execute side effects once |
| Approval race safety | Atomic DB updates — only one terminal outcome wins |
| Thread safety | Per-request context, per-operation DB queries, no shared request globals |

---

## Publishing

Releases are published to PyPI automatically via GitHub Actions using
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC).
No API tokens or secrets are required.

See [PUBLISHING.md](PUBLISHING.md) for setup instructions and the release process.

---

## Running tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

The test suite covers concurrency (idempotency races, approval races, context
isolation under parallel requests) and requires `pytest-django`.

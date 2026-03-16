"""Django views for the agent-actions HTTP API.

Include these URLs in your project::

    # urls.py
    from django.urls import path, include

    urlpatterns = [
        path("agent-actions/", include("django_agent_actions.urls")),
    ]

All endpoints read identity from request headers.  With no auth backend
configured, trust ``X-Actor-Id``, ``X-Roles``, and ``X-Tenant-Id`` headers
set by upstream middleware.

Endpoints
---------
GET  /agent-actions/health/
GET  /agent-actions/actions/
POST /agent-actions/actions/<name>/execute/
GET  /agent-actions/approvals/
POST /agent-actions/approvals/<pk>/approve/
POST /agent-actions/approvals/<pk>/reject/
GET  /agent-actions/audit-logs/
"""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from django_agent_actions.approvals import ApprovalAlreadyResolved, ApprovalNotFound
from django_agent_actions.runtime import get_runtime


def _json_body(request) -> dict:
    if not request.body:
        return {}
    try:
        return json.loads(request.body)
    except json.JSONDecodeError:
        return {}


def _approval_to_dict(a) -> dict:
    return {
        "id": str(a.id),
        "action_name": a.action_name,
        "actor_id": a.actor_id,
        "tenant_id": a.tenant_id,
        "status": a.status,
        "approver_id": a.approver_id,
        "created_at": a.created_at.isoformat(),
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "result": a.get_result(),
    }


def _audit_log_to_dict(log) -> dict:
    return {
        "id": log.id,
        "action_name": log.action_name,
        "actor_id": log.actor_id,
        "tenant_id": log.tenant_id,
        "input_payload": log.get_inputs(),
        "policy_decision": log.policy_decision,
        "status": log.status,
        "approval_id": log.approval_id,
        "approver_id": log.approver_id,
        "idempotency_key": log.idempotency_key,
        "result": log.get_result(),
        "created_at": log.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def health(request):
    return JsonResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def list_actions(request):
    runtime = get_runtime()
    return JsonResponse([a.to_schema_dict() for a in runtime.registry.list()], safe=False)


@method_decorator(csrf_exempt, name="dispatch")
class ExecuteActionView(View):
    def post(self, request, action_name: str):
        runtime = get_runtime()
        body = _json_body(request)
        raw_inputs = body.get("inputs", {})
        idempotency_key = body.get("idempotency_key") or None

        try:
            result = runtime.invoke(
                action_name=action_name,
                raw_inputs=raw_inputs,
                request=request,
                idempotency_key=idempotency_key,
            )
        except PermissionError:
            return JsonResponse({"detail": "Authentication required."}, status=401)
        except KeyError as exc:
            return JsonResponse({"detail": str(exc)}, status=404)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=422)
        except Exception:
            return JsonResponse({"detail": "Action execution failed."}, status=500)

        return JsonResponse(result.model_dump())


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def list_approvals(request):
    runtime = get_runtime()
    status_filter = request.GET.get("status") or None
    try:
        limit = int(request.GET.get("limit", 50))
        offset = int(request.GET.get("offset", 0))
    except ValueError:
        return JsonResponse({"detail": "Invalid limit or offset."}, status=400)

    items = runtime.approvals.list(status=status_filter, limit=limit, offset=offset)
    return JsonResponse([_approval_to_dict(a) for a in items], safe=False)


@method_decorator(csrf_exempt, name="dispatch")
class ApproveView(View):
    def post(self, request, pk: str):
        runtime = get_runtime()
        approver_ctx = runtime._context_resolver.resolve_request(request)
        try:
            result = runtime.invoke_approved(pk, approver_id=approver_ctx.actor_id)
        except ApprovalNotFound as exc:
            return JsonResponse({"detail": str(exc)}, status=404)
        except ApprovalAlreadyResolved as exc:
            return JsonResponse({"detail": str(exc)}, status=409)
        except Exception:
            return JsonResponse({"detail": "Approval execution failed."}, status=500)
        return JsonResponse(result.model_dump())


@method_decorator(csrf_exempt, name="dispatch")
class RejectView(View):
    def post(self, request, pk: str):
        runtime = get_runtime()
        approver_ctx = runtime._context_resolver.resolve_request(request)
        try:
            result = runtime.invoke_rejected(pk, approver_id=approver_ctx.actor_id)
        except ApprovalNotFound as exc:
            return JsonResponse({"detail": str(exc)}, status=404)
        except ApprovalAlreadyResolved as exc:
            return JsonResponse({"detail": str(exc)}, status=409)
        return JsonResponse(result.model_dump())


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------


def list_audit_logs(request):
    runtime = get_runtime()
    action_name = request.GET.get("action_name") or None
    actor_id = request.GET.get("actor_id") or None
    tenant_id = request.GET.get("tenant_id") or None
    try:
        limit = int(request.GET.get("limit", 50))
        offset = int(request.GET.get("offset", 0))
    except ValueError:
        return JsonResponse({"detail": "Invalid limit or offset."}, status=400)

    logs = runtime.audit_logger.list_logs(
        action_name=action_name,
        actor_id=actor_id,
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
    )
    return JsonResponse([_audit_log_to_dict(log) for log in logs], safe=False)

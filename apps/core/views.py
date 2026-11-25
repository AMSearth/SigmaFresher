"""Core views including healthcheck, CSP reporting, and authenticated dashboard."""

import json

import structlog
from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

logger = structlog.get_logger(__name__)

MAX_CSP_REPORT_BYTES = 16 * 1024


@login_not_required
@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    """Public readiness endpoint for container/load-balancer health checks."""
    return JsonResponse({"status": "ok"})


@login_not_required
@csrf_exempt
@require_POST
def csp_report(request: HttpRequest) -> HttpResponse:
    """Receive Content Security Policy violation reports safely."""
    if len(request.body) > MAX_CSP_REPORT_BYTES:
        return HttpResponse(status=413)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponse(status=400)
    logger.warning("csp_violation", report=payload)
    return HttpResponse(status=204)


@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    """Authenticated job seeker dashboard (protected by LoginRequiredMiddleware)."""
    profile = getattr(request.user, "profile", None)
    return render(
        request,
        "core/dashboard.html",
        {
            "profile": profile,
        },
    )

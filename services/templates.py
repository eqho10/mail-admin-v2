"""Template context builder with CSRF token injection.

Use _ctx(request, **kwargs) instead of {"request": request, ...} when calling
templates.TemplateResponse, so authenticated pages get the csrf_token in
context (rendered as <meta name="csrf-token"> in base.html).
"""
from fastapi import Request
from services.csrf import issue_token


def _ctx(request: Request, **kwargs) -> dict:
    """Build template context with CSRF token injected (if session present)."""
    ctx = {"request": request}
    sess = request.cookies.get("ma_sess", "")
    if sess:
        ctx["csrf_token"] = issue_token(sess)
    ctx.update(kwargs)
    return ctx

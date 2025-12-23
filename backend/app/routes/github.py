"""
GitHub API routes for repository information and status.

Provides endpoints for the frontend to display GitHub integration status.
"""

from fastapi import APIRouter
from typing import List, Dict, Any

from app.config import settings
from app.services.github_service import github_service

router = APIRouter(prefix="/api/github", tags=["github"])


@router.get("/repos")
async def list_repos() -> List[Dict[str, Any]]:
    """
    List all configured GitHub repositories.

    Returns repository information WITHOUT tokens for security.
    """
    if not settings.github_tools_enabled:
        return []

    repos = github_service.get_repos()
    return [repo.to_dict(include_token=False) for repo in repos]


@router.get("/rate-limit")
async def get_rate_limits() -> Dict[str, Any]:
    """
    Get current rate limit status for all configured repositories.

    Returns rate limit info keyed by repository label.
    """
    if not settings.github_tools_enabled:
        return {"enabled": False, "repos": {}}

    repos = github_service.get_repos()
    result = {
        "enabled": True,
        "repos": {},
    }

    for repo in repos:
        rate_info = github_service.check_rate_limit(repo.token)
        if rate_info:
            result["repos"][repo.label] = {
                "remaining": rate_info.remaining,
                "limit": rate_info.limit,
                "reset_timestamp": rate_info.reset_timestamp,
                "reset_time_formatted": rate_info.reset_time_formatted,
            }
        else:
            # No rate limit info yet (no requests made)
            result["repos"][repo.label] = {
                "remaining": None,
                "limit": None,
                "reset_timestamp": None,
                "reset_time_formatted": None,
                "status": "unknown",
            }

    return result

"""Plane REST API adapter — GET-only usage.

All HTTP calls go here; no business logic. Callers are responsible for
passing a valid API token (obtained via app.auth.plane.read_plane_token_from_env).
"""

from collections.abc import Callable
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_DEFAULT_PAGE_SIZE = 100


class PlaneAPIError(Exception):
    """Raised when the Plane API returns a non-2xx response or a network error."""


class PlaneClient:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        api_base: str = "/api/v1/workspaces",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_base = api_base.rstrip("/")
        self._headers = {"X-API-Key": api_token, "Accept": "application/json"}

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        stop_early: Callable[[list[dict]], bool] | None = None,
    ) -> list[dict]:
        """Paginate through results for a GET endpoint.

        stop_early: called after each page with that page's results. When it
                    returns True, pagination stops (the current page is kept).
        """
        url = f"{self._base_url}{path}"
        page = 0
        results: list[dict] = []

        while True:
            # Plane cursor format: {per_page}:{page_number}:0  (page-based, not offset)
            cursor = f"{_DEFAULT_PAGE_SIZE}:{page}:0"
            query = {**(params or {}), "per_page": _DEFAULT_PAGE_SIZE, "cursor": cursor}
            try:
                response = httpx.get(
                    url, headers=self._headers, params=query, timeout=30
                )
            except httpx.RequestError as exc:
                raise PlaneAPIError(f"Network error calling {url}: {exc}") from exc

            if response.status_code != 200:
                raise PlaneAPIError(
                    f"Plane API returned {response.status_code} for {url}: "
                    f"{response.text[:200]}"
                )

            data = response.json()
            page_results = data.get("results", [])
            results.extend(page_results)

            log.debug(
                "plane_api_page",
                url=url,
                page=page,
                count=len(page_results),
            )

            if stop_early and stop_early(page_results):
                break
            if not data.get("next_page_results", False):
                break
            page += 1

        return results

    def _get_plain(self, path: str) -> list[dict]:
        """Single GET for endpoints that return a plain JSON array (no pagination)."""
        url = f"{self._base_url}{path}"
        try:
            response = httpx.get(url, headers=self._headers, timeout=30)
        except httpx.RequestError as exc:
            raise PlaneAPIError(f"Network error calling {url}: {exc}") from exc
        if response.status_code != 200:
            raise PlaneAPIError(
                f"Plane API returned {response.status_code} for {url}: "
                f"{response.text[:200]}"
            )
        return response.json()

    def list_projects(self, workspace_slug: str) -> list[dict]:
        return self._get(f"{self._api_base}/{workspace_slug}/projects/")

    def list_modules(self, workspace_slug: str, project_id: str) -> list[dict]:
        return self._get(
            f"{self._api_base}/{workspace_slug}/projects/{project_id}/modules/"
        )

    def list_module_issues(
        self, workspace_slug: str, project_id: str, module_id: str
    ) -> list[dict]:
        return self._get(
            f"{self._api_base}/{workspace_slug}/projects/{project_id}/modules/{module_id}/module-issues/"
        )

    def list_members(self, workspace_slug: str) -> list[dict]:
        return self._get_plain(f"{self._api_base}/{workspace_slug}/members/")

    def get_states(self, workspace_slug: str, project_id: str) -> list[dict]:
        return self._get(
            f"{self._api_base}/{workspace_slug}/projects/{project_id}/states/"
        )

    def get_issues(
        self,
        workspace_slug: str,
        project_id: str,
        *,
        order_by: str | None = None,
        stop_early: Callable[[list[dict]], bool] | None = None,
    ) -> list[dict]:
        """Fetch issues for a project.

        order_by:   sort field, e.g. "-created_at" for newest-first.
        stop_early: called after each page; return True to stop pagination.
        """
        params: dict[str, Any] = {}
        if order_by:
            params["order_by"] = order_by
        return self._get(
            f"{self._api_base}/{workspace_slug}/projects/{project_id}/issues/",
            params=params,
            stop_early=stop_early,
        )

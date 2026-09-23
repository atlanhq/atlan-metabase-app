"""Shared HTTP-response guard for the extract modules.

A failed Metabase response is raised as a typed
:class:`~app.errors.MetabaseSourceUnavailableError` at the point it is
observed. Whether that failure aborts the run or is tolerated is decided by
each extract function's own ``except`` — the ones that tolerate it record a
residual (``app/residuals.py``) and return their empty/``None`` sentinel, so
the run reports ``PARTIAL_SUCCESS`` rather than a complete crawl.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.errors import MetabaseSourceUnavailableError

if TYPE_CHECKING:
    import httpx


def json_or_raise(response: Optional[httpx.Response], *, endpoint: str) -> Any:
    """Return the decoded JSON body of a successful Metabase response.

    Args:
        response: The response from ``MetabaseApiClient``, or ``None`` when
            the request produced no response at all.
        endpoint: The Metabase API path the request targeted — carried on
            the raised error and into the residual record.

    Returns:
        The decoded JSON body.

    Raises:
        MetabaseSourceUnavailableError: If there is no response or its status
            is not a success. ``http_status`` is ``None`` when there was no
            response or it carried no integer status.
    """
    if response is None or not response.is_success:
        status = response.status_code if response is not None else None
        raise MetabaseSourceUnavailableError(
            message="Metabase API request failed.",
            source_type="metabase",
            endpoint=endpoint,
            http_status=status if isinstance(status, int) else None,
        )
    return response.json()

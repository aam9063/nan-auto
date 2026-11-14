"""The real Clef adapter: one HTTP call, a 2-slot semaphore and 429 retries.

NaN allows at most two in-flight requests per person (spec 6), so the
semaphore is not a nicety: exceeding it earns a 429. A decision that cannot be
obtained raises, and the caller fails open to the profile default (NAU-11).
"""

import asyncio

import httpx

from nan_auto.clef_client.parser import parse_decision
from nan_auto.clef_client.types import Decision

DEFAULT_URL = "https://api.nan.builders/v1/systemone"


class ClefError(RuntimeError):
    """Base class for Clef failures the router can fall open on."""


class ClefUnavailable(ClefError):
    """Clef did not answer usefully: timeout, 429 sustained or a 5xx."""


class ClefAuthError(ClefError):
    """The key was rejected; retrying cannot help."""


class NanDecisionProvider:
    """`DecisionProvider` backed by the real `/v1/systemone` endpoint."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        url: str = DEFAULT_URL,
        max_in_flight: int = 2,
        timeout: float = 0.8,
        max_retries: int = 5,
        retry_backoff: float = 0.2,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._url = url
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._slots = asyncio.Semaphore(max_in_flight)

    async def decide(self, *, state: str, questions: dict) -> Decision:
        """Ask Clef for one decision within the concurrency limit."""
        body = {"model": "clef", "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async with self._slots:
            last_status = 0
            for attempt in range(self._max_retries):
                try:
                    response = await self._client.post(
                        self._url, json=body, headers=headers, timeout=self._timeout
                    )
                except httpx.TimeoutException as exc:
                    raise ClefUnavailable(f"Clef timeout after {self._timeout}s") from exc
                except httpx.HTTPError as exc:
                    raise ClefUnavailable(f"Clef transport error: {exc}") from exc

                if response.status_code == 401:
                    raise ClefAuthError("Clef rejected the API key (401)")
                if response.status_code == 429:
                    last_status = 429
                    if attempt == self._max_retries - 1:
                        break
                    await asyncio.sleep(self._retry_backoff)
                    continue
                if response.status_code >= 500:
                    raise ClefUnavailable(f"Clef returned {response.status_code}")
                if response.status_code >= 400:
                    raise ClefUnavailable(
                        f"Clef returned {response.status_code}: {response.text[:200]}"
                    )
                return parse_decision(response.json())

            raise ClefUnavailable(
                f"Clef 429 sustained after {self._max_retries} attempts (last {last_status})"
            )

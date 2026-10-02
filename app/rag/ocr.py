"""Read a page image with a vision model, and never read the same one twice.

Design forced by measurement
----------------------------
A single book here is 226 pages needing OCR, each taking about 13 seconds, on
a free model that returns `429 rate-limited` without warning. That rules out
the obvious implementation - a loop that calls the API and holds everything in
memory - because it would lose an hour of work to one dropped connection.

So three properties are built in from the start:

* **Cached.** Every page is written to disk the moment it is read. A second
  run of the same book costs nothing and touches no network.
* **Resumable.** Work is keyed by the image's own hash, so an interrupted run
  continues where it stopped, and re-ingesting after changing the *chunking*
  does not re-read a single page.
* **Patient.** `429` is the expected case on a free model, not an error. The
  client backs off and retries, because ingestion is offline and nobody is
  waiting. That is exactly the trade ADR-016 makes: free where a retry costs
  only time, paid where latency matters.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import time
from pathlib import Path

import httpx

from app.config import get_settings
from app.core.logging import get_logger
from app.rag.latex_unwrap import unwrap
from app.rag.quality import check_page, strip_preamble

log = get_logger(__name__)

PROMPT = """Transcribe all text on this textbook page exactly as it appears.

Rules:
- The page is in Arabic. Every Arabic word must stay Arabic. Never translate
  a word into English, not even a technical term: write التابع, never
  "function" or "follow".
- Write mathematics in LaTeX: $...$ inline, $$...$$ on its own line.
- Do NOT wrap the whole page in one LaTeX environment. Arabic prose is prose;
  only formulas belong inside $ delimiters.
- Keep the numbering of exercises and sections exactly as printed.
- If something is unreadable, write [غير واضح] rather than guessing.
- Output the transcription only: no commentary, no summary, no translation.
"""


class OcrError(RuntimeError):
    """Raised when a page could not be read after every retry."""


# Bumped whenever a change to the post-processing would produce different text
# from the same page: preamble stripping, LaTeX unwrapping, normalisation. The
# cache records it, and a record from an older version is treated as a miss.
# Found the need the hard way - pages cached before `unwrap` existed sat in the
# cache as egin{tabular} blobs and would never have been re-read.
PIPELINE_VERSION = 2


class PageCache:
    """One JSON file per page, tagged with the pipeline that produced it."""

    def __init__(self, root: str | Path, document_id: str) -> None:
        self.dir = Path(root) / document_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, page_number: int) -> Path:
        return self.dir / f"page_{page_number:04d}.json"

    def get(self, page_number: int, image_sha: str) -> str | None:
        """Return the cached text, or None.

        `image_sha` may be the hash of the source *document* rather than of
        the page image. That matters for speed: extracting a page image from
        this PDF costs about 1.5 seconds, so checking the cache by image hash
        meant paying 6 minutes of extraction to discover a full cache. Keyed
        on the document instead, a resumed run reads nothing it already has.
        """
        path = self._path(page_number)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A half-written file from an interrupted run. Treat it as absent
            # rather than crashing the whole ingestion.
            log.warning("ocr.cache_unreadable", page=page_number)
            return None
        # The hash is what makes this safe: a re-scanned or replaced page
        # produces a different image and is read again.
        if record.get("image_sha") != image_sha:
            return None
        if record.get("pipeline_version") != PIPELINE_VERSION:
            log.info("ocr.cache_stale", page=page_number)
            return None
        return record.get("text")

    def put(self, page_number: int, image_sha: str, text: str, model: str) -> None:
        self._path(page_number).write_text(
            json.dumps(
                {
                    "page": page_number,
                    "image_sha": image_sha,
                    "pipeline_version": PIPELINE_VERSION,
                    "model": model,
                    "text": text,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


class VisionOcr:
    """Reads page images through an OpenRouter vision model."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        settings = get_settings()
        key = settings.openrouter_api_key.get_secret_value()
        if not key:
            raise OcrError("OPENROUTER_API_KEY is not set - OCR needs it.")
        self._models = settings.ocr_model_list
        self._max_retries = settings.ocr_max_retries
        self._min_arabic = settings.ocr_min_arabic_ratio
        self._client = client or httpx.Client(
            base_url=settings.openrouter_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(300.0, connect=10.0),
        )

    def read(self, image_png: bytes) -> str:
        """Transcribe one page, trying each model in the chain in turn.

        Returns the first transcription that passes the quality gate. Raises
        `OcrError` only when every model in the chain has been exhausted -
        which the caller should record and move past, not treat as fatal. One
        unreadable page out of 226 is a page to review by hand, not a reason
        to abandon a book.
        """
        b64 = base64.b64encode(image_png).decode()
        problems: list[str] = []

        for model in self._models:
            try:
                return self._read_with(model, b64)
            except OcrError as exc:
                log.warning("ocr.model_exhausted", model=model, reason=str(exc))
                problems.append(f"{model}: {exc}")

        raise OcrError("every model failed -> " + " | ".join(problems))

    def _read_with(self, model: str, b64: str) -> str:
        payload = {
            "model": model,
            # Generous: a dense page of exercises produced ~950 tokens, and a
            # truncated transcription is worse than none because it looks
            # complete.
            "max_tokens": 8000,
            # The first attempt at this spent 1876 tokens thinking and ran out
            # of budget before writing a single line of output.
            "reasoning": {"enabled": False},
            "usage": {"include": True},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
        }

        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.RequestError as exc:
                # A dropped connection or a DNS blip is exactly as transient
                # as a 429, and over an hour-long run it is just as likely.
                # Found the hard way: a `getaddrinfo failed` ended a run that
                # had already survived the rate limiter.
                log.info(
                    "ocr.network_retry",
                    model=model,
                    error=type(exc).__name__,
                    attempt=attempt,
                )
                self._sleep(attempt)
                continue

            if response.status_code == 200:
                try:
                    choice = response.json()["choices"][0]
                except (KeyError, IndexError, ValueError):
                    # A 200 carrying an error body. Seen from more than one
                    # free provider; crashing on it would end the run.
                    log.warning("ocr.malformed_response", model=model)
                    self._sleep(attempt)
                    continue

                # Order matters: drop the commentary, recover prose from
                # LaTeX packaging, and only then judge what is left.
                text = unwrap(strip_preamble(choice["message"].get("content") or ""))
                problem = check_page(text, self._min_arabic)
                if problem is not None:
                    # A transcription that is visibly wrong is worse than a
                    # missing one: it gets embedded, indexed, and silently
                    # fails to match anything for the rest of the system's
                    # life. Retrying is cheap; a poisoned index is not.
                    log.warning(
                        "ocr.rejected",
                        model=model,
                        reason=str(problem),
                        attempt=attempt,
                    )
                    self._sleep(attempt)
                    continue
                return text

            if response.status_code in (429, 500, 502, 503, 504):
                log.info(
                    "ocr.retrying",
                    model=model,
                    status=response.status_code,
                    attempt=attempt,
                )
                self._sleep(attempt, response.headers.get("retry-after"))
                continue

            raise OcrError(f"HTTP {response.status_code}: {response.text[:200]}")

        raise OcrError(f"{self._max_retries} attempts exhausted")

    @staticmethod
    def _sleep(attempt: int, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 120))
                return
            except ValueError:
                pass
        # Exponential, with jitter so a resumed run does not march in lockstep
        # with whatever rate limiter refused it.
        delay = min(2**attempt, 60) + random.uniform(0, 2)
        time.sleep(delay)


def image_sha(image_png: bytes) -> str:
    return hashlib.sha256(image_png).hexdigest()[:16]

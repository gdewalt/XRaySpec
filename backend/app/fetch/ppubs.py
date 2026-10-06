"""USPTO Patent Public Search (PPUBS) document retrieval.

Uses the same public session, exact-number search, HTML text, and PDF endpoints
as PPUBS Basic Search. The restricted fetcher still validates every network hop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote

from ..patents import PatentIdentity
from .adapter import FetchResult, RestrictedFetcher
from .errors import FetchError

_BASE = "https://ppubs.uspto.gov/api"


@dataclass(frozen=True, slots=True)
class PpubsDocument:
    document_id: str
    patent_number: str
    title: str | None
    source: str
    page_count: int | None


class PpubsClient:
    def __init__(self, fetcher: RestrictedFetcher, *, max_json_bytes: int) -> None:
        self._fetcher = fetcher
        self._max_json_bytes = max_json_bytes
        self._token: str | None = None

    async def _session_token(self) -> str:
        if self._token:
            return self._token
        response = await self._fetcher.request(
            f"{_BASE}/users/me/session",
            method="POST",
            headers={"content-type": "application/json; charset=utf-8"},
            content=b"-1",
            max_bytes=self._max_json_bytes,
        )
        token = response.headers.get("x-access-token")
        if not token:
            raise FetchError("ppubs_session", "PPUBS did not return an access token")
        self._token = token
        return token

    async def resolve(self, identity: PatentIdentity) -> PpubsDocument:
        token = await self._session_token()
        payload = json.dumps(
            {
                "cursorMarker": "*",
                "databaseFilters": [
                    {"databaseName": "USPAT"},
                    {"databaseName": "US-PGPUB"},
                    {"databaseName": "USOCR"},
                ],
                "fields": [
                    "documentId",
                    "patentNumber",
                    "title",
                    "datePublished",
                    "pageCount",
                    "type",
                ],
                "op": "OR",
                "pageSize": 20,
                "q": f"({identity.number}).pn.",
                "searchType": 0,
                "sort": "date_publ desc",
            },
            separators=(",", ":"),
        ).encode()
        response = await self._fetcher.request(
            f"{_BASE}/searches/generic",
            method="POST",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "x-access-token": token,
            },
            content=payload,
            max_bytes=self._max_json_bytes,
        )
        try:
            docs = json.loads(response.content).get("docs", [])
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
            raise FetchError("ppubs_malformed", "PPUBS returned invalid search data") from exc

        expected_source = "US-PGPUB" if identity.doc_type == "application" else "USPAT"
        matching: list[dict] = []
        for doc in docs:
            if str(doc.get("patentNumber", "")).lstrip("0") != identity.number.lstrip("0"):
                continue
            document_id = str(doc.get("documentId", ""))
            if identity.kind and not document_id.upper().endswith(f"-{identity.kind}"):
                continue
            matching.append(doc)
        if not matching:
            raise FetchError("not_found", "PPUBS did not find that patent publication")
        matching.sort(
            key=lambda doc: (
                doc.get("type") != expected_source,
                doc.get("type") == "USOCR",
            )
        )
        selected = matching[0]
        return PpubsDocument(
            document_id=str(selected["documentId"]),
            patent_number=str(selected["patentNumber"]),
            title=str(selected["title"]).strip() if selected.get("title") else None,
            source=str(selected["type"]),
            page_count=(
                int(selected["pageCount"])
                if selected.get("pageCount") is not None
                else None
            ),
        )

    def _document_url(self, path: str, document: PpubsDocument, token: str) -> str:
        return (
            f"{_BASE}/{path}/{quote(document.document_id, safe='')}"
            f"?source={quote(document.source, safe='')}&requestToken={quote(token, safe='')}"
        )

    async def fetch_text(self, document: PpubsDocument, *, max_bytes: int) -> FetchResult:
        token = await self._session_token()
        return await self._fetcher.request(
            self._document_url("patents/html", document, token),
            headers={"x-access-token": token},
            max_bytes=max_bytes,
        )

    async def fetch_pdf(self, document: PpubsDocument, *, max_bytes: int) -> FetchResult:
        token = await self._session_token()
        return await self._fetcher.request(
            self._document_url("pdf/downloadPdf", document, token),
            headers={"x-access-token": token},
            max_bytes=max_bytes,
            expect_pdf=True,
        )

"""The HTTP API: the capture app's requests plus processing status and results.

Request and response bodies are the JSON shapes in the Android app spec
(``.kiro/specs/android-app/design.md``, "Server contract"). Every error body
is ``{"error": "<code>", "message": "<text>"}``.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from albumproc.printsize import parse_page_size

from . import __version__
from .auth import Authenticator
from .config import Settings
from .files import Layout, TooLarge, Upload, fsync_dir, remove_tree
from .store import Conflict, Invalid, LimitExceeded, Store, iso, utcnow
from .thumbs import exif_taken, is_jpeg, make_thumb

log = logging.getLogger("albumserver")

SHA_HEADER = "X-Content-SHA256"
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".tif": "image/tiff", ".tiff": "image/tiff"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status, headers=headers)


def _id(value: str, what: str) -> str:
    """A UUID path id, lower-cased; 400 for anything else."""
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        canonical = None
    if canonical is None or canonical != value.lower():
        raise ApiError(400, "bad_id", f"{what} id {value!r} is not a UUID")
    return canonical


# What the app sends (Kotlin's Instant.toString()): any number of fractional digits.
_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})")


def _check_time(value: str) -> None:
    m = _TIME_RE.fullmatch(value)
    if not m:
        raise ValueError(value)
    datetime.fromisoformat(m[1])  # a real date and time


class AlbumIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr
    page_size: StrictStr
    pages: list[StrictStr]
    created: StrictStr

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("page_size")
    @classmethod
    def _size(cls, v: str) -> str:
        parse_page_size(v)  # its ValueError names the problem
        return v

    @field_validator("pages")
    @classmethod
    def _pages(cls, v: list[str]) -> list[str]:
        out = []
        for i, p in enumerate(v):
            try:
                out.append(_id(p, "page"))
            except ApiError as e:
                raise ValueError(f"[{i}]: {e.message}") from None
        if len(set(out)) != len(out):
            dup = next(p for p in out if out.count(p) > 1)
            raise ValueError(f"page {dup} is listed twice")
        return out

    @field_validator("created")
    @classmethod
    def _created(cls, v: str) -> str:
        try:
            _check_time(v)
        except ValueError:
            raise ValueError(f"{v!r} is not an ISO 8601 time") from None
        return v


class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shots: list[StrictStr]

    @field_validator("shots")
    @classmethod
    def _shots(cls, v: list[str]) -> list[str]:
        if not 1 <= len(v) <= 25:
            raise ValueError("must list 1 to 25 shot ids")
        out = []
        for i, s in enumerate(v):
            try:
                out.append(_id(s, "shot"))
            except ApiError as e:
                raise ValueError(f"[{i}]: {e.message}") from None
        if len(set(out)) != len(out):
            dup = next(s for s in out if out.count(s) > 1)
            raise ValueError(f"shot {dup} is listed twice")
        return out


def _validation_message(e: ValidationError | RequestValidationError) -> str:
    parts = []
    for err in e.errors():
        where = ".".join(str(x) for x in err["loc"] if x != "body") or "body"
        msg = err["msg"].removeprefix("Value error, ")
        if err["type"] == "extra_forbidden":
            msg = "unknown key"
        elif err["type"] == "missing":
            msg = "required"
        parts.append(f"{where}: {msg}")
    return "; ".join(parts)


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    tags = [t.strip() for t in header.split(",")]
    return "*" in tags or etag in tags or f"W/{etag}" in tags


class Gate:
    """Outermost layer: token check, body size limit and one log line per request.

    Pure ASGI, so a rejected request is answered before any of its body is
    read, and so it sees every request, including ones no route matches.
    """

    def __init__(self, app, auth: Authenticator, max_body: int):
        self.app = app
        self.auth = auth
        self.max_body = max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        start = time.monotonic()
        result = {"status": 500, "bytes": 0}

        async def logged_send(message):
            if message["type"] == "http.response.start":
                result["status"] = message["status"]
            elif message["type"] == "http.response.body":
                result["bytes"] += len(message.get("body", b""))
            await send(message)

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        client = scope.get("client")
        client = f"{client[0]}:{client[1]}" if client else "unknown"
        try:
            auth = headers.get(b"authorization")
            name = await run_in_threadpool(self.auth.check, auth.decode("latin-1") if auth else None)
            if name is None:
                log.warning("rejected request from %s: %s token", client, "invalid" if auth else "missing")
                resp = _error(401, "unauthorized", "a valid token is required", {"WWW-Authenticate": "Bearer"})
                return await resp(scope, receive, logged_send)
            length = headers.get(b"content-length")
            if length is not None:
                try:
                    n = int(length)
                except ValueError:
                    return await _error(400, "bad_request", "invalid Content-Length")(scope, receive, logged_send)
                if n > self.max_body:
                    msg = f"the body is {n} bytes; the limit is {self.max_body}"
                    return await _error(413, "too_large", msg, {"Connection": "close"})(scope, receive, logged_send)
            scope.setdefault("state", {})["token_name"] = name
            await self.app(scope, receive, logged_send)
        finally:
            ms = (time.monotonic() - start) * 1000
            path = scope.get("path", "")
            log.info("%s %s %d %d %.1fms", scope.get("method"), path, result["status"], result["bytes"], ms)


def create_app(settings: Settings, store: Store | None = None, clock: Callable[[], datetime] = utcnow) -> FastAPI:
    layout = Layout(settings.data_dir)
    layout.create()
    store = store or Store(layout.index, clock, settings.max_pages, settings.max_shots)
    max_body = settings.max_body_bytes

    app = FastAPI(title="albumserver", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.layout = layout
    app.add_middleware(Gate, auth=Authenticator(store), max_body=max_body)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, e: ApiError):
        return _error(e.status, e.code, e.message)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(request: Request, e: RequestValidationError):
        return _error(400, "bad_request", _validation_message(e))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, e: StarletteHTTPException):
        codes = {404: "not_found", 405: "method_not_allowed"}
        return _error(e.status_code, codes.get(e.status_code, "error"), str(e.detail))

    api = "/api/v1"

    def not_found(what: str) -> ApiError:
        return ApiError(404, "not_found", f"{what} not found")

    # -- albums -----------------------------------------------------------

    @app.get(f"{api}/ping")
    def ping():
        return {"status": "ok", "version": __version__}

    @app.get(f"{api}/albums")
    def list_albums():
        return {"albums": store.list_albums()}

    @app.get(f"{api}/albums/{{album_id}}")
    def get_album(album_id: str):
        a = store.get_album(_id(album_id, "album"))
        if a is None:
            raise not_found("album")
        return a

    @app.put(f"{api}/albums/{{album_id}}")
    async def put_album(album_id: str, request: Request):
        aid = _id(album_id, "album")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > max_body:
                raise ApiError(413, "too_large", f"the body is over the {max_body}-byte limit")
        try:
            meta = AlbumIn.model_validate_json(bytes(body))
        except ValidationError as e:
            raise ApiError(400, "bad_request", _validation_message(e)) from None
        try:
            await run_in_threadpool(store.put_album, aid, meta.name, meta.page_size, meta.pages, meta.created)
        except Invalid as e:
            raise ApiError(400, "bad_request", str(e)) from None
        except LimitExceeded as e:
            raise ApiError(422, e.limit, str(e)) from None
        return await run_in_threadpool(store.get_album, aid)

    @app.delete(f"{api}/albums/{{album_id}}", status_code=204)
    def delete_album(album_id: str):
        aid = _id(album_id, "album")
        store.delete_album(aid)
        # Removed from the index first: the worker stops any run of it when it
        # next looks, and a crash before this line leaves only unindexed files.
        remove_tree(layout.album(aid))
        return Response(status_code=204)

    # -- pages ------------------------------------------------------------

    @app.get(f"{api}/albums/{{album_id}}/pages/{{page_id}}")
    def get_page(album_id: str, page_id: str):
        p = store.get_page(_id(album_id, "album"), _id(page_id, "page"))
        if p is None:
            raise not_found("page")
        return p

    @app.delete(f"{api}/albums/{{album_id}}/pages/{{page_id}}", status_code=204)
    def delete_page(album_id: str, page_id: str):
        aid, pid = _id(album_id, "album"), _id(page_id, "page")
        removed = store.delete_page(aid, pid)
        if removed:
            remove_tree(layout.photos(aid, pid))
            for sid in removed.shot_ids:
                layout.thumb(aid, sid).unlink(missing_ok=True)
            remove_tree(layout.outputs(aid, pid))
            remove_tree(layout.work(aid, pid))
        return Response(status_code=204)

    @app.post(f"{api}/albums/{{album_id}}/pages/{{page_id}}/move", status_code=204)
    async def move_shots(album_id: str, page_id: str, request: Request):
        aid, pid = _id(album_id, "album"), _id(page_id, "page")
        try:
            body = MoveIn.model_validate_json(await request.body())
        except ValidationError as e:
            raise ApiError(400, "bad_request", _validation_message(e)) from None

        def link(moves: list[tuple[str, str]]) -> None:
            # A second name for each file in the new page's folder; the old
            # name goes once the move is committed.
            dest = layout.photos(aid, pid)
            dest.mkdir(parents=True, exist_ok=True)
            for sid, src in moves:
                try:
                    os.link(layout.photo(aid, src, sid), layout.photo(aid, pid, sid))
                except FileExistsError:
                    pass  # left by a move that crashed before its commit
                except FileNotFoundError:
                    log.warning("moving shot %s whose photo is missing", sid)
            fsync_dir(dest)

        try:
            moved = await run_in_threadpool(store.move_shots, aid, pid, body.shots, link)
        except Conflict as e:
            raise ApiError(409, "conflict", str(e)) from None
        except LimitExceeded as e:
            raise ApiError(422, e.limit, str(e)) from None
        if moved is None:
            raise not_found("album")
        for sid, src in moved.moves:
            layout.photo(aid, src, sid).unlink(missing_ok=True)
        for src in moved.emptied:
            remove_tree(layout.outputs(aid, src))
        return Response(status_code=204)

    # -- shots ------------------------------------------------------------

    def shot_json(s: dict) -> dict:
        return {"id": s["id"], "sha256": s["sha256"], "bytes": s["bytes"], "taken": s["taken"]}

    @app.put(f"{api}/albums/{{album_id}}/pages/{{page_id}}/shots/{{shot_id}}")
    async def put_shot(album_id: str, page_id: str, shot_id: str, request: Request):
        aid, pid, sid = _id(album_id, "album"), _id(page_id, "page"), _id(shot_id, "shot")
        claimed = request.headers.get(SHA_HEADER, "").strip().lower()
        if not _SHA_RE.fullmatch(claimed):
            raise ApiError(400, "bad_hash", f"{SHA_HEADER} must be the body's SHA-256 in hex")

        # A repeat is answered before reading the body: retries cost nothing.
        existing = await run_in_threadpool(store.get_shot, sid)
        if existing is not None:
            if existing["sha256"] == claimed:
                return JSONResponse(shot_json(existing), status_code=200)
            raise ApiError(409, "conflict", f"shot {sid} is already stored with a different hash")

        up = None
        try:
            up = await run_in_threadpool(Upload, layout.photo(aid, pid, sid), max_body)
            try:
                async for chunk in request.stream():
                    up.write(chunk)
            except TooLarge:
                raise ApiError(413, "too_large", f"the body is over the {max_body}-byte limit") from None
            digest = await run_in_threadpool(up.finish)
            if digest != claimed:
                raise ApiError(400, "hash_mismatch", f"the body's SHA-256 is {digest}, not {claimed}")
            if not await run_in_threadpool(is_jpeg, up.tmp):
                raise ApiError(400, "not_jpeg", "the body is not a JPEG")
            taken = await run_in_threadpool(exif_taken, up.tmp)
            taken_s = iso(taken or clock())
            try:
                created = await run_in_threadpool(store.add_shot, aid, pid, sid, claimed, up.size, taken_s, up.place)
            except Conflict as e:
                raise ApiError(409, "conflict", str(e)) from None
            except LimitExceeded as e:
                raise ApiError(422, e.limit, str(e)) from None
        except OSError as e:
            if e.errno in (errno.ENOSPC, errno.EDQUOT):
                raise ApiError(507, "insufficient_storage", "the server's disk is full") from None
            raise
        finally:
            if up is not None:
                up.discard()
        shot = await run_in_threadpool(store.get_shot, sid)
        return JSONResponse(shot_json(shot), status_code=201 if created else 200)

    @app.get(f"{api}/albums/{{album_id}}/pages/{{page_id}}/shots/{{shot_id}}")
    def get_shot(album_id: str, page_id: str, shot_id: str, request: Request, size: str | None = None):
        aid, pid, sid = _id(album_id, "album"), _id(page_id, "page"), _id(shot_id, "shot")
        if size not in (None, "thumb"):
            raise ApiError(400, "bad_request", "size must be 'thumb' when given")
        s = store.get_shot(sid)
        if s is None or s["page_id"] != pid or s["album_id"] != aid:
            raise not_found("shot")
        path = layout.photo(aid, pid, sid)
        headers = {}
        if size == "thumb":
            etag = f'"{s["sha256"]}-thumb"'
            thumb = layout.thumb(aid, sid)
            if not thumb.exists() and not _etag_matches(request.headers.get("if-none-match"), etag):
                try:
                    make_thumb(path, thumb)
                except FileNotFoundError:
                    raise not_found("shot") from None
            path = thumb
        else:
            etag = f'"{s["sha256"]}"'
            headers[SHA_HEADER] = s["sha256"]
        headers["ETag"] = etag
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        if not path.is_file():
            raise not_found("shot file")
        return FileResponse(path, media_type="image/jpeg", headers=headers)

    @app.delete(f"{api}/albums/{{album_id}}/pages/{{page_id}}/shots/{{shot_id}}", status_code=204)
    def delete_shot(album_id: str, page_id: str, shot_id: str):
        aid, pid, sid = _id(album_id, "album"), _id(page_id, "page"), _id(shot_id, "shot")
        removed = store.delete_shot(aid, pid, sid)
        if removed:
            layout.photo(aid, pid, sid).unlink(missing_ok=True)
            layout.thumb(aid, sid).unlink(missing_ok=True)
            if removed.outputs_dropped:
                remove_tree(layout.outputs(aid, pid))
        return Response(status_code=204)

    # -- results ----------------------------------------------------------

    @app.get(f"{api}/albums/{{album_id}}/status")
    def status(album_id: str):
        st = store.album_status(_id(album_id, "album"))
        if st is None:
            raise not_found("album")
        return st

    @app.get(f"{api}/albums/{{album_id}}/pdf")
    def pdf(album_id: str):
        aid = _id(album_id, "album")
        path = layout.pdf(aid)
        if store.pdf_built(aid) is None or not path.is_file():
            raise not_found("PDF")
        return FileResponse(path, media_type="application/pdf", filename="album.pdf")

    @app.get(f"{api}/albums/{{album_id}}/pages/{{page_id}}/print")
    def print_image(album_id: str, page_id: str):
        aid, pid = _id(album_id, "album"), _id(page_id, "page")
        rel = store.page_print(aid, pid)
        path = layout.album(aid) / rel if rel else None
        if path is None or not path.is_file():
            raise not_found("print image")
        return FileResponse(path, media_type=_MEDIA.get(path.suffix.lower(), "application/octet-stream"))

    return app

"""Command line.

``albumserver serve`` runs the server and its processing worker,
``albumserver token create|list|revoke`` manages the phones' tokens, and
``albumserver check`` compares the index with the data folder.
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import multiprocessing
import os
import signal
import sys

from .config import Settings, SettingsError, load_settings
from .files import Layout, check, cleanup
from .store import Conflict, Store

log = logging.getLogger("albumserver")


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not any(getattr(h, "_albumserver", False) for h in root.handlers):
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(processName)s: %(message)s"))
        h._albumserver = True
        root.addHandler(h)
    root.setLevel(level)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _open(settings: Settings) -> tuple[Layout, Store]:
    layout = Layout(settings.data_dir)
    layout.create()
    return layout, Store(layout.index, max_pages=settings.max_pages, max_shots=settings.max_shots)


def prepare(settings: Settings) -> tuple[Layout, Store]:
    """Open the data folder for serving: interrupted runs reset, unindexed files removed."""
    layout, store = _open(settings)
    if n := store.recover():
        log.info("%d interrupted page run(s) will run again", n)
    cleanup(layout, store.snapshot())
    if not settings.tls and not _is_loopback(settings.host):
        log.warning(
            "listening on %s over plain HTTP: tokens and photos travel unencrypted; "
            "set tls_cert and tls_key, or put the server behind Tailscale or a TLS proxy",
            settings.host,
        )
    return layout, store


def _cmd_serve(settings: Settings, a) -> int:
    import uvicorn

    from .api import create_app
    from .worker import worker_main

    _, store = prepare(settings)
    app = create_app(settings, store)
    ctx = multiprocessing.get_context("spawn")
    stop = ctx.Event()
    worker = ctx.Process(target=worker_main, args=(settings, stop, os.getpid()), name="worker")
    worker.start()
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        ssl_certfile=str(settings.tls_cert) if settings.tls_cert else None,
        ssl_keyfile=str(settings.tls_key) if settings.tls_key else None,
        access_log=False,  # the API logs its own line per request, without headers
        log_config=None,
        timeout_graceful_shutdown=30,
    )
    # uvicorn stops accepting on SIGTERM or SIGINT, finishes requests in
    # progress, then raises the signal again under the handlers it found.
    # With these in place that is harmless, and the worker is stopped below
    # instead of the process dying with it still running.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: None)
    try:
        uvicorn.Server(config).run()
    finally:
        stop.set()
        worker.join(60)
        if worker.is_alive():
            worker.terminate()
            worker.join()
        store.close()
    log.info("server stopped")
    return 0


def _cmd_token(settings: Settings, a) -> int:
    from .auth import create_token

    _, store = _open(settings)
    if a.action == "create":
        try:
            token = create_token(store, a.name)
        except (Conflict, ValueError) as e:
            print(e, file=sys.stderr)
            return 1
        print(f"Token for {a.name!r} (shown only now; enter it in the app's settings):", file=sys.stderr)
        print(token)
    elif a.action == "list":
        rows = store.list_tokens()
        if not rows:
            print("no tokens", file=sys.stderr)
        for r in rows:
            print(f"{r['name']}\tcreated {r['created']}\tlast used {r['last_used'] or 'never'}")
    else:
        if not store.revoke_token(a.name):
            print(f"no token named {a.name!r}", file=sys.stderr)
            return 1
        print(f"revoked {a.name!r}", file=sys.stderr)
    return 0


def _cmd_check(settings: Settings, a) -> int:
    layout, store = _open(settings)
    found = check(layout, store.snapshot())
    for label, items in (("missing (indexed, no file)", found.missing), ("not in the index", found.extra), ("changed since upload", found.changed)):
        for item in items:
            print(f"{label}: {item}")
    if found.ok:
        print("ok: index and data folder agree", file=sys.stderr)
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="albumserver", description="Stores albums from the capture app and keeps their print images and PDF current.")
    ap.add_argument("--config", help="TOML settings file; ALBUMSERVER_* environment variables override it")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the server and its processing worker")
    tok = sub.add_parser("token", help="manage the phones' access tokens")
    tsub = tok.add_subparsers(dest="action", required=True)
    tsub.add_parser("create", help="make a token and print it once").add_argument("name")
    tsub.add_parser("list", help="list tokens (never the tokens themselves)")
    tsub.add_parser("revoke", help="revoke a token").add_argument("name")
    sub.add_parser("check", help="report index entries without files, unindexed files and changed photos")
    a = ap.parse_args(argv)

    setup_logging()
    try:
        settings = load_settings(a.config)
    except SettingsError as e:
        print(f"albumserver: {e}", file=sys.stderr)
        return 2
    return {"serve": _cmd_serve, "token": _cmd_token, "check": _cmd_check}[a.cmd](settings, a)


if __name__ == "__main__":
    sys.exit(main())

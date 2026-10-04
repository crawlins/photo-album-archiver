"""The processing worker: runs the album step page by page and assembles each album's PDF.

It runs in its own process, started by ``albumserver serve``, and learns
about changes only from the index. Each pass of its loop:

1. collects finished page runs and records them;
2. stops runs whose page or album was deleted (and, with ``cancel_runs``,
   runs whose page changed);
3. makes pages ready once they have been idle for ``idle_period_s``;
4. starts the oldest ready pages, up to ``workers`` at a time, each in a
   child process;
5. builds the PDF of every album whose PDF is out of date and that has no
   page waiting to run.

A page run works on a one-page album folder of hard links to the page's
photos and writes into a fresh output folder seeded with hard links to the
page's previous outputs, so the album step can reuse them. The new folder
replaces the old one in the index only when the run ends, so the previous
print image is served throughout the run, and a stopped run is discarded by
deleting its folder.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from .config import Settings
from .files import Layout, remove_tree, write_atomic
from .store import Claim, Store, utcnow

log = logging.getLogger("albumserver")

PAGE_FOLDER = "page"
TRASH_DELAY_S = 30  # how long a replaced output folder is kept for requests already reading it
ASSEMBLY_RETRY_S = 60


def run_album_step(work: str, out: str) -> None:
    """Child-process target: the album step on a one-page album folder."""
    from albumproc.album import AlbumOptions, process_album

    process_album(work, out, AlbumOptions())


class PageRun(Protocol):
    def poll(self) -> tuple[bool, str | None] | None:
        """None while running, then (succeeded, error message)."""

    def kill(self) -> None: ...


def _child(target: Callable[[str, str], None], work: str, out: str, conn) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the worker decides when runs stop
    try:
        target(work, out)
        conn.send(None)
    except BaseException as e:  # reported to the worker, which records it as the page's error
        conn.send(str(e) or type(e).__name__)
    finally:
        conn.close()


class ProcessRun:
    def __init__(self, ctx, target, work: Path, out: Path):
        self._conn, child_conn = ctx.Pipe(duplex=False)
        self.proc = ctx.Process(target=_child, args=(target, str(work), str(out), child_conn), daemon=True)
        self.proc.start()
        child_conn.close()
        self._result: tuple[bool, str | None] | None = None

    def poll(self):
        if self._result is None:
            if self._conn.poll():
                try:
                    msg = self._conn.recv()
                    self._result = (msg is None, msg)
                except EOFError:  # died without a word: killed, or out of memory
                    self._result = (False, f"the page run exited with code {self._wait()}")
            elif not self.proc.is_alive():
                code = self._wait()
                self._result = (False, f"the page run exited with code {code}")
            else:
                return None
            self._wait()
            self._conn.close()
        return self._result

    def _wait(self) -> int | None:
        self.proc.join(30)
        return self.proc.exitcode

    def kill(self) -> None:
        if self.proc.is_alive():
            self.proc.terminate()
            self.proc.join(5)
            if self.proc.is_alive():
                self.proc.kill()
                self.proc.join()
        self._conn.close()


class ProcessRunner:
    """Runs each page in a child process, so a run can be stopped and never slows the API."""

    def __init__(self, target: Callable[[str, str], None] = run_album_step, context: str = "spawn"):
        self.target = target
        self.ctx = multiprocessing.get_context(context)

    def start(self, work: Path, out: Path) -> PageRun:
        return ProcessRun(self.ctx, self.target, work, out)


@dataclass
class _Active:
    claim: Claim
    run: PageRun
    output: str  # relative to the album folder
    started: float


def _link_tree(src: Path, dst: Path) -> None:
    """Hard-link every file under ``src`` into ``dst``; the album step replaces files, never edits them."""
    for path in src.rglob("*"):
        if path.is_file() and not path.name.endswith(".tmp"):
            target = dst / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path, target)
            except FileExistsError:
                pass


class Worker:
    def __init__(self, settings: Settings, store: Store, layout: Layout, runner=None, clock: Callable[[], datetime] = utcnow):
        self.settings = settings
        self.store = store
        self.layout = layout
        self.runner = runner or ProcessRunner()
        self.clock = clock
        self.active: dict[str, _Active] = {}
        self._trash: list[tuple[float, Path]] = []
        self._assembly_failed: dict[str, float] = {}

    # -- one pass ---------------------------------------------------------

    def step(self) -> None:
        self._reap()
        self._stop_stale()
        self.store.promote_idle(self.clock() - timedelta(seconds=self.settings.idle_period_s))
        while len(self.active) < self.settings.workers:
            claim = self.store.claim_ready()
            if claim is None:
                break
            self._start(claim)
        for album_id in self.store.albums_to_assemble():
            if time.monotonic() - self._assembly_failed.get(album_id, -ASSEMBLY_RETRY_S) >= ASSEMBLY_RETRY_S:
                self.assemble(album_id)
        self._empty_trash()

    def run_forever(self, stop, poll_s: float = 0.5, parent_pid: int | None = None) -> None:
        log.info("processing worker started (%d page run(s) at a time)", self.settings.workers)
        while not stop.is_set():
            if parent_pid is not None and os.getppid() != parent_pid:
                log.warning("server process went away; worker stopping")
                break
            try:
                self.step()
            except Exception:
                log.exception("worker pass failed")
            stop.wait(poll_s)
        self.stop()
        log.info("processing worker stopped")

    def stop(self) -> None:
        """Stop every run; their pages wait to be run again."""
        for page_id in list(self.active):
            act = self.active.pop(page_id)
            act.run.kill()
            self._discard(act)
            self.store.abandon_run(act.claim)
        self._empty_trash(force=True)

    # -- page runs --------------------------------------------------------

    def _start(self, claim: Claim) -> None:
        aid, pid = claim.album_id, claim.page_id
        work = self.layout.work(aid, pid)
        remove_tree(work)
        (work / PAGE_FOLDER).mkdir(parents=True)
        for sid in claim.shots:
            try:
                os.link(self.layout.photo(aid, pid, sid), work / PAGE_FOLDER / f"{sid}.jpg")
            except FileNotFoundError:
                pass  # deleted since the claim; the page has changed and will run again
        manifest = {"title": claim.title, "page_size": claim.page_size, "pages": [{"folder": PAGE_FOLDER, "name": f"Page {claim.number}"}]}
        write_atomic(work / "album.json", (json.dumps(manifest, indent=2) + "\n").encode())
        output = self.layout.new_run(aid, pid)
        out = self.layout.album(aid) / output
        out.mkdir(parents=True)
        if claim.output and (self.layout.album(aid) / claim.output).is_dir():
            _link_tree(self.layout.album(aid) / claim.output, out)
        run = self.runner.start(work, out)
        self.active[pid] = _Active(claim, run, output, time.monotonic())
        log.info("page run started: album %s page %d (%s), %d shot(s)", aid, claim.number, pid, len(claim.shots))

    def _reap(self) -> None:
        for pid in list(self.active):
            act = self.active[pid]
            result = act.run.poll()
            if result is None:
                continue
            del self.active[pid]
            ok, error = result
            claim = act.claim
            out = self.layout.album(claim.album_id) / act.output
            secs = time.monotonic() - act.started
            if ok:
                print_path, warnings = None, []
                try:
                    report = json.loads((out / "report.json").read_text())
                    entry = report["pages"][0]
                    if entry["status"] == "failed":
                        error = entry.get("error") or "the album step failed"
                    elif entry.get("print"):
                        print_path = f"{act.output}/{entry['print']}"
                    warnings = entry.get("warnings", [])
                except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
                    error = f"no usable report from the album step: {e}"
                outcome, old = self.store.finish_run(claim, act.output, error, print_path, warnings)
            else:
                outcome, old = self.store.finish_run(claim, None, error)
            if outcome == "adopted":
                if old:
                    self._trash.append((time.monotonic() + TRASH_DELAY_S, self.layout.album(claim.album_id) / old))
            else:
                remove_tree(out)
            remove_tree(self.layout.work(claim.album_id, pid))
            codes = ", ".join(w.get("code", "?") for w in (warnings if ok else []) if isinstance(w, dict))
            what = "failed: " + error if error else "done" + (f" ({codes})" if codes else "")
            log.info("page run ended: album %s page %d (%s) %s in %.1fs [%s]", claim.album_id, claim.number, pid, what, secs, outcome)

    def _stop_stale(self) -> None:
        for pid in list(self.active):
            act = self.active[pid]
            exists, gen = self.store.run_target(pid)
            if exists and not (self.settings.cancel_runs and gen != act.claim.gen):
                continue
            del self.active[pid]
            act.run.kill()
            self._discard(act)
            if exists:
                self.store.abandon_run(act.claim)
                log.info("page run cancelled: album %s page %d (%s) changed during the run", act.claim.album_id, act.claim.number, pid)
            else:
                log.info("page run stopped: album %s page %s was deleted", act.claim.album_id, pid)

    def _discard(self, act: _Active) -> None:
        remove_tree(self.layout.album(act.claim.album_id) / act.output)
        remove_tree(self.layout.work(act.claim.album_id, act.claim.page_id))

    def _empty_trash(self, force: bool = False) -> None:
        now = time.monotonic()
        keep = []
        for due, path in self._trash:
            if force or due <= now:
                remove_tree(path)
            else:
                keep.append((due, path))
        self._trash = keep

    # -- album assembly ---------------------------------------------------

    def assemble(self, album_id: str) -> None:
        """Build the album's PDF and report from every page's latest outputs, in album order."""
        from albumproc.pdf import PdfPage, write_album_pdf

        plan = self.store.begin_assembly(album_id)
        if plan is None:
            return
        album = self.layout.album(album_id)
        started = time.monotonic()
        try:
            pdf_pages, entries, dpis = [], [], set()
            for p in plan["pages"]:
                entry = {"id": p["id"], "number": p["number"], "state": p["state"] if p["has_shots"] else "empty", "report": None}
                page_report = None
                if p["output"]:
                    try:
                        rep = json.loads((album / p["output"] / "report.json").read_text())
                        page_report = rep["pages"][0]
                        entry["report"] = page_report
                    except (OSError, ValueError, KeyError, IndexError):
                        pass
                if p["has_shots"] and p["state"] != "failed" and p["print_path"] and page_report and (album / p["print_path"]).is_file():
                    dpi = int(rep.get("dpi", 300))
                    dpis.add(dpi)
                    pdf_pages.append(PdfPage(album / p["print_path"], dpi, int(page_report.get("bleed_px", 0)), f"Page {p['number']}"))
                    entry["in_pdf"] = True
                entries.append(entry)
            if len(dpis) > 1:  # a page from before a DPI change: leave the odd ones out rather than fail
                common = max(dpis, key=lambda d: sum(pp.dpi == d for pp in pdf_pages))
                pdf_pages = [pp for pp in pdf_pages if pp.dpi == common]
            report = {"version": 1, "title": plan["title"], "pdf": None, "pdf_pages": 0, "pages": entries}
            if pdf_pages:
                report["pdf_pages"] = write_album_pdf(pdf_pages, self.layout.pdf(album_id), plan["title"])
                report["pdf"] = "album.pdf"
            else:
                self.layout.pdf(album_id).unlink(missing_ok=True)
            write_atomic(self.layout.report(album_id), (json.dumps(report, indent=2) + "\n").encode())
            self.store.set_pdf_built(album_id, bool(pdf_pages))
            self._assembly_failed.pop(album_id, None)
            log.info("album assembled: %s, %d page(s) in the PDF in %.1fs", album_id, report["pdf_pages"], time.monotonic() - started)
        except Exception:
            log.exception("album assembly failed: %s", album_id)
            self._assembly_failed[album_id] = time.monotonic()
            self.store.mark_pdf_stale(album_id)


def worker_main(settings: Settings, stop, parent_pid: int) -> None:
    """Entry point of the worker process started by ``albumserver serve``."""
    from .cli import setup_logging

    setup_logging()
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C reaches the server, which stops us
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    layout = Layout(settings.data_dir)
    store = Store(layout.index, max_pages=settings.max_pages, max_shots=settings.max_shots)
    try:
        Worker(settings, store, layout).run_forever(stop, parent_pid=parent_pid)
    finally:
        store.close()

"""Processing, with the album step and the clock replaced by fakes, and the result routes."""

from __future__ import annotations

import io
import json
import os

import pikepdf
import pytest
from PIL import Image

from albumserver.store import Store
from albumserver.worker import Worker
from conftest import make_jpeg, new_id


class FakeRun:
    def __init__(self, work, out):
        self.work, self.out = work, out
        self.result = None
        self.killed = False
        self.manifest = json.loads((work / "album.json").read_text())
        self.photos = sorted(p.name for p in (work / "page").iterdir())
        self.seeded = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())

    def finish(self, status="ok", warnings=(), error=None, colour=(200, 50, 50)):
        """Write what the album step writes for a one-page album, then report success."""
        entry = {"index": 1, "name": self.manifest["pages"][0]["name"], "folder": "page", "status": status}
        if status == "failed":
            entry.update(error=error or "no photos registered", warnings=[])
        else:
            im = Image.new("RGB", (150, 120), colour)
            (self.out / "print").mkdir(parents=True, exist_ok=True)  # as the album step does
            im.save(self.out / "print" / "001-page.jpg", "JPEG", dpi=(150, 150))
            entry.update(print="print/001-page.jpg", bleed_px=0, warnings=[dict(w) for w in warnings])
        (self.out / "report.json").write_text(json.dumps({"dpi": 150, "pages": [entry]}))
        self.result = (True, None)

    def crash(self, message="boom"):
        self.result = (False, message)

    def poll(self):
        return self.result

    def kill(self):
        self.killed = True


class FakeRunner:
    def __init__(self):
        self.runs: list[FakeRun] = []

    def start(self, work, out):
        run = FakeRun(work, out)
        self.runs.append(run)
        return run


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def worker(settings, client, runner, clock):
    w = Worker(settings, client.store, client.layout, runner, clock)
    yield w
    w.stop()


def _page_state(api, a, number):
    return api.get(f"/albums/{a}/status").json()["pages"][number - 1]["state"]


def _album_with_shots(api, pages, shots_per_page=2, page_size="5x4in"):
    a = new_id()
    api.put_album(a, pages[:1], page_size=page_size)
    for i, p in enumerate(pages):
        for k in range(shots_per_page):
            api.put_shot(a, p, new_id(), make_jpeg(10 * i + k))
    return a


# -- when pages run -------------------------------------------------------------


def test_page_runs_after_idle_period_not_before(api, worker, runner, clock):
    p = new_id()
    a = _album_with_shots(api, [p])
    worker.step()
    assert runner.runs == [] and _page_state(api, a, 1) == "changed"
    clock.advance(29.9)
    worker.step()
    assert runner.runs == []
    clock.advance(0.1)
    worker.step()
    assert len(runner.runs) == 1
    assert _page_state(api, a, 1) == "processing"


def test_each_new_shot_restarts_the_idle_period(api, worker, runner, clock):
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(20)
    api.put_shot(a, p, new_id(), make_jpeg(50))
    clock.advance(20)
    worker.step()
    assert runner.runs == []
    clock.advance(10)
    worker.step()
    assert len(runner.runs) == 1


def test_next_page_makes_the_previous_page_ready_at_once(api, worker, runner, clock):
    p1, p2 = new_id(), new_id()
    a = _album_with_shots(api, [p1])
    worker.step()
    assert runner.runs == []
    api.put_album(a, [p1, p2], page_size="5x4in")  # the app's "Next page"
    assert _page_state(api, a, 1) == "ready"
    assert _page_state(api, a, 2) == "empty"
    worker.step()
    assert len(runner.runs) == 1
    assert runner.runs[0].photos and len(runner.runs[0].photos) == 2


def test_metadata_without_a_new_page_does_not_hurry_processing(api, worker, runner):
    p1 = new_id()
    a = _album_with_shots(api, [p1])
    api.put_album(a, [p1], name="Renamed", page_size="5x4in")
    assert _page_state(api, a, 1) == "changed"
    worker.step()
    assert runner.runs == []


def test_oldest_ready_first_within_the_worker_limit(api, worker, runner, clock):
    p1, p2, p3 = new_id(), new_id(), new_id()
    a = new_id()
    api.put_album(a, [p1, p2, p3], page_size="5x4in")
    for p in (p1, p3, p2):  # page 3 is shot before page 2
        api.put_shot(a, p, new_id(), make_jpeg(1))
        clock.advance(1)
    clock.advance(27)
    worker.step()
    names = lambda: [r.manifest["pages"][0]["name"] for r in runner.runs]  # noqa: E731
    assert names() == ["Page 1"]
    clock.advance(1)
    worker.step()  # page 3 becomes ready, but workers = 1
    clock.advance(1)
    worker.step()  # and then page 2
    assert names() == ["Page 1"]
    assert [_page_state(api, a, n) for n in (1, 2, 3)] == ["processing", "ready", "ready"]
    runner.runs[0].finish()
    worker.step()
    assert names() == ["Page 1", "Page 3"]


def test_two_workers_run_two_pages(api, client, runner, clock, settings):
    w = Worker(settings.model_copy(update={"workers": 2}), client.store, client.layout, runner, clock)
    _album_with_shots(api, [new_id(), new_id(), new_id()])
    clock.advance(30)
    w.step()
    assert len(runner.runs) == 2
    w.stop()


def test_work_folder_holds_hard_links_and_the_album_json(api, client, worker, runner, clock):
    p = new_id()
    a = new_id()
    api.put_album(a, [new_id(), p], name="Family 1962", page_size="10x12in")
    shots = [new_id(), new_id()]
    for k, s in enumerate(shots):
        api.put_shot(a, p, s, make_jpeg(k))
    clock.advance(30)
    worker.step()
    run = runner.runs[0]
    assert run.manifest == {"title": "Family 1962", "page_size": "10x12in", "pages": [{"folder": "page", "name": "Page 2"}]}
    assert run.photos == sorted(f"{s}.jpg" for s in shots)
    for s in shots:
        linked = run.work / "page" / f"{s}.jpg"
        assert os.path.samefile(linked, client.layout.photo(a, p, s))
    assert run.work == client.layout.work(a, p)
    run.finish()
    worker.step()
    assert not client.layout.work(a, p).exists()


def test_shot_during_a_run_sends_the_page_back_and_it_runs_again(api, worker, runner, clock):
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(30)
    worker.step()
    api.put_shot(a, p, new_id(), make_jpeg(77))
    worker.step()
    assert not runner.runs[0].killed  # cancellation is off: the run finishes
    runner.runs[0].finish()
    worker.step()
    st = api.get(f"/albums/{a}/status").json()
    assert st["pages"][0]["state"] == "changed"
    assert st["pages"][0]["last_run"] is not None
    assert api.get(f"/albums/{a}/pages/{p}/print").status_code == 200  # its result is kept meanwhile
    clock.advance(30)
    worker.step()
    assert len(runner.runs) == 2
    assert len(runner.runs[1].photos) == 3
    runner.runs[1].finish()
    worker.step()
    assert _page_state(api, a, 1) == "done"


def test_next_page_during_a_run_makes_the_changed_page_ready_when_the_run_ends(api, worker, runner, clock):
    p1, p2 = new_id(), new_id()
    a = _album_with_shots(api, [p1])
    clock.advance(30)
    worker.step()
    api.put_shot(a, p1, new_id(), make_jpeg(77))  # a late shot of page 1, then "Next page"
    api.put_album(a, [p1, p2], page_size="5x4in")
    runner.runs[0].finish()
    worker.step()
    assert len(runner.runs) == 2  # no idle wait
    assert len(runner.runs[1].photos) == 3


def test_page_with_no_shots_is_reported_empty(api, worker, runner, clock):
    p1, p2 = new_id(), new_id()
    a = _album_with_shots(api, [p1])
    api.put_album(a, [p1, p2], page_size="5x4in")
    assert _page_state(api, a, 2) == "empty"
    api.put_shot(a, p2, new_id(), make_jpeg(3))
    assert _page_state(api, a, 2) == "changed"


def test_shot_deleted_during_a_run_also_sends_the_page_back(api, worker, runner, clock):
    p = new_id()
    a = _album_with_shots(api, [p], shots_per_page=3)
    clock.advance(30)
    worker.step()
    s = api.get(f"/albums/{a}/pages/{p}").json()["shots"][0]["id"]
    api.delete(f"/albums/{a}/pages/{p}/shots/{s}")
    runner.runs[0].finish()
    worker.step()
    assert _page_state(api, a, 1) == "changed"


def test_page_size_change_runs_pages_again(api, worker, runner, clock):
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(30)
    worker.step()
    runner.runs[0].finish()
    worker.step()
    assert _page_state(api, a, 1) == "done"
    api.put_album(a, [p], page_size="10x12in")
    assert _page_state(api, a, 1) == "changed"
    clock.advance(30)
    worker.step()
    assert len(runner.runs) == 2
    assert runner.runs[1].manifest["page_size"] == "10x12in"
    # seeded with the previous run's outputs, so the album step can reuse them
    assert runner.runs[1].seeded == ["print/001-page.jpg", "report.json"]


def test_no_processing_while_waiting_for_a_page_size(api, worker, runner, clock):
    a, p = new_id(), new_id()
    api.put_shot(a, p, new_id(), make_jpeg(1))  # metadata has not arrived
    clock.advance(300)
    worker.step()
    assert runner.runs == []
    st = api.get(f"/albums/{a}/status").json()
    assert st["waiting_for_page_size"] is True
    assert st["pages"][0]["state"] == "changed"
    api.put_album(a, [p], page_size="letter")
    assert api.get(f"/albums/{a}/status").json()["waiting_for_page_size"] is False
    worker.step()
    assert runner.runs == []  # a new page size is a change like any other
    clock.advance(30)
    worker.step()
    assert len(runner.runs) == 1


def test_failed_page_is_reported_and_left_out_of_the_pdf(api, worker, runner, clock):
    p1, p2 = new_id(), new_id()
    a = _album_with_shots(api, [p1, p2])
    api.put_album(a, [p1, p2], page_size="5x4in")
    clock.advance(30)
    worker.step()
    runner.runs[0].finish(status="failed", error="no photos registered")
    worker.step()
    runner.runs[1].finish(warnings=[{"code": "photos_dropped", "message": "1 of 3 photos did not match"}])
    worker.step()
    st = api.get(f"/albums/{a}/status").json()
    assert st["pages"][0]["state"] == "failed"
    assert st["pages"][0]["error"] == "no photos registered"
    assert st["pages"][1] == {
        "id": p2,
        "number": 2,
        "state": "done",
        "last_run": st["pages"][1]["last_run"],
        "warnings": [{"code": "photos_dropped", "message": "1 of 3 photos did not match"}],
    }
    assert st["pdf"] == "current"
    pdf = pikepdf.open(io.BytesIO(api.get(f"/albums/{a}/pdf").content))
    assert len(pdf.pages) == 1
    assert api.get(f"/albums/{a}/pages/{p1}/print").status_code == 404
    assert api.get(f"/albums/{a}/pages/{p2}/print").status_code == 200


def test_crashed_run_is_failed_and_retried_on_the_next_change(api, worker, runner, clock):
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(30)
    worker.step()
    runner.runs[0].crash("the page run exited with code -9")
    worker.step()
    st = api.get(f"/albums/{a}/status").json()["pages"][0]
    assert st["state"] == "failed" and "-9" in st["error"]
    clock.advance(300)
    worker.step()
    assert len(runner.runs) == 1  # not retried on its own
    api.put_shot(a, p, new_id(), make_jpeg(5))
    clock.advance(30)
    worker.step()
    assert len(runner.runs) == 2


def test_crashed_run_drops_the_previous_result_and_rebuilds_the_pdf(api, worker, runner, clock):
    p1, p2 = new_id(), new_id()
    a = _album_with_shots(api, [p1, p2])
    api.put_album(a, [p1, p2], page_size="5x4in")
    clock.advance(30)
    worker.step()
    runner.runs[0].finish()
    worker.step()
    runner.runs[1].finish()
    worker.step()
    assert len(pikepdf.open(io.BytesIO(api.get(f"/albums/{a}/pdf").content)).pages) == 2

    api.put_shot(a, p1, new_id(), make_jpeg(5))
    clock.advance(30)
    worker.step()
    runner.runs[2].crash("the page run exited with code -9")
    worker.step()
    st = api.get(f"/albums/{a}/status").json()
    assert st["pages"][0]["state"] == "failed" and "-9" in st["pages"][0]["error"]
    assert st["pdf"] == "current"
    assert len(pikepdf.open(io.BytesIO(api.get(f"/albums/{a}/pdf").content)).pages) == 1
    assert api.get(f"/albums/{a}/pages/{p1}/print").status_code == 404
    assert api.get(f"/albums/{a}/pages/{p2}/print").status_code == 200


# -- the album PDF ----------------------------------------------------------------


def test_pdf_built_only_once_no_page_is_pending(api, worker, runner, clock):
    p1, p2 = new_id(), new_id()
    a = _album_with_shots(api, [p1, p2])
    api.put_album(a, [p1, p2], page_size="5x4in")
    assert api.get(f"/albums/{a}/pdf").status_code == 404
    assert api.get(f"/albums/{a}/status").json()["pdf"] == "none"
    clock.advance(30)
    worker.step()
    runner.runs[0].finish()
    worker.step()  # page 2 starts; no PDF while it is pending
    assert api.get(f"/albums/{a}/pdf").status_code == 404
    runner.runs[1].finish()
    worker.step()
    st = api.get(f"/albums/{a}/status").json()
    assert st["pdf"] == "current"
    assert st["pdf_built"] is not None
    r = api.get(f"/albums/{a}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    pdf = pikepdf.open(io.BytesIO(r.content))
    assert len(pdf.pages) == 2
    assert str(pdf.docinfo["/Title"]) == "Rawlins family 1962-1968"
    report = json.loads(api.c.layout.report(a).read_text())
    assert [e["id"] for e in report["pages"]] == [p1, p2]
    assert report["pdf_pages"] == 2


def test_pdf_rebuilt_after_reorder_and_rename_without_page_runs(api, worker, runner, clock):
    p1, p2 = new_id(), new_id()
    a = _album_with_shots(api, [p1, p2])
    api.put_album(a, [p1, p2], page_size="5x4in")
    clock.advance(30)
    worker.step()
    runner.runs[0].finish(colour=(255, 0, 0))
    worker.step()
    runner.runs[1].finish(colour=(0, 0, 255))
    worker.step()
    first_built = api.get(f"/albums/{a}/status").json()["pdf_built"]
    clock.advance(5)
    api.put_album(a, [p2, p1], name="New title", page_size="5x4in")
    assert api.get(f"/albums/{a}/status").json()["pdf"] == "out_of_date"
    worker.step()
    assert len(runner.runs) == 2  # no page ran again
    st = api.get(f"/albums/{a}/status").json()
    assert st["pdf"] == "current" and st["pdf_built"] > first_built
    pdf = pikepdf.open(io.BytesIO(api.get(f"/albums/{a}/pdf").content))
    assert str(pdf.docinfo["/Title"]) == "New title"
    labels = [str(x) for x in pdf.Root.PageLabels.Nums if isinstance(x, pikepdf.Dictionary) for x in [x.P]]
    assert labels == ["Page 1", "Page 2"]
    first_img = next(iter(pdf.pages[0].images.values()))
    assert pikepdf.PdfImage(first_img).as_pil_image().getpixel((5, 5))[2] > 200  # page 2's blue image now first


def test_page_deletion_makes_the_pdf_out_of_date_and_drops_it(api, worker, runner, clock, client):
    p1, p2 = new_id(), new_id()
    a = _album_with_shots(api, [p1, p2])
    api.put_album(a, [p1, p2], page_size="5x4in")
    clock.advance(30)
    for _ in range(2):
        worker.step()
        runner.runs[-1].finish()
    worker.step()
    api.delete(f"/albums/{a}/pages/{p1}")
    assert api.get(f"/albums/{a}/status").json()["pdf"] == "out_of_date"
    assert not client.layout.outputs(a, p1).exists()
    worker.step()
    assert len(pikepdf.open(io.BytesIO(api.get(f"/albums/{a}/pdf").content)).pages) == 1


def test_page_emptied_of_shots_loses_its_outputs(api, worker, runner, clock, client):
    p1, p2 = new_id(), new_id()
    a = _album_with_shots(api, [p1, p2], shots_per_page=1)
    api.put_album(a, [p1, p2], page_size="5x4in")
    clock.advance(30)
    for _ in range(2):
        worker.step()
        runner.runs[-1].finish()
    worker.step()
    s = api.get(f"/albums/{a}/pages/{p2}").json()["shots"][0]["id"]
    api.delete(f"/albums/{a}/pages/{p2}/shots/{s}")
    assert api.get(f"/albums/{a}/pages/{p2}/print").status_code == 404
    assert not client.layout.outputs(a, p2).exists()
    clock.advance(30)
    worker.step()
    assert len(runner.runs) == 2  # nothing to run for an empty page
    assert api.get(f"/albums/{a}/status").json()["pdf"] == "current"
    assert len(pikepdf.open(io.BytesIO(api.get(f"/albums/{a}/pdf").content)).pages) == 1


def test_no_printable_pages_means_no_pdf(api, worker, runner, clock):
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(30)
    worker.step()
    runner.runs[0].finish(status="failed")
    worker.step()
    assert api.get(f"/albums/{a}/status").json()["pdf"] == "none"
    assert api.get(f"/albums/{a}/pdf").status_code == 404


# -- deletion and cancellation ------------------------------------------------------


def test_album_deletion_stops_its_run(api, worker, runner, clock, client):
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(30)
    worker.step()
    api.delete(f"/albums/{a}")
    worker.step()
    assert runner.runs[0].killed
    assert worker.active == {}
    assert not client.layout.album(a).exists()


def test_page_deletion_stops_its_run(api, worker, runner, clock, client):
    p, other = new_id(), new_id()
    a = _album_with_shots(api, [p, other])
    clock.advance(30)
    worker.step()
    out = runner.runs[0].out
    api.delete(f"/albums/{a}/pages/{p}")
    worker.step()
    assert runner.runs[0].killed
    assert not out.exists()
    assert not client.layout.work(a, p).exists()
    # the worker carries on with the other page
    assert len(runner.runs) == 2


def test_run_ending_after_its_page_was_deleted_is_discarded(api, worker, runner, clock, client):
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(30)
    worker.step()
    run = runner.runs[0]
    api.delete(f"/albums/{a}/pages/{p}")
    run.finish()  # finishes before the worker notices the deletion
    worker.step()
    assert not run.out.exists()


def test_cancel_runs_stops_a_run_and_discards_its_output(api, client, runner, clock, settings):
    w = Worker(settings.model_copy(update={"cancel_runs": True}), client.store, client.layout, runner, clock)
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(30)
    w.step()
    run = runner.runs[0]
    api.put_shot(a, p, new_id(), make_jpeg(66))
    w.step()
    assert run.killed
    assert not run.out.exists()
    assert _page_state(api, a, 1) == "changed"
    assert api.get(f"/albums/{a}/pages/{p}/print").status_code == 404
    clock.advance(30)
    w.step()
    assert len(runner.runs) == 2 and len(runner.runs[1].photos) == 3
    w.stop()


def test_stopping_the_worker_leaves_pages_to_run_again(api, worker, runner, clock):
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(30)
    worker.step()
    worker.stop()
    assert runner.runs[0].killed
    assert _page_state(api, a, 1) == "changed"


def test_startup_treats_interrupted_runs_as_not_run(api, client, runner, clock, settings):
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(30)
    Worker(settings, client.store, client.layout, runner, clock).step()
    assert _page_state(api, a, 1) == "processing"
    # the server dies; on the next start:
    fresh = Store(client.store.path, clock)
    assert fresh.recover() == 1
    assert _page_state(api, a, 1) == "changed"
    w = Worker(settings, fresh, client.layout, runner, clock)
    w.step()
    assert len(runner.runs) == 2
    w.stop()
    fresh.close()


# -- results while processing ---------------------------------------------------------


def test_previous_print_and_pdf_served_during_a_run(api, worker, runner, clock):
    p = new_id()
    a = _album_with_shots(api, [p])
    clock.advance(30)
    worker.step()
    assert api.get(f"/albums/{a}/pages/{p}/print").status_code == 404
    runner.runs[0].finish(colour=(255, 0, 0))
    worker.step()
    old_print = api.get(f"/albums/{a}/pages/{p}/print")
    old_pdf = api.get(f"/albums/{a}/pdf").content
    assert old_print.headers["content-type"] == "image/jpeg"

    api.put_shot(a, p, new_id(), make_jpeg(3))
    clock.advance(30)
    worker.step()
    assert _page_state(api, a, 1) == "processing"
    st = api.get(f"/albums/{a}/status").json()
    assert st["pdf"] == "out_of_date"
    assert api.get(f"/albums/{a}/pages/{p}/print").content == old_print.content
    assert api.get(f"/albums/{a}/pdf").content == old_pdf

    runner.runs[1].finish(colour=(0, 0, 255))
    worker.step()
    new_print = api.get(f"/albums/{a}/pages/{p}/print").content
    assert new_print != old_print.content
    assert Image.open(io.BytesIO(new_print)).getpixel((5, 5))[2] > 200
    assert api.get(f"/albums/{a}/status").json()["pdf"] == "current"


def test_status_404_for_unknown_album(api):
    assert api.get(f"/albums/{new_id()}/status").status_code == 404
    assert api.get(f"/albums/{new_id()}/pdf").status_code == 404
    assert api.get(f"/albums/{new_id()}/pages/{new_id()}/print").status_code == 404

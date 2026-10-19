"""The GTK 4 window: the only module that imports gi.

Everything slow (SANE, the network, encoding) runs on worker threads and
comes back to the main loop through ``GLib.idle_add``; the session, outbox
and settings are only touched from the main loop, except the outbox's own
sender thread.
"""

from __future__ import annotations

import dataclasses
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .client import Client, HttpError, Unreachable  # noqa: E402
from .outbox import AUTH, BLOCKED, Outbox, Sender  # noqa: E402
from .sane import Device, Options, ScanCancelled, ScanError, Scanner, choose_device, list_devices, read_options  # noqa: E402
from .session import NAMED_SIZES, LimitError, Session, check_page_size, default_name  # noqa: E402
from .settings import Settings, data_dir, load_token, save_token  # noqa: E402
from .shot import MAX_PIXELS, PLACEMENTS, ScanInfo, TooLarge, encode_file  # noqa: E402

APP_ID = "org.bit63.albumscan"
PENDING_STATES = ("changed", "ready", "processing")
HINT = "A page larger than the glass: scan it in overlapping parts. Each part is a scan of the same page."


def _in_thread(work, done, error):
    """Run ``work()`` on a thread, then ``done(result)`` or ``error(exc)`` on the main loop."""

    def run():
        try:
            result = work()
        except Exception as e:  # noqa: BLE001 - every failure is shown to the user
            GLib.idle_add(error, e)
        else:
            GLib.idle_add(done, result)

    threading.Thread(target=run, daemon=True).start()


def _texture(data: bytes) -> Gdk.Texture | None:
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    except GLib.Error:
        return None


def _message(e: Exception) -> str:
    if isinstance(e, HttpError):
        return e.detail or f"the server answered {e.status}"
    if isinstance(e, Unreachable):
        return f"cannot reach the server: {e}"
    return str(e) or type(e).__name__


class ScanWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, settings: Settings, outbox: Outbox):
        super().__init__(application=app, title="Album scanner", default_width=1000, default_height=780)
        self.settings = settings
        self.outbox = outbox
        self.client: Client | None = None
        self.session: Session | None = None
        self.devices: list[Device] = []
        self.device: Device | None = None
        self.options: Options | None = None
        self.scanning = False
        self.cancel = threading.Event()
        self.status_timer = 0
        self.thumbs: dict[str, Gdk.Texture] = {}
        self.sender = Sender(outbox, None, on_change=lambda s: GLib.idle_add(self._upload_changed))
        self.sender.start()
        self._build()
        self.connect("close-request", self._on_close)
        self._upload_changed()

    # -- layout ----------------------------------------------------------------------

    def _build(self) -> None:
        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        albums = Gtk.Button(label="Albums")
        albums.connect("clicked", lambda *_: self.show_albums())
        header.pack_start(albums)
        menu = Gio.Menu()
        menu.append("Rename album…", "win.rename")
        menu.append("Page size…", "win.page-size")
        menu.append("Server…", "win.server")
        menu.append(f"About albumscan {__version__}", "win.about")
        for name, cb in (("rename", self.ask_rename), ("page-size", self.ask_page_size), ("server", self.ask_server), ("about", lambda: None)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_, cb=cb: cb())
            self.add_action(a)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        self.set_child(root)

        bar = Gtk.Box(spacing=8)
        root.append(bar)
        bar.append(Gtk.Label(label="Scanner"))
        self.device_list = Gtk.StringList()
        self.device_drop = Gtk.DropDown(model=self.device_list, hexpand=True)
        self.device_drop.connect("notify::selected", self._on_device_selected)
        bar.append(self.device_drop)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Look for scanners again")
        refresh.connect("clicked", lambda *_: self.find_scanners())
        bar.append(refresh)
        bar.append(Gtk.Label(label="Resolution"))
        self.res_list = Gtk.StringList()
        self.res_drop = Gtk.DropDown(model=self.res_list)
        self.res_drop.connect("notify::selected", self._on_resolution_selected)
        bar.append(self.res_drop)
        bar.append(Gtk.Label(label="Page turned"))
        self.place_drop = Gtk.DropDown.new_from_strings([f"{p}° clockwise" if p else "upright" for p in PLACEMENTS])
        self.place_drop.set_tooltip_text("How the page lies on the glass; scans are turned back by this much")
        self.place_drop.connect("notify::selected", self._on_placement_selected)
        bar.append(self.place_drop)

        self.picture = Gtk.Picture(vexpand=True, content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        frame = Gtk.Frame(child=self.picture, vexpand=True)
        root.append(frame)

        self.progress = Gtk.ProgressBar(show_text=True, visible=False)
        root.append(self.progress)
        self.notice = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.notice.add_css_class("error")
        root.append(self.notice)

        strip_scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=110)
        self.strip = Gtk.Box(spacing=6)
        strip_scroll.set_child(self.strip)
        root.append(strip_scroll)

        row = Gtk.Box(spacing=8)
        root.append(row)
        self.page_label = Gtk.Label(xalign=0)
        self.page_label.add_css_class("title-3")
        row.append(self.page_label)
        self.page_status = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        row.append(self.page_status)
        self.print_prev = Gtk.Button(label="Previous page's print", visible=False)
        self.print_prev.connect("clicked", lambda *_: self.open_print(self.session.previous_page_id))
        row.append(self.print_prev)
        self.print_cur = Gtk.Button(label="This page's print", visible=False)
        self.print_cur.connect("clicked", lambda *_: self.open_print(self.session.page_id))
        row.append(self.print_cur)

        actions = Gtk.Box(spacing=8)
        root.append(actions)
        hint = Gtk.Label(label=HINT, xalign=0, wrap=True, hexpand=True)
        hint.add_css_class("dim-label")
        actions.append(hint)
        self.cancel_button = Gtk.Button(label="Cancel scan", visible=False)
        self.cancel_button.connect("clicked", lambda *_: self.cancel.set())
        actions.append(self.cancel_button)
        self.next_button = Gtk.Button(label="Next page (Enter)")
        self.next_button.connect("clicked", lambda *_: self.next_page())
        actions.append(self.next_button)
        self.scan_button = Gtk.Button(label="Scan (Space)")
        self.scan_button.add_css_class("suggested-action")
        self.scan_button.connect("clicked", lambda *_: self.scan())
        actions.append(self.scan_button)

        uploads = Gtk.Box(spacing=8)
        root.append(uploads)
        self.upload_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        uploads.append(self.upload_label)
        self.retry_button = Gtk.Button(label="Try again", visible=False)
        self.retry_button.connect("clicked", lambda *_: self._retry_upload())
        uploads.append(self.retry_button)
        self.discard_button = Gtk.Button(label="Discard this upload", visible=False)
        self.discard_button.add_css_class("destructive-action")
        self.discard_button.connect("clicked", lambda *_: self._discard_upload())
        uploads.append(self.discard_button)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self._refresh_page()

    def _on_key(self, _ctrl, keyval, _code, state) -> bool:
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if isinstance(self.get_focus(), (Gtk.Text, Gtk.Entry)):
            return False
        if keyval == Gdk.KEY_space:
            self.scan()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.next_page()
            return True
        if keyval == Gdk.KEY_Escape and self.scanning:
            self.cancel.set()
            return True
        return False

    def say(self, text: str) -> None:
        self.notice.set_label(text)

    # -- start -------------------------------------------------------------------------

    def start(self) -> None:
        self.present()
        self.find_scanners()
        token = load_token()
        if not self.settings.server_url or not token:
            self.ask_server()
            return
        self._connect(Client(self.settings.server_url, token))

    def _connect(self, client: Client) -> None:
        self.client = client
        self.sender.unblock(client)
        if self.settings.last_album:
            self.open_album(self.settings.last_album, fallback=True)
        else:
            self.show_albums()

    # -- scanner -----------------------------------------------------------------------

    def find_scanners(self) -> None:
        self.say("Looking for scanners…")
        self.scan_button.set_sensitive(False)
        _in_thread(list_devices, self._got_devices, lambda e: self._got_devices([], e))

    def _got_devices(self, devices: list[Device], error: Exception | None = None) -> None:
        self.devices = devices
        self.device_list.splice(0, self.device_list.get_n_items(), [d.label for d in devices])
        if not devices:
            self.device = self.options = None
            why = f" ({error})" if error else ""
            self.say(f"No scanner found{why}. Check its cable and power, run scanimage -L in a terminal, then press ⟳.")
            return
        self.say("")
        chosen = choose_device(devices, self.settings.device)
        self.device_drop.set_selected(devices.index(chosen))
        self._on_device_selected()

    def _on_device_selected(self, *_):
        i = self.device_drop.get_selected()
        if not 0 <= i < len(self.devices):
            return
        dev = self.devices[i]
        if dev == self.device and self.options is not None:
            return
        self.device, self.options = dev, None
        self.scan_button.set_sensitive(False)
        _in_thread(lambda: read_options(dev.name), lambda o: self._got_options(dev, o), lambda e: self.say(f"Cannot open {dev.label}: {_message(e)}"))

    def _got_options(self, dev: Device, opt: Options) -> None:
        if dev != self.device:
            return
        if not opt.resolutions:
            self.say(f"{dev.label} offers no resolution of 300 dpi or more.")
            return
        if not opt.colour:
            self.say(f"{dev.label} cannot scan in colour.")
            return
        # Only resolutions whose scan the server can process; the highest of them by default.
        opt = dataclasses.replace(opt, resolutions=opt.usable(MAX_PIXELS))
        if not opt.resolutions:
            self.say(f"{dev.label} offers no resolution from 300 dpi whose scan the server can process.")
            return
        res = self.settings.resolution if self.settings.resolution in opt.resolutions else opt.default_resolution()
        self.options = None  # filling the list selects its first entry; that is not the user's choice
        self.res_list.splice(0, self.res_list.get_n_items(), [f"{r} dpi" for r in opt.resolutions])
        self.options = opt
        self.res_drop.set_selected(opt.resolutions.index(res))
        self.settings.device = dev.name
        self.settings.save()
        self._refresh_page()

    def _on_resolution_selected(self, *_):
        if self.options and 0 <= self.res_drop.get_selected() < len(self.options.resolutions):
            self.settings.resolution = self.options.resolutions[self.res_drop.get_selected()]
            self.settings.save()

    def _on_placement_selected(self, *_):
        if self.session is not None:
            self.settings.placements[self.session.album_id] = PLACEMENTS[self.place_drop.get_selected()]
            self.settings.save()

    # -- albums ----------------------------------------------------------------------

    def show_albums(self) -> None:
        if self.client is None:
            self.ask_server()
            return
        AlbumChooser(self).present()

    def open_album(self, album_id: str, fallback: bool = False) -> None:
        self.say("Opening the album…")

        def failed(e):
            self.say(f"Cannot open the album: {_message(e)}")
            if fallback:
                self.show_albums()

        _in_thread(lambda: Session.open(self.outbox, self.client, album_id), self._set_session, failed)

    def new_album(self, name: str, page_size: str) -> None:
        self._set_session(Session.new(self.outbox, name, page_size))
        self.sender.wake()

    def _set_session(self, session: Session) -> None:
        self.session = session
        self.thumbs.clear()
        self.settings.last_album = session.album_id
        self.settings.save()
        self.place_drop.set_selected(PLACEMENTS.index(self.settings.placement(session.album_id)))
        self.say("" if session.page_size else "This album has no page size yet: set one in the menu before scanning.")
        self._refresh_page()
        self._poll_status()

    def ask_rename(self) -> None:
        if self.session:
            ask_text(self, "Rename album", "Name", self.session.name, self._do_rename)

    def _do_rename(self, name: str) -> str | None:
        try:
            self.session.rename(name)
        except ValueError as e:
            return str(e)
        self.sender.wake()
        self._refresh_page()
        return None

    def ask_page_size(self) -> None:
        if self.session:
            ask_text(self, "Page size", "letter, a4, a3, or e.g. 10x12in", self.session.page_size or "letter", self._do_page_size)

    def _do_page_size(self, size: str) -> str | None:
        try:
            self.session.set_page_size(size)
        except ValueError as e:
            return str(e)
        self.sender.wake()
        self.say("")
        self._refresh_page()
        return None

    def ask_server(self) -> None:
        ServerDialog(self).present()

    # -- scanning ----------------------------------------------------------------------

    def scan(self) -> None:
        if self.scanning:
            return
        s = self.session
        if s is None:
            self.say("Open or create an album first.")
            return
        if not s.page_size:
            self.say("Set the album's page size in the menu before scanning.")
            return
        if self.device is None or self.options is None:
            self.say("No scanner is ready.")
            return
        try:
            s.check_can_scan()
        except LimitError as e:
            self.say(str(e))
            return
        dev, opt = self.device, self.options
        res = opt.resolutions[self.res_drop.get_selected()]
        placement = PLACEMENTS[self.place_drop.get_selected()]
        tmp = data_dir() / "scan.pnm"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        self.cancel.clear()
        self._scanning(True)
        self.say("")

        def progress(p: float) -> None:
            GLib.idle_add(self._progress, p)

        def work() -> bytes:
            try:
                Scanner(dev, opt).scan(res, tmp, progress, self.cancel)
                GLib.idle_add(self._progress, None)
                return encode_file(tmp, placement, ScanInfo(dev.vendor, dev.model, res, datetime.now(timezone.utc)))
            finally:
                tmp.unlink(missing_ok=True)

        _in_thread(work, self._scanned, self._scan_failed)

    def _progress(self, p: float | None) -> None:
        if p is None:
            self.progress.set_text("Saving…")
            self.progress.pulse()
        else:
            self.progress.set_fraction(p / 100)
            self.progress.set_text(f"Scanning… {p:.0f}%")

    def _scanning(self, on: bool) -> None:
        self.scanning = on
        self.progress.set_visible(on)
        self.progress.set_fraction(0)
        self.progress.set_text("Starting the scanner…")
        self.cancel_button.set_visible(on)
        self.scan_button.set_sensitive(not on)
        self.next_button.set_sensitive(not on)

    def _scanned(self, data: bytes) -> None:
        self._scanning(False)
        try:
            self.session.add_scan(data)
        except (LimitError, OSError) as e:
            self.say(f"The scan was not kept: {e}")
            return
        self.sender.wake()
        self._refresh_page()
        self._poll_status()

    def _scan_failed(self, e: Exception) -> None:
        self._scanning(False)
        if isinstance(e, ScanCancelled):
            self.say("Scan cancelled.")
        elif isinstance(e, (ScanError, TooLarge)):
            self.say(f"The scan failed: {e}")
        else:
            self.say(f"The scan failed: {_message(e)}")

    def next_page(self) -> None:
        if self.scanning or self.session is None:
            return
        try:
            moved = self.session.next_page()
        except LimitError as e:
            self.say(str(e))
            return
        if not moved:
            self.say("This page has no scans yet: scan it before moving on.")
            return
        self.say("")
        self.sender.wake()
        self._refresh_page()
        self._poll_status()

    def confirm_delete(self, shot_id: str) -> None:
        n = next(i for i, s in enumerate(self.session.shots, 1) if s.id == shot_id)
        dlg = Gtk.AlertDialog(message=f"Delete scan {n} of page {self.session.page_number}?", detail="This cannot be undone.", buttons=["Cancel", "Delete"], cancel_button=0, default_button=0)

        def answered(d, result):
            try:
                if d.choose_finish(result) == 1:
                    self.session.delete_shot(shot_id)
                    self.sender.wake()
                    self._refresh_page()
            except GLib.Error:
                pass

        dlg.choose(self, None, answered)

    # -- showing the page --------------------------------------------------------------

    def _refresh_page(self) -> None:
        s = self.session
        ready = s is not None and self.options is not None and not self.scanning
        self.scan_button.set_sensitive(bool(ready))
        self.next_button.set_sensitive(s is not None and not self.scanning)
        if s is None:
            self.set_title("Album scanner")
            self.page_label.set_label("")
            self.picture.set_paintable(None)
            return
        self.set_title(f"{s.name} — {s.page_size or 'no page size'}")
        self.page_label.set_label(f"Page {s.page_number}")
        child = self.strip.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.strip.remove(child)
            child = nxt
        for shot in s.shots:
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, width_request=100, height_request=100, can_shrink=True)
            self._show(pic, shot)
            b = Gtk.Button(child=pic, tooltip_text="Delete this scan…")
            b.connect("clicked", lambda _b, sid=shot.id: self.confirm_delete(sid))
            self.strip.append(b)
        if s.shots:
            self._show(self.picture, s.shots[-1])
        else:
            self.picture.set_paintable(None)
        self._upload_changed()

    def _show(self, pic: Gtk.Picture, shot) -> None:
        if shot.path is not None and shot.path.exists():
            pic.set_filename(str(shot.path))
        elif shot.id in self.thumbs:
            pic.set_paintable(self.thumbs[shot.id])
        elif self.client is not None:
            album, page = self.session.album_id, self.session.page_id

            def got(data):
                tex = _texture(data)
                if tex is not None:
                    self.thumbs[shot.id] = tex
                    pic.set_paintable(tex)

            _in_thread(lambda: self.client.thumb(album, page, shot.id), got, lambda e: None)

    # -- processing feedback -------------------------------------------------------------

    def _poll_status(self) -> None:
        if self.status_timer:
            GLib.source_remove(self.status_timer)
            self.status_timer = 0
        self._fetch_status()

    def _fetch_status(self) -> bool:
        self.status_timer = 0
        s, client = self.session, self.client
        if s is None or client is None:
            return False
        _in_thread(lambda: client.status(s.album_id), lambda st: self._got_status(s, st), lambda e: self._got_status(s, None))
        return False

    def _got_status(self, s: Session, st: dict | None) -> None:
        if s is not self.session:
            return
        pages = {p["id"]: p for p in (st or {}).get("pages", [])}
        lines, pending = [], False
        for label, pid, button in (("Previous page", s.previous_page_id, self.print_prev), ("This page", s.page_id, self.print_cur)):
            p = pages.get(pid) if pid else None
            button.set_visible(bool(p and p.get("state") == "done"))
            if p is None:
                continue
            pending |= p["state"] in PENDING_STATES
            text = f"{label}: {p['state']}"
            if p.get("error"):
                text += f" ({p['error']})"
            for w in p.get("warnings", []):
                text += f"\n  • {w.get('message', w.get('code'))}"
            lines.append(text)
        self.page_status.set_label("\n".join(lines))
        if pending or self.outbox.pending_shots():
            self.status_timer = GLib.timeout_add_seconds(3, self._fetch_status)

    def open_print(self, page_id: str | None) -> None:
        if not page_id or self.client is None:
            return
        s, client = self.session, self.client

        def fetch() -> Path:
            data, ctype = client.print_image(s.album_id, page_id)
            suffix = ".png" if "png" in ctype else ".tif" if "tif" in ctype else ".jpg"
            f = tempfile.NamedTemporaryFile(prefix="albumscan-print-", suffix=suffix, delete=False)
            with f:
                f.write(data)
            return Path(f.name)

        def show(path: Path) -> None:
            Gtk.FileLauncher.new(Gio.File.new_for_path(str(path))).launch(self, None, None)

        _in_thread(fetch, show, lambda e: self.say(f"Cannot fetch the print image: {_message(e)}"))

    # -- uploads -----------------------------------------------------------------------

    def _upload_changed(self) -> bool:
        n = self.outbox.pending_shots()
        st = self.sender.state
        blocked = st in (AUTH, BLOCKED)
        if st == AUTH:
            text = "The server refused the token. Enter a new one under Server… in the menu; nothing is lost."
        elif st == BLOCKED:
            text = f"Uploads stopped: {self.sender.message}."
        elif self.client is None:
            text = f"{n} scan{'s' if n != 1 else ''} waiting for a server." if n else "Not connected to a server."
        elif st == "waiting":
            text = f"{n} scan{'s' if n != 1 else ''} waiting to upload; {self.sender.message}."
        elif n:
            text = f"Uploading: {n} scan{'s' if n != 1 else ''} to go."
        else:
            text = "All scans are on the server."
        self.upload_label.set_label(text)
        self.retry_button.set_visible(blocked or st == "waiting")
        self.discard_button.set_visible(st == BLOCKED)
        return False

    def _retry_upload(self) -> None:
        if self.sender.state == AUTH:
            self.ask_server()
        else:
            self.sender.unblock()

    def _discard_upload(self) -> None:
        op = self.sender.blocked_op
        if op is None:
            return
        dlg = Gtk.AlertDialog(message=f"Discard the {op.describe()}?", detail=self.sender.message, buttons=["Keep it", "Discard"], cancel_button=0, default_button=0)

        def answered(d, result):
            try:
                if d.choose_finish(result) == 1:
                    self.sender.discard()
            except GLib.Error:
                pass

        dlg.choose(self, None, answered)

    def _on_close(self, *_):
        n = self.outbox.pending_shots()
        if not n or getattr(self, "_closing", False):
            self.sender.stop()
            return False
        dlg = Gtk.AlertDialog(
            message=f"{n} scan{'s are' if n != 1 else ' is'} not on the server yet",
            detail="They are kept and will upload the next time the scanner app runs.",
            buttons=["Keep the app open", "Quit"],
            cancel_button=0,
            default_button=0,
        )

        def answered(d, result):
            try:
                if d.choose_finish(result) == 1:
                    self._closing = True
                    self.close()
            except GLib.Error:
                pass

        dlg.choose(self, None, answered)
        return True


def ask_text(parent: Gtk.Window, title: str, label: str, value: str, apply) -> None:
    """A small dialog with one entry; ``apply(text)`` returns an error message to keep it open, or None."""
    win = Gtk.Window(title=title, transient_for=parent, modal=True, default_width=360)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
    win.set_child(box)
    box.append(Gtk.Label(label=label, xalign=0))
    entry = Gtk.Entry(text=value, activates_default=True)
    box.append(entry)
    err = Gtk.Label(xalign=0, wrap=True)
    err.add_css_class("error")
    box.append(err)
    ok = Gtk.Button(label="OK")
    ok.add_css_class("suggested-action")
    box.append(ok)
    win.set_default_widget(ok)

    def done(*_):
        problem = apply(entry.get_text())
        if problem:
            err.set_label(problem)
        else:
            win.close()

    ok.connect("clicked", done)
    win.present()


class ServerDialog(Gtk.Window):
    """The server's address and this app's token, tested with a ping before they are saved."""

    def __init__(self, main: ScanWindow):
        super().__init__(title="Album server", transient_for=main, modal=True, default_width=440)
        self.main = main
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_child(box)
        box.append(Gtk.Label(label="Server address, e.g. http://127.0.0.1:8080", xalign=0))
        self.url = Gtk.Entry(text=main.settings.server_url)
        box.append(self.url)
        box.append(Gtk.Label(label="Token, from: albumserver token create scanner", xalign=0))
        self.token = Gtk.PasswordEntry(show_peek_icon=True)
        if load_token():
            self.token.set_property("placeholder-text", "(saved; leave empty to keep it)")
        box.append(self.token)
        self.err = Gtk.Label(xalign=0, wrap=True)
        self.err.add_css_class("error")
        box.append(self.err)
        self.ok = Gtk.Button(label="Connect")
        self.ok.add_css_class("suggested-action")
        self.ok.connect("clicked", self._connect)
        box.append(self.ok)

    def _connect(self, *_):
        url = self.url.get_text().strip()
        token = self.token.get_text().strip() or load_token()
        if not url or not token:
            self.err.set_label("Both the address and a token are needed.")
            return
        client = Client(url, token, timeout=15)
        self.ok.set_sensitive(False)
        self.err.set_label("Connecting…")

        def ok(_):
            save_token(token)
            self.main.settings.server_url = url
            self.main.settings.save()
            self.main._connect(Client(url, token))
            self.close()

        def failed(e):
            self.ok.set_sensitive(True)
            if isinstance(e, HttpError) and e.status == 401:
                self.err.set_label("The server refused this token.")
            else:
                self.err.set_label(_message(e))

        _in_thread(client.ping, ok, failed)


class AlbumChooser(Gtk.Window):
    """The server's albums, newest change first, and a form for a new one."""

    def __init__(self, main: ScanWindow):
        super().__init__(title="Albums", transient_for=main, modal=True, default_width=520, default_height=560)
        self.main = main
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_child(box)

        box.append(Gtk.Label(label="New album", xalign=0, css_classes=["heading"]))
        form = Gtk.Box(spacing=8)
        box.append(form)
        self.name = Gtk.Entry(text=default_name(), hexpand=True)
        form.append(self.name)
        self.size = Gtk.ComboBoxText.new_with_entry()
        for s in NAMED_SIZES:
            self.size.append_text(s)
        self.size.set_active(0)
        self.size.set_tooltip_text("letter, a4, a3, or a size such as 10x12in")
        form.append(self.size)
        create = Gtk.Button(label="Create")
        create.add_css_class("suggested-action")
        create.connect("clicked", self._create)
        form.append(create)
        self.err = Gtk.Label(xalign=0, wrap=True)
        self.err.add_css_class("error")
        box.append(self.err)

        box.append(Gtk.Label(label="Carry on with an album on the server", xalign=0, css_classes=["heading"]))
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.connect("row-activated", self._open)
        box.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True))
        self.ids: list[str] = []
        _in_thread(main.client.albums, self._got_albums, lambda e: self.err.set_label(f"Cannot list albums: {_message(e)}"))

    def _got_albums(self, albums: list[dict]) -> None:
        self.ids = [a["id"] for a in albums]
        for a in albums:
            text = f"{a['name']}\n{a.get('page_size') or 'no page size'} · {a['pages']} pages · {a['shots']} shots · changed {a['updated']}"
            self.listbox.append(Gtk.Label(label=text, xalign=0, margin_top=6, margin_bottom=6, margin_start=6))
        if not albums:
            self.listbox.append(Gtk.Label(label="No albums yet.", xalign=0))

    def _open(self, _lb, row) -> None:
        i = row.get_index()
        if 0 <= i < len(self.ids):
            self.main.open_album(self.ids[i])
            self.close()

    def _create(self, *_):
        try:
            size = check_page_size(self.size.get_active_text() or "")
            self.main.new_album(self.name.get_text(), size)
        except ValueError as e:
            self.err.set_label(str(e))
            return
        self.close()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.window: ScanWindow | None = None

    def do_activate(self):
        if self.window is None:
            self.window = ScanWindow(self, Settings.load(), Outbox(data_dir() / "outbox"))
            self.window.start()
        else:
            self.window.present()


def run() -> int:
    return App().run([])

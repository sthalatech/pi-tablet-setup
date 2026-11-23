#!/usr/bin/env python3
"""touchlock - touch-friendly Wayland screen locker.

Uses ext-session-lock-v1 (via gtk4-layer-shell's Gtk4SessionLock) so it is a
real compositor-enforced lock, and authenticates with PAM (service "swaylock",
i.e. the normal login password). Has its own on-screen keyboard because the
compositor hides other surfaces (squeekboard) while the session is locked.

  touchlock.py          lock the session
  touchlock.py --test   preview in a normal fullscreen window (no real lock)
"""
import ctypes
import ctypes.util
import os
import pwd
import sys
import threading
import time

TEST = "--test" in sys.argv
if not TEST:
    # gtk4-layer-shell must be linked before libwayland-client.
    ctypes.CDLL("libgtk4-layer-shell.so")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

if not TEST:
    gi.require_version("Gtk4SessionLock", "1.0")
    from gi.repository import Gtk4SessionLock as SessionLock  # noqa: E402

USER = pwd.getpwuid(os.getuid()).pw_name
PAM_SERVICE = "swaylock"
MAX_FAILS = 5          # failed attempts before a cool-down
LOCKOUT_S = 30         # cool-down length
IDLE_HIDE_S = 30       # hide keyboard after this long without input
TEST_TIMEOUT_S = 180   # --test mode closes itself after this long

# ---------------------------------------------------------------- PAM ------
_pam = ctypes.CDLL(ctypes.util.find_library("pam"))
_libc = ctypes.CDLL(ctypes.util.find_library("c"))


class _PamMessage(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class _PamResponse(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


_CONV = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_int,
    ctypes.POINTER(ctypes.POINTER(_PamMessage)),
    ctypes.POINTER(ctypes.POINTER(_PamResponse)), ctypes.c_void_p)


class _PamConv(ctypes.Structure):
    _fields_ = [("conv", _CONV), ("appdata_ptr", ctypes.c_void_p)]


_pam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                           ctypes.POINTER(_PamConv), ctypes.POINTER(ctypes.c_void_p)]
_pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
_pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
_libc.calloc.restype = ctypes.c_void_p
_libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
_libc.strdup.restype = ctypes.c_void_p
_libc.strdup.argtypes = [ctypes.c_char_p]


def pam_check(user, password):
    secret = password.encode()

    def conv(n, msgs, resp, _data):
        arr = _libc.calloc(n, ctypes.sizeof(_PamResponse))
        if not arr:
            return 5  # PAM_BUF_ERR
        out = ctypes.cast(arr, ctypes.POINTER(_PamResponse))
        for i in range(n):
            style = msgs[i].contents.msg_style
            if style == 1:      # PAM_PROMPT_ECHO_OFF
                out[i].resp = _libc.strdup(secret)
            elif style == 2:    # PAM_PROMPT_ECHO_ON
                out[i].resp = _libc.strdup(user.encode())
        resp[0] = out
        return 0

    pconv = _PamConv(_CONV(conv), None)
    handle = ctypes.c_void_p()
    if _pam.pam_start(PAM_SERVICE.encode(), user.encode(),
                      ctypes.byref(pconv), ctypes.byref(handle)) != 0:
        return False
    rc = _pam.pam_authenticate(handle, 0)
    _pam.pam_end(handle, rc)
    return rc == 0


# ---------------------------------------------------------------- UI -------
CSS = b"""
window.touchlock {
  background: linear-gradient(165deg, #0f172a 0%, #172036 55%, #0a101d 100%);
  color: #e2e8f0;
  font-family: "Noto Sans", "DejaVu Sans", sans-serif;
}
.clock  { font-weight: 200; color: #f8fafc; }
.date   { color: #94a3b8; }
.hint   { color: #94a3b8; }
.user   { color: #cbd5e1; font-weight: 600; }
.pill {
  background: rgba(255,255,255,0.07);
  border: 2px solid rgba(255,255,255,0.16);
  border-radius: 999px;
  padding: 0 28px;
  transition: border-color 200ms;
}
.pill.error { border-color: #f87171; }
.pill.shake { animation: shake 450ms ease-out; }
@keyframes shake {
  0%   { margin-left: 0;    margin-right: 0; }
  20%  { margin-left: 40px; margin-right: 0; }
  40%  { margin-left: 0;    margin-right: 40px; }
  60%  { margin-left: 24px; margin-right: 0; }
  80%  { margin-left: 0;    margin-right: 12px; }
  100% { margin-left: 0;    margin-right: 0; }
}
.pw      { color: #f8fafc; letter-spacing: 6px; }
.pw.dim  { color: #64748b; letter-spacing: 0; }
.status       { color: #94a3b8; }
.status.err   { color: #f87171; }
.status.ok    { color: #4ade80; }
.status.info  { color: #93c5fd; }
button.key {
  background: rgba(255,255,255,0.10);
  color: #f1f5f9;
  border: none;
  border-radius: 14px;
  box-shadow: 0 3px 0 rgba(0,0,0,0.35);
  padding: 0; min-height: 0; min-width: 0;
  transition: background 60ms;
}
button.key:active      { background: rgba(148,163,184,0.60); box-shadow: none; }
button.key.mod         { background: rgba(255,255,255,0.05); color: #cbd5e1; }
button.key.mod:active  { background: rgba(148,163,184,0.45); }
button.key.on          { background: #3b82f6; color: #ffffff; }
button.key.enter       { background: #2563eb; color: #ffffff; font-weight: 600; }
button.key.enter:active { background: #1e40af; }
button.key:disabled    { opacity: 0.35; }
button.eye {
  background: none; border: none; box-shadow: none;
  color: #94a3b8; padding: 0 6px; min-height: 0;
}
button.eye:active { color: #f8fafc; }
"""

DIGITS = "1234567890"
LAYERS = {
    "abc": [list(DIGITS), list("qwertyuiop"), list("asdfghjkl"), list("zxcvbnm")],
    "sym": [list(DIGITS), list("!@#$%^&*()"), list("-_=+/\\:;'"), list('"<>?,|~')],
}


class LockView:
    """One lock window per monitor."""

    def __init__(self, locker, monitor, idx):
        self.locker = locker
        self.monitor = monitor
        geo = monitor.get_geometry()
        w, h = geo.width, geo.height
        short = min(w, h)
        kb_w = min(w * 0.96, h * 1.15, 1150)
        self.key_h = int(min(kb_w / 10 * 0.92, h * 0.085))
        gap = max(6, int(kb_w / 120))

        self.win = Gtk.Window(application=locker.app)
        self.win.set_name(f"lock{idx}")
        self.win.add_css_class("touchlock")
        self.win.set_decorated(False)

        sizes = Gtk.CssProvider()
        sel = f"window#lock{idx}"
        sizes.load_from_string(
            f"{sel} .clock {{ font-size: {int(short * 0.17)}px; }}\n"
            f"{sel} .date  {{ font-size: {int(short * 0.04)}px; }}\n"
            f"{sel} .hint  {{ font-size: {int(short * 0.032)}px; }}\n"
            f"{sel} .user  {{ font-size: {int(self.key_h * 0.36)}px; }}\n"
            f"{sel} .pill  {{ min-height: {int(self.key_h * 0.95)}px; }}\n"
            f"{sel} .pw    {{ font-size: {int(self.key_h * 0.40)}px; }}\n"
            f"{sel} .eye   {{ font-size: {int(self.key_h * 0.28)}px; }}\n"
            f"{sel} .status {{ font-size: {int(self.key_h * 0.30)}px; }}\n"
            f"{sel} button.key {{ font-size: {int(self.key_h * 0.38)}px; }}\n")
        Gtk.StyleContext.add_provider_for_display(
            monitor.get_display(), sizes, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_margin_top(int(h * 0.04))
        root.set_margin_bottom(int(h * 0.03))
        self.win.set_child(root)

        top = Gtk.Box(vexpand=True)
        root.append(top)

        self.clock = Gtk.Label(css_classes=["clock"])
        self.date = Gtk.Label(css_classes=["date"])
        root.append(self.clock)
        root.append(self.date)

        self.hint = Gtk.Label(label="Tap anywhere to unlock", css_classes=["hint"])
        self.hint.set_margin_top(int(short * 0.05))
        root.append(self.hint)

        root.append(Gtk.Box(vexpand=True))

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=int(gap * 1.6))
        panel.set_halign(Gtk.Align.CENTER)
        panel.set_size_request(int(kb_w), -1)

        user = Gtk.Label(label=USER, css_classes=["user"])
        panel.append(user)

        self.pill = Gtk.Box(css_classes=["pill"], spacing=8)
        self.pill.set_size_request(int(min(kb_w * 0.8, 820)), -1)
        self.pill.set_halign(Gtk.Align.CENTER)
        self.pw = Gtk.Label(hexpand=True, xalign=0.5, css_classes=["pw"],
                            ellipsize=Pango.EllipsizeMode.END)
        self.eye = Gtk.Button(css_classes=["eye"])
        self.eye.connect("clicked", lambda *_: locker.toggle_show())
        self.pill.append(self.pw)
        self.pill.append(self.eye)
        panel.append(self.pill)

        self.status = Gtk.Label(css_classes=["status"])
        self.status.set_size_request(-1, int(self.key_h * 0.5))
        panel.append(self.status)

        self.grid = Gtk.Grid(column_homogeneous=True, row_spacing=gap, column_spacing=gap)
        self.grid.set_margin_top(gap)
        panel.append(self.grid)

        self.revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=220, child=panel)
        root.append(self.revealer)

        tap = Gtk.GestureClick()
        tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        tap.connect("pressed", lambda *_: locker.activity())
        self.win.add_controller(tap)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.win.add_controller(keys)

        self.layout_sig = None
        self.build_keys()
        self.tick()
        self.render()

    # -- keyboard ---------------------------------------------------------
    def _key(self, label, cb, span, row, col, classes=()):
        b = Gtk.Button(label=label, css_classes=["key", *classes])
        b.set_size_request(-1, self.key_h)
        b.set_focusable(False)
        b.connect("clicked", lambda *_: cb())
        self.grid.attach(b, col, row, span, 1)
        return b

    def build_keys(self):
        lk = self.locker
        sig = (lk.layer, lk.shift, lk.caps)
        if sig == self.layout_sig:
            return
        self.layout_sig = sig
        while (child := self.grid.get_first_child()) is not None:
            self.grid.remove(child)

        rows = LAYERS[lk.layer]
        upper = lk.layer == "abc" and (lk.shift or lk.caps)

        def chars(row_chars, row, col0):
            for i, c in enumerate(row_chars):
                ch = c.upper() if upper else c
                self._key(ch, lambda ch=ch: lk.type_char(ch), 2, row, col0 + i * 2)

        chars(rows[0], 0, 0)
        chars(rows[1], 1, 0)
        chars(rows[2], 2, 1)
        if lk.layer == "abc":
            cls = ("mod", "on") if (lk.shift or lk.caps) else ("mod",)
            self._key("⇪" if lk.caps else "⇧", lk.press_shift, 3, 3, 0, cls)
        else:
            self._key("", lambda: None, 3, 3, 0, ("mod",)).set_sensitive(False)
        chars(rows[3], 3, 3)
        self._key("⌫", lk.backspace, 3, 3, 17, ("mod",))

        self._key("ABC" if lk.layer == "sym" else "?#1", lk.toggle_layer, 4, 4, 0, ("mod",))
        self._key("@", lambda: lk.type_char("@"), 2, 4, 4)
        self._key("space", lambda: lk.type_char(" "), 8, 4, 6, ("mod",))
        self._key(".", lambda: lk.type_char("."), 2, 4, 14)
        self.enter = self._key("Unlock", lk.submit, 4, 4, 16, ("enter",))

    def _on_key(self, _ctrl, keyval, _code, state):
        lk = self.locker
        lk.activity()
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            lk.submit()
        elif keyval == Gdk.KEY_BackSpace:
            lk.backspace()
        elif keyval == Gdk.KEY_Escape:
            lk.clear()
        elif not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            uc = Gdk.keyval_to_unicode(keyval)
            if uc >= 32:
                lk.type_char(chr(uc), physical=True)
        return True

    # -- rendering --------------------------------------------------------
    def tick(self):
        now = time.localtime()
        self.clock.set_label(time.strftime("%H:%M", now))
        self.date.set_label(time.strftime("%A, %d %B", now))

    def render(self):
        lk = self.locker
        self.build_keys()
        if lk.pw:
            self.pw.set_label(lk.pw if lk.show else "●" * len(lk.pw))
            self.pw.remove_css_class("dim")
        else:
            self.pw.set_label("Enter password")
            self.pw.add_css_class("dim")
        self.eye.set_label("Hide" if lk.show else "Show")
        text, kind = lk.status_msg()
        self.status.set_label(text)
        for k in ("err", "ok", "info"):
            (self.status.add_css_class if k == kind else self.status.remove_css_class)(k)
        (self.pill.add_css_class if kind == "err" else self.pill.remove_css_class)("error")
        self.grid.set_sensitive(lk.input_allowed())
        self.revealer.set_reveal_child(lk.revealed)
        self.hint.set_visible(not lk.revealed)

    def shake(self):
        self.pill.remove_css_class("shake")
        self.pill.add_css_class("shake")
        GLib.timeout_add(500, lambda: (self.pill.remove_css_class("shake"), False)[1])


class Locker:
    def __init__(self, app):
        self.app = app
        self.pw = ""
        self.show = False
        self.layer = "abc"
        self.shift = False
        self.caps = False
        self._last_shift = 0.0
        self.busy = False
        self.unlocking = False
        self.fails = 0
        self.lockout_until = 0.0
        self.message = ("", "")
        self.revealed = False
        self.last_activity = time.monotonic()
        self.views = []
        self.monitors = []
        self.lock = None

    # -- state ------------------------------------------------------------
    def input_allowed(self):
        return not self.busy and not self.unlocking and time.monotonic() >= self.lockout_until

    def status_msg(self):
        left = self.lockout_until - time.monotonic()
        if left > 0:
            return (f"Too many attempts. Try again in {int(left) + 1} s", "err")
        return self.message

    def refresh(self):
        for v in self.views:
            v.render()

    def activity(self):
        self.last_activity = time.monotonic()
        if not self.revealed:
            self.revealed = True
            self.refresh()

    def type_char(self, ch, physical=False):
        self.activity()
        if not self.input_allowed() or len(self.pw) >= 256:
            return
        self.pw += ch
        if self.message[1] == "err":
            self.message = ("", "")
        if self.shift and not self.caps and not physical:
            self.shift = False
        self.refresh()

    def backspace(self):
        self.activity()
        if self.input_allowed() and self.pw:
            self.pw = self.pw[:-1]
            self.refresh()

    def clear(self):
        if not self.input_allowed():
            return
        if self.pw:
            self.pw = ""
        else:
            self.revealed = False
        self.message = ("", "")
        self.refresh()

    def press_shift(self):
        self.activity()
        now = time.monotonic()
        if self.caps:
            self.caps = self.shift = False
        elif self.shift and now - self._last_shift < 0.45:
            self.caps = True
        else:
            self.shift = not self.shift
        self._last_shift = now
        self.refresh()

    def toggle_layer(self):
        self.activity()
        self.layer = "sym" if self.layer == "abc" else "abc"
        self.refresh()

    def toggle_show(self):
        self.activity()
        self.show = not self.show
        self.refresh()

    # -- auth -------------------------------------------------------------
    def submit(self):
        self.activity()
        if not self.input_allowed():
            return
        if not self.pw:
            self.message = ("Type your password first", "info")
            self.refresh()
            return
        self.busy = True
        self.message = ("Checking…", "info")
        self.refresh()
        pw = self.pw
        threading.Thread(target=lambda: GLib.idle_add(self._auth_done, pam_check(USER, pw)),
                         daemon=True).start()

    def _auth_done(self, ok):
        self.busy = False
        self.pw = ""
        self.show = False
        if ok:
            self.unlocking = True
            self.fails = 0
            self.message = ("Unlocked", "ok")
            self.refresh()
            GLib.timeout_add(200, self._unlock)
        else:
            self.fails += 1
            if self.fails >= MAX_FAILS:
                self.fails = 0
                self.lockout_until = time.monotonic() + LOCKOUT_S
                self.message = ("", "")
            else:
                left = MAX_FAILS - self.fails
                self.message = (f"Wrong password ({left} {'try' if left == 1 else 'tries'} left)", "err")
            self.refresh()
            for v in self.views:
                v.shake()
        return False

    def _unlock(self):
        if TEST:
            self.app.quit()
        else:
            self.lock.unlock()
        return False

    # -- timers -----------------------------------------------------------
    def _tick(self):
        idle = time.monotonic() - self.last_activity
        if self.revealed and self.input_allowed():
            if (not self.pw and idle > IDLE_HIDE_S) or idle > IDLE_HIDE_S * 2:
                self.pw = ""
                self.show = False
                self.revealed = False
                self.message = ("", "")
        for v in self.views:
            v.tick()
        self.refresh()
        return True

    # -- windows ----------------------------------------------------------
    def _add_monitor(self, monitor):
        if monitor in self.monitors:
            return
        self.monitors.append(monitor)
        view = LockView(self, monitor, len(self.views))
        self.views.append(view)
        if TEST:
            view.win.fullscreen_on_monitor(monitor)
        else:
            self.lock.assign_window_to_monitor(view.win, monitor)
        view.win.present()

    def _sync_monitors(self, model, *_):
        for i in range(model.get_n_items()):
            self._add_monitor(model.get_item(i))

    def start(self):
        css = Gtk.CssProvider()
        css.load_from_string(CSS.decode())
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        monitors = display.get_monitors()

        if TEST:
            mons = [monitors.get_item(i) for i in range(monitors.get_n_items())]
            dsi = [m for m in mons if (m.get_connector() or "").startswith("DSI")]
            self._add_monitor((dsi or mons)[0])
            GLib.timeout_add_seconds(TEST_TIMEOUT_S, lambda: self.app.quit())
        else:
            self.lock = SessionLock.Instance.new()
            self.lock.connect("locked", self._on_locked)
            self.lock.connect("unlocked", lambda *_: self.app.quit())
            self.lock.connect("failed", self._on_failed)
            if not self.lock.lock():
                return
            self._sync_monitors(monitors)
            monitors.connect("items-changed", self._sync_monitors)
        GLib.timeout_add_seconds(1, self._tick)

    def _on_locked(self, *_):
        ready = os.environ.get("TOUCHLOCK_READY")
        if ready:
            try:
                fd = os.open(ready, os.O_WRONLY | os.O_NONBLOCK)
                os.write(fd, b"1")
                os.close(fd)
            except OSError:
                pass

    def _on_failed(self, *_):
        print("touchlock: compositor refused the lock (already locked?)", file=sys.stderr)
        self.app.quit()


def main():
    app = Gtk.Application(application_id=None)
    locker = Locker(app)
    app.connect("activate", lambda *_: locker.start())
    app.hold()  # windows appear asynchronously; don't quit before they exist
    app.run(None)


if __name__ == "__main__":
    main()

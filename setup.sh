#!/bin/bash
# Raspberry Pi OS (trixie, labwc desktop) -> touch tablet.
#
#   sudo ./setup.sh [options]
#
# Safe to re-run: every step checks what is already there. Backups of any user
# config it changes are written next to the file as *.bak-YYYYmmdd-HHMMSS.
#
# Options (defaults in brackets):
#   --user NAME          desktop user to configure        [$SUDO_USER]
#   --scale N            display scale for the DSI panel  [1.5]
#   --dsi-overlay NAME   config.txt overlay for the panel, "" to skip
#                        [vc4-kms-dsi-ili79600-10-1inch,dsi0]
#   --lock-after S       idle seconds before locking      [300]
#   --screen-off-after S idle seconds before screen off   [600]
#   --skip-apt           don't touch apt (offline re-run)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET_USER="${SUDO_USER:-}"
SCALE="1.5"
DSI_OVERLAY="vc4-kms-dsi-ili79600-10-1inch,dsi0"
LOCK_AFTER=300
SCREEN_OFF_AFTER=600
SKIP_APT=0
GLS_TAG="v1.1.1"                                     # gtk4-layer-shell
GLS_COMMIT="4867d7b85cdf1e829fc1fd6f1d5f04c42cc99389"

while [ $# -gt 0 ]; do
  case "$1" in
    --user) TARGET_USER="$2"; shift 2 ;;
    --scale) SCALE="$2"; shift 2 ;;
    --dsi-overlay) DSI_OVERLAY="$2"; shift 2 ;;
    --lock-after) LOCK_AFTER="$2"; shift 2 ;;
    --screen-off-after) SCREEN_OFF_AFTER="$2"; shift 2 ;;
    --skip-apt) SKIP_APT=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

die() { echo "ERROR: $*" >&2; exit 1; }
log() { printf '\n==> %s\n' "$*"; }
[ "$(id -u)" = 0 ] || die "run with sudo"
[ -n "$TARGET_USER" ] || die "no user: run via sudo from the desktop user, or pass --user"
HOME_DIR="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[ -d "$HOME_DIR" ] || die "home of $TARGET_USER not found"
USER_GROUP="$(id -gn "$TARGET_USER")"
STAMP="$(date +%Y%m%d-%H%M%S)"
REBOOT=0

as_user() { sudo -u "$TARGET_USER" -H env HOME="$HOME_DIR" "$@"; }
backup() { [ -f "$1" ] && cp -p "$1" "$1.bak-$STAMP" || true; }
# write stdin to $1 as the user, only if content changed (keeps backup)
put_user_file() {
  local dst="$1" tmp; tmp="$(mktemp)"; cat > "$tmp"
  if [ -f "$dst" ] && cmp -s "$tmp" "$dst"; then rm -f "$tmp"; echo "   unchanged: $dst"; return; fi
  backup "$dst"
  as_user mkdir -p "$(dirname "$dst")"
  install -o "$TARGET_USER" -g "$USER_GROUP" -m "${MODE:-644}" "$tmp" "$dst"; rm -f "$tmp"
  echo "   wrote: $dst"
}

# ---------------------------------------------------------------- 1. display
log "1/8 Display overlay in config.txt"
CFG=/boot/firmware/config.txt
if [ -z "$DSI_OVERLAY" ]; then
  echo "   skipped (--dsi-overlay \"\")"
elif grep -qE "^dtoverlay=${DSI_OVERLAY%%,*}(,|$)" "$CFG"; then
  echo "   already present"
else
  backup "$CFG"
  printf '\n[all]\n# touch panel (pi-tablet-setup)\ndtoverlay=%s\n' "$DSI_OVERLAY" >> "$CFG"
  echo "   added dtoverlay=$DSI_OVERLAY"; REBOOT=1
fi
if [ -n "$DSI_OVERLAY" ] && grep -q '^display_auto_detect=1' "$CFG"; then
  backup "$CFG"; sed -i 's/^display_auto_detect=1/display_auto_detect=0/' "$CFG"
  echo "   display_auto_detect=0"; REBOOT=1
fi

# ---------------------------------------------------------------- 2. packages
PKGS=(squeekboard swayidle wlopm kanshi wlr-randr grim chromium python3-gi
      git meson ninja-build pkg-config libgtk-4-dev gir1.2-gtk-4.0 libwayland-dev
      wayland-protocols gobject-introspection libgirepository-2.0-dev
      libgirepository1.0-dev)
log "2/8 Packages"
if [ "$SKIP_APT" = 1 ]; then
  echo "   skipped (--skip-apt)"
else
  lists_ok() {
    local f; f="$(ls /var/lib/apt/lists/*debian_dists_*_main_binary-"$(dpkg --print-architecture)"_Packages 2>/dev/null | head -1)"
    [ -n "$f" ] && [ -s "$f" ]
  }
  apt_update() { apt-get -o Acquire::Languages=none -o Acquire::Retries=5 update -q; }
  apt_update || true
  if ! lists_ok; then
    # Seen on flaky networks: index files end up 0 bytes and apt finds nothing.
    echo "   package lists empty/broken - clearing and retrying"
    rm -rf /var/lib/apt/lists/partial/* /var/lib/apt/lists/*_Packages /var/lib/apt/lists/*InRelease
    apt_update
    lists_ok || die "apt package lists still empty; check network and re-run"
  fi
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${PKGS[@]}"
fi

# ---------------------------------------------------------------- 3. gtk4-layer-shell
log "3/8 gtk4-layer-shell $GLS_TAG (session-lock support, user-local)"
PC="$HOME_DIR/.local/lib/pkgconfig/gtk4-layer-shell-0.pc"
if [ -f "$PC" ] && grep -q "^Version: ${GLS_TAG#v}$" "$PC"; then
  echo "   already installed"
else
  SRC="$HOME_DIR/src/gtk4-layer-shell"
  as_user rm -rf "$SRC"
  as_user git clone -q --depth 1 --branch "$GLS_TAG" https://github.com/wmww/gtk4-layer-shell "$SRC"
  got="$(as_user git -C "$SRC" rev-parse HEAD)"
  [ "$got" = "$GLS_COMMIT" ] || die "gtk4-layer-shell $GLS_TAG is $got, expected $GLS_COMMIT"
  as_user meson setup "$SRC/build" "$SRC" -Dexamples=false -Ddocs=false -Dtests=false \
    -Dvapi=false -Dintrospection=true --prefix="$HOME_DIR/.local" --libdir=lib >/dev/null
  as_user ninja -C "$SRC/build" install >/dev/null
  echo "   built and installed to ~/.local/lib"
fi

# ---------------------------------------------------------------- 4. touchlock
log "4/8 touchlock lock screen"
put_user_file "$HOME_DIR/.local/share/touchlock/touchlock.py" < "$HERE/files/touchlock.py"
MODE=755 put_user_file "$HOME_DIR/.local/bin/touchlock" < "$HERE/files/touchlock"
MODE=755 put_user_file "$HOME_DIR/.local/bin/rotate-screen" < "$HERE/files/rotate-screen"
put_user_file "$HOME_DIR/.local/share/applications/rotate-screen.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Rotate Screen
Comment=Rotate the display 90° clockwise
Exec=rotate-screen
Icon=object-rotate-right
Categories=Settings;DesktopSettings;
EOF
MODE=755 put_user_file "$HOME_DIR/.local/bin/swaylock" <<'EOF'
#!/bin/sh
# Stand-in so the panel Lock button uses touchlock. Real one: /usr/bin/swaylock
exec "$HOME/.local/bin/touchlock" -f
EOF

# ---------------------------------------------------------------- 5. labwc
log "5/8 labwc: touch mapping, lock keys, idle lock, PATH"
LABWC="$HOME_DIR/.config/labwc"

TOUCH_NAME="$(awk '/^N: Name=/{n=$0} /^B: ABS=/{ if (length($0) > 0 && n ~ /ili|touch|ft5|goodix|edt|Touch/) {sub(/^N: Name="/,"",n); sub(/"$/,"",n); print n; exit} }' /proc/bus/input/devices)"
DSI_OUT="$(for c in /sys/class/drm/card*-DSI-*; do [ -e "$c" ] && basename "$c" | sed 's/^card[0-9]*-//' && break; done)"
DSI_OUT="${DSI_OUT:-DSI-1}"
if [ -n "$TOUCH_NAME" ]; then
  echo "   touch device: $TOUCH_NAME -> $DSI_OUT"
else
  echo "   touch device not detected yet (reboot needed?) - mapping all touch devices to $DSI_OUT; re-run after reboot"
fi

RC="$LABWC/rc.xml"
as_user python3 - "$RC" "$TOUCH_NAME" "$DSI_OUT" "$HOME_DIR/.local/bin/touchlock" "$STAMP" <<'PY'
import os, re, shutil, sys
rc, touch, out, lock, stamp = sys.argv[1:]
rot = os.path.join(os.path.dirname(lock), "rotate-screen")
if os.path.exists(rc):
    s = open(rc).read()
else:
    os.makedirs(os.path.dirname(rc), exist_ok=True)
    s = '<?xml version="1.0"?>\n<openbox_config xmlns="http://openbox.org/3.4/rc">\n</openbox_config>\n'
orig = s
s = re.sub(r'[ \t]*<touch\b[^>]*/>\n?', '', s)
s = re.sub(r'[ \t]*<!-- pi-tablet-setup -->.*?<!-- /pi-tablet-setup -->\n?', '', s, flags=re.S)
s = re.sub(r'[ \t]*<keybind key="[^"]*"><action name="Execute" command="[^"]*(touchlock|rotate-screen)[^"]*" ?/></keybind>\n?', '', s)
s = re.sub(r'[ \t]*<keyboard>\s*</keyboard>\n?', '', s)
name = f' deviceName="{touch}"' if touch else ''
block = (f'  <!-- pi-tablet-setup -->\n'
         f'  <touch{name} mapToOutput="{out}" mouseEmulation="no"/>\n'
         f'  <keyboard>\n'
         f'    <keybind key="C-A-l"><action name="Execute" command="{lock} -f" /></keybind>\n'
         f'    <keybind key="W-l"><action name="Execute" command="{lock} -f" /></keybind>\n'
         f'    <keybind key="W-r"><action name="Execute" command="{rot}" /></keybind>\n'
         f'    <keybind key="C-A-r"><action name="Execute" command="{rot}" /></keybind>\n'
         f'  </keyboard>\n'
         f'  <!-- /pi-tablet-setup -->\n')
close = re.search(r'</(openbox_config|labwc_config)>', s)
if not close:
    sys.exit(f"{rc}: no closing root tag, not touching it")
s = s[:close.start()] + block + s[close.start():]
if s != orig:
    if os.path.exists(rc):
        shutil.copy2(rc, f"{rc}.bak-{stamp}")
    open(rc, "w").write(s)
    print(f"   wrote: {rc}")
else:
    print(f"   unchanged: {rc}")
PY

# autostart: replace any swayidle invocation with ours, keep everything else
AS="$LABWC/autostart"
as_user python3 - "$AS" "$LOCK_AFTER" "$SCREEN_OFF_AFTER" "$STAMP" <<'PY'
import os, re, shutil, sys
path, lock_after, off_after, stamp = sys.argv[1:]
orig = open(path).read() if os.path.exists(path) else ""
s = re.sub(r'\n?# pi-tablet-setup.*?# /pi-tablet-setup\n?', '\n', orig, flags=re.S)
lines, out, buf = s.split("\n"), [], ""
for ln in lines:                      # join backslash continuations
    buf += ln
    if buf.endswith("\\"):
        buf = buf[:-1] + "\n"
        continue
    if not re.search(r'\bswayidle\b|^\s*LOCKCMD=', buf):
        out.append(buf)
    buf = ""
body = "\n".join(out).strip("\n")
if not body.startswith("#!"):
    body = "#!/bin/sh\n" + body
block = f'''# pi-tablet-setup
LOCKCMD="$HOME/.local/bin/touchlock -f"
swayidle -w \\
  timeout {lock_after} "$LOCKCMD" \\
  timeout {off_after} 'wlopm --off \\*' \\
  resume 'wlopm --on \\*' \\
  before-sleep "$LOCKCMD" &
# /pi-tablet-setup
'''
head, _, rest = body.partition("\n")
new = head + "\n" + block + (rest + "\n" if rest.strip() else "")
if new != orig:
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.bak-{stamp}")
    open(path, "w").write(new)
    print(f"   wrote: {path}")
else:
    print(f"   unchanged: {path}")
PY

ENVF="$LABWC/environment"
if as_user grep -q '^PATH=.*\.local/bin' "$ENVF" 2>/dev/null; then
  echo "   unchanged: $ENVF"
else
  backup "$ENVF"
  as_user sed -i '/^PATH=/d' "$ENVF" 2>/dev/null || true
  echo "PATH=$HOME_DIR/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/games:/usr/games" \
    | as_user tee -a "$ENVF" >/dev/null
  echo "   wrote: $ENVF"
fi

# ---------------------------------------------------------------- 6. scaling
log "6/8 Display scale $SCALE (kanshi)"
put_user_file "$HOME_DIR/.config/kanshi/config" <<EOF
profile tablet {
  output $DSI_OUT enable scale $SCALE
}
profile docked {
  output $DSI_OUT enable scale $SCALE
  output HDMI-A-1 enable
}
EOF

# ---------------------------------------------------------------- 7. chromium
log "7/8 Chromium touch flags"
FLAGS=/etc/chromium.d/90-tablet-touch
NEWFLAGS='# Tablet / touchscreen tuning (pi-tablet-setup)
export CHROMIUM_FLAGS="$CHROMIUM_FLAGS --ozone-platform=wayland --enable-wayland-ime --wayland-text-input-version=3 --touch-events=enabled --top-chrome-touch-ui=enabled --pull-to-refresh=1 --enable-features=OverscrollHistoryNavigation,TouchpadOverscrollHistoryNavigation"'
if [ -f "$FLAGS" ] && [ "$(cat "$FLAGS")" = "$NEWFLAGS" ]; then
  echo "   unchanged: $FLAGS"
else
  mkdir -p /etc/chromium.d; printf '%s\n' "$NEWFLAGS" > "$FLAGS"; chmod 644 "$FLAGS"
  echo "   wrote: $FLAGS"
fi

# ---------------------------------------------------------------- 8. apply
log "8/8 Apply"
UID_T="$(id -u "$TARGET_USER")"
if pgrep -u "$TARGET_USER" -x labwc >/dev/null; then
  pkill -HUP -u "$TARGET_USER" -x labwc && echo "   labwc config reloaded"
  echo "   idle-lock, PATH and scale changes take full effect after logout/reboot"
else
  echo "   no running desktop session; changes apply at next login"
fi

# smoke test: the session-lock typelib must load
if as_user env LD_LIBRARY_PATH="$HOME_DIR/.local/lib" GI_TYPELIB_PATH="$HOME_DIR/.local/lib/girepository-1.0" \
     python3 -c "import ctypes; ctypes.CDLL('libgtk4-layer-shell.so'); import gi; gi.require_version('Gtk4SessionLock','1.0'); from gi.repository import Gtk4SessionLock" 2>/dev/null; then
  echo "   touchlock dependencies OK"
else
  echo "   WARNING: Gtk4SessionLock failed to load - lock screen will not work" >&2
fi

echo
[ "$REBOOT" = 1 ] && echo "Reboot required (display overlay changed): sudo reboot" || echo "Done. Reboot recommended."

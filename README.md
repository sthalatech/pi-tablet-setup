# pi-tablet-setup

Turns a Raspberry Pi 5 with a DSI touch panel into something you can actually use as a tablet. Tested on Raspberry Pi OS trixie (labwc desktop) with a 10.1" ILI79600 panel.

## What it sets up

- **Touch** goes to the right screen as real touch events, not fake mouse clicks, so scrolling, pinch-zoom and swipes work.
- **A lock screen you can use with your fingers.** The stock swaylock hides the on-screen keyboard while locked and gives no feedback. `touchlock` replaces it with a clock, a built-in keyboard and clear messages (checking, wrong password, unlocked). It's a proper Wayland session lock and checks your normal login password.
- **Chromium** opens the on-screen keyboard when you tap a text field, and gets touch-sized tabs, swipe back/forward and pull-to-refresh.
- **Display scale 1.5**, so text and icons aren't tiny.
- **Lock shortcuts** Ctrl+Alt+L and Super+L. The panel's Lock button also uses touchlock.

## Install

On a fresh Raspberry Pi OS (64-bit, desktop):

```sh
git clone https://github.com/sthalatech/pi-tablet-setup
cd pi-tablet-setup
sudo ./setup.sh
sudo reboot
```

If the script says it couldn't find the touch device (the panel only shows up after the first reboot), run it again after rebooting:

```sh
sudo ./setup.sh --skip-apt
```

It's safe to run as many times as you like. Steps that are already done get skipped, and any config file it changes is backed up as `*.bak-<timestamp>`.

## Options

```
--scale 2                  display scale (default 1.5)
--lock-after 120           lock after N idle seconds (default 300)
--screen-off-after 300     screen off after N idle seconds (default 600)
--dsi-overlay NAME         config.txt overlay for your panel, "" to skip
--user NAME                configure another user (default: whoever ran sudo)
--skip-apt                 don't install anything, just apply the config
```

Using a different panel? Pass its overlay with `--dsi-overlay`, or pass `""` if you've already set it up yourself.

## Good to know

- Preview the lock screen without locking anything: `~/.local/bin/touchlock --test`
- If the lock screen ever crashes, the session stays locked and touchlock restarts itself.
- The original swaylock is still at `/usr/bin/swaylock`.
- The Raspberry Pi "Screen Configuration" app can reset the scale, and the screen-blanking toggle in Control Centre can rewrite the idle-lock settings. Re-run the script to fix both.
- To build its lock screen, touchlock needs gtk4-layer-shell 1.1, which isn't packaged in Debian trixie yet. The script builds it into `~/.local` from a pinned release.

## License

Public domain ([Unlicense](LICENSE)).

# Printer display

A small touch screen for the front of one printer. It shows the printer's state, what
filament to load for the next waiting job, and a **Set Ready** button.

![Four example screens: load a filament, Set Ready picks up a job with a low-filament warning, printing, and paused](../docs/display-preview.png)

*Mock-up of the layout with sample data.*

**Status: untested on real hardware.** The configuration passes ESPHome's validation, but
it could not be compiled or flashed where it was written. Treat the first flash as the test.

## What you need

- An **ESP32-2432S028** board ("Cheap Yellow Display"): 2.8" 320×240 screen, single
  micro-USB port. The `R` version has resistive touch; without touch it still works as a
  display, but the Set Ready button can't be pressed.
- A computer with [ESPHome](https://esphome.io/guides/installing_esphome) installed and
  a micro-USB **data** cable.
- The SetReady server reachable from your Wi-Fi (2.4 GHz), with the Watchtower login set
  in Settings if you want the filament advice.

## Setting one up

1. On the SetReady page, open **Settings → Printer displays**, choose the printer and
   press **Create token**. Copy the token; it is shown once.
2. Copy `printer-display.yaml` and edit the values at the top:

   | Value | What to put |
   |---|---|
   | `device_name` | A unique name per screen, e.g. `display-p1s-5` |
   | `wifi_ssid`, `wifi_password` | Your 2.4 GHz Wi-Fi |
   | `server` | The SetReady page's address, e.g. `http://192.168.2.2:8080`, no trailing slash |
   | `token` | The token from step 1 |

3. Plug the board in and run `esphome run your-file.yaml`. Choose the serial port when
   asked. This **replaces whatever firmware is on the board**.
4. The screen shows "Connecting…", then the printer within a few seconds.

Later changes can be sent over Wi-Fi with the same command.

## What the screen shows

- **Top bar:** the printer's name and a dot: green when ready, red when printing, paused
  or waiting to be cleared, amber when offline.
- **Status:** e.g. "Printing 26%", with time left and finish time.
- **Advice:**
  - "Load for next job:" with the job and the filament to load, each with a colour dot.
  - "Set Ready picks up:" with the job, when the loaded filament already covers one.
  - "Load for waiting jobs:" on a busy printer that is the closest fit for a stuck job.
  - An amber "Low:" line when the spool has less than the job needs.
- **Hold: Set Ready:** appears when the plate is waiting to be cleared. Hold it for a
  second; it turns brighter while held, then sends. A brush against the screen does nothing.

It asks the server every 10 seconds.

## If something is wrong

| What you see | Likely cause |
|---|---|
| "No Wi-Fi" | Wrong Wi-Fi name or password, or a 5 GHz-only network |
| "Can't reach server" | Wrong `server` address or port, or the server isn't reachable from Wi-Fi |
| "Token not accepted" | Token mistyped, or revoked in Settings |
| "Not found in Watchtower" | The printer was renamed; create a new token |
| Colours inverted or mirrored | Your board is a different variant; see below |
| Touches land in the wrong place | Touch calibration; see below |

**Screen variants.** The two-USB-port version of this board uses a different screen.
For that one, change `model: ILI9341` to `model: ST7789V` and set `invert_colors: true`.
If the picture is upside down or mirrored, change `mirror_x` / `mirror_y` under the
display's `transform`.

**Touch calibration.** If the button doesn't respond where it is drawn, flip `mirror_x`
or `mirror_y` under the touchscreen's `transform` first. For fine tuning, watch the log
(`esphome logs your-file.yaml`) while pressing the corners and adjust the `calibration`
numbers to the raw values it reports.

**Colours are approximate.** The board doesn't have the memory for full colour, so the
filament dots use a reduced palette. Similar shades can look the same.

## What a display is allowed to do

A token works for one printer only. With it, a display can read that printer's summary
and send Set Ready for it. It can't see other printers, cameras or settings, and can't
pause or stop a print. Revoke a token under **Settings → Printer displays** if a screen
is lost or replaced.

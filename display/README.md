# Printer display

A small touch screen for the front of one printer. It shows the printer's state, what
filament to load for the next waiting job, and a **Set Ready** button.

![Four example screens: load a filament, Set Ready picks up a job with a low-filament warning, printing, and paused](../docs/display-preview.png)

*Mock-up of the layout with sample data.*

**Status: untested on real hardware.** The firmware is built automatically, so it is
known to compile, but it has not yet been run on a board. Treat the first flash as the test.

## What you need

- An **ESP32-2432S028** board ("Cheap Yellow Display"): 2.8" 320×240 screen, single
  micro-USB port. The `R` version has resistive touch; without touch it still works as a
  display, but the Set Ready button can't be pressed.
- A micro-USB **data** cable and a computer with Chrome or Edge, for the one-off flash.
- A phone, for the setup steps.
- The SetReady server reachable from your Wi-Fi (2.4 GHz), with the Watchtower login set
  in Settings if you want the filament advice.

Every board runs the same firmware. Nothing is edited per printer; each board is told
its Wi-Fi, server and token after it starts.

## 1. Flash the board (once)

1. Download the firmware from the
   [display-latest release](https://github.com/M4-Consulting-Ltd/Watchtower-SetReady/releases/tag/display-latest).
   These boards ship with one of three screens that look identical, so there are three
   files. Start with the first; if the picture is scrambled, flash the next one.

   | File | Screen | Usually found on |
   |---|---|---|
   | `printer-display.factory.bin` | ILI9341 | boards with a single micro-USB port |
   | `printer-display-st7789.factory.bin` | ST7789 | boards with micro-USB and USB-C |
   | `printer-display-ili9342.factory.bin` | ILI9342 | some boards with micro-USB and USB-C |

2. Plug the board into the computer and open <https://web.esphome.io> in Chrome or Edge.
3. Click **Connect**, pick the board's serial port, then **Install** and choose the file.

This **replaces whatever firmware is on the board**. If the port doesn't appear, hold the
board's **BOOT** button while plugging it in.

## 2. Set it up (on the board)

The screen walks you through two steps, each with a QR code to scan with your phone's
camera, so there is nothing to type to get to the right page.

**Step 1 of 2: Wi-Fi.** Scan the code on the screen. Your phone offers to join the
board's own network (its name, such as `printer-display-a1b2c3`, is shown at the
bottom). Once joined, a page opens by itself; if it doesn't, browse to
`http://192.168.4.1`. Choose your own Wi-Fi and enter its password.

**Step 2 of 2: server and token.** The board joins your Wi-Fi and shows a second code.
Put your phone back on your normal Wi-Fi and scan it to open the board's settings page
(its address is also shown at the bottom). Fill in:

- **SetReady server address:** where the SetReady page runs, e.g. `192.168.2.2:8080`.
  `http://` is added for you.
- **Display token:** on the SetReady page, open **Settings → Printer displays**, choose
  the printer this board is for, press **Create token**, and paste it here.

Then press **Apply and restart**. The board restarts and shows the printer within a few
seconds. The settings are kept on the board and survive power cuts.

**Changing things later.** Open the board's address again to change the server or
token. Whenever the server can't be reached, the screen shows the reason with a QR code
to that page. If the
Wi-Fi changes or can't be joined, the board goes back to step 1 after about a minute.

## Building it yourself (optional)

With [ESPHome](https://esphome.io/guides/installing_esphome) installed:
`esphome run printer-display.yaml`. The `default_server` and `default_token` values at
the top of the file can bake those two settings in, which saves step 2 when you are
only making one.

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
| Stuck on "Setup: step 1 of 2" | Wrong Wi-Fi password, or a 5 GHz-only network; join the board's network again and re-enter |
| "Can't reach server" | Wrong server address or port (it is shown underneath), or the server isn't reachable from Wi-Fi |
| "Token not accepted" | Token mistyped, or revoked in Settings; create a new one and enter it on the board's page |
| "Not found in Watchtower" | The printer was renamed; create a new token |
| Scrambled picture, or inverted colours | The firmware is for a different screen type; see below |
| Touches land in the wrong place | Touch calibration; see below |

**Scrambled picture or wrong colours.** The firmware doesn't match the board's screen
type. Flash one of the other two files listed under "Flash the board". Flashing again
keeps the Wi-Fi, server and token already entered.

**Upside down.** Build it yourself with `rotation: "270"` at the top of
`printer-display.yaml` (the published files use 90).

**Touch calibration.** If the button doesn't respond where it is drawn, flip `mirror_x`
or `mirror_y` under the touchscreen's `transform` first. For fine tuning, watch the log
(`esphome logs printer-display.yaml`, or the **Logs** button on web.esphome.io) while pressing the corners and adjust the `calibration`
numbers to the raw values it reports.

## Who can change a board's settings

The board's settings page has no login, like the board itself: anyone on your Wi-Fi who
opens its address can change the server or token. The token is never shown back.

## What a display is allowed to do

A token works for one printer only. With it, a display can read that printer's summary
and send Set Ready for it. It can't see other printers, cameras or settings, and can't
pause or stop a print. Revoke a token under **Settings → Printer displays** if a screen
is lost or replaced.

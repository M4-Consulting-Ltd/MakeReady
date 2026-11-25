# Printer display

A small touch screen for the front of one printer. It shows the printer's state, what
filament to load for the next waiting job, and a **Set Ready** button.

![Four example screens: printing with time left and a progress bar, load a filament before Set Ready, Set Ready picks up a job, and the queue page](../docs/display-preview.png)

*Mock-up of the layout with sample data.*

**Status:** running on real boards (two-socket ESP32-2432S028 with the ST7789 screen,
and an LCDWiki ESP32-32E with the ILI9341 screen): flashing, both setup steps and the
printer screens work, and touch works on the ESP32-32E board.

## What you need

- An **ESP32-2432S028** board ("Cheap Yellow Display"): 2.8" 320×240 screen, single
  micro-USB port. The `R` version has resistive touch; without touch it still works as a
  display, but the Set Ready button can't be pressed. The orange **LCDWiki 2.8inch
  ESP32-32E** board (E32R28T, USB-C) uses the same pins and also works.
- A micro-USB **data** cable and a computer with Chrome or Edge, for the one-off flash.
- A phone, for the setup steps.
- The MakeReady server reachable from your Wi-Fi (2.4 GHz), with the Watchtower login set
  in Settings if you want the filament advice.

Every board runs the same firmware. Nothing is edited per printer; each board is told
its Wi-Fi, server and token after it starts.

## 1. Flash the board (once)

1. Download the firmware from the
   [display-latest release](https://github.com/M4-Consulting-Ltd/MakeReady/releases/tag/display-latest).
   These boards ship with one of three screens that look identical, so there are three
   files. Start with the first; if the picture is scrambled, flash the next one.

   | File | Screen | Usually found on |
   |---|---|---|
   | `printer-display.factory.bin` | ILI9341 | boards with a single micro-USB port; the LCDWiki ESP32-32E board (confirmed on one) |
   | `printer-display-st7789.factory.bin` | ST7789 | boards with micro-USB and USB-C (confirmed on one) |
   | `printer-display-ili9342.factory.bin` | ILI9342 | some boards with micro-USB and USB-C |

   A screen that stays **white** with the backlight on means the file is for a different
   screen chip: flash one of the other two. Nothing is harmed by trying.

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

- **MakeReady page address:** where the MakeReady page runs (what is in your browser's
  address bar when you open it), e.g. `192.168.2.2:8080`. Not Watchtower's address.
  `http://` is added for you.
- **Display token:** on the MakeReady page, open **Settings → Printer displays**, choose
  the printer this board is for, press **Create token**, and paste it here.

Then press **Save**. The board connects straight away (the **Status** line on the page
says how it got on) and shows the printer within a few
seconds. The settings are kept on the board and survive power cuts.

**If the MakeReady server moves.** You don't have to visit each board: on the MakeReady
page, **Settings → Printer displays → Settings for all displays** can send every display
the new address. See the main README.

**Changing things later.** Open the board's address again to change the server or
token. Whenever the server can't be reached, the screen shows the reason with a QR code
to that page.

**Moving to another Wi-Fi.** In the **Wi-Fi** section of the board's page, pick the
network from the list (the ones the board saw when it last started; choose "Other
network..." to type a name), enter its password and press **Connect**. The board tries
the new network for 30 seconds. If it joins, it stays there and remembers it, and its
address will probably change (the screen shows the new one if needed). If it can't join,
it stays on the network it had, and the page says so. If the old network is already
gone, the board returns to step 1 after about a minute instead.

**Renaming the board.** Each board calls itself `printer-display-` plus six characters
of its own. To give it a name you will recognise in your router's device list, type one
under **This board's name on the network** in the **Wi-Fi** section (letters, numbers
and hyphens, e.g. `p1s-4-display`) and press **Save name and restart**. The board is
then also reachable as `http://p1s-4-display.local` where your network supports that.
Leave the box blank to go back to the built-in name. The setup hotspot keeps the
built-in name.

**Updates.** Every six hours the board checks the release page for a newer firmware for
its screen type. The **Firmware** section of its page shows the result, with an
**Install update** button when there is one; the board downloads it over Wi-Fi, shows
"Updating firmware" and restarts. **Check for updates** looks straight away, and
**Install updates automatically** makes the board install new versions by itself.
**Install from a file** uploads a `.ota.bin` you have downloaded. The automatic check
only works if the board can reach the internet (github.com). A board flashed before
this feature existed has to be updated by USB once more.

**Updating many boards.** The MakeReady page lists every display under **Settings →
Printer displays**, with its version and an **Update** button (and **Update all**) when
a newer firmware is out. See the main README.

**Do updates keep the settings?** Yes: the Wi-Fi, MakeReady address, token and screen
options stay on the board through an update. (One exception has already happened: the
update that renamed the address field to "MakeReady page address" lost the saved
address, which has to be typed in once more. The settings keep their internal names
from now on, so this won't recur.)

**Which version is it running?** The **Firmware** section shows what is **Running**, e.g.
`2026.11.23-1a2b3c4 (ESP32-2432S028-7789)`: the build date, the commit it was built
from, and the screen type. The start-up screen shows the version too. The same version
is in the notes of the release you downloaded from.

**Starting up.** After power-on the screen shows a start-up page ("Connecting to
Wi-Fi…", then "Contacting server…") until the printer appears. The setup QR codes are
only shown if the board actually needs setting up.

## Building it yourself (optional)

With [ESPHome](https://esphome.io/guides/installing_esphome) installed:
`esphome run printer-display.yaml`. The `default_server` and `default_token` values at
the top of the file can bake those two settings in, which saves step 2 when you are
only making one.

The page a phone is shown while joining a new board's Wi-Fi is `setup-page.html`. The
firmware carries a compressed copy of it; after editing the page, run
`python3 make-setup-page.py` to rebuild that copy (the build checks they match).

## What the screen shows

- **Top bar:** the printer's name and a dot: green when ready, red when printing, paused
  or waiting to be cleared, amber when offline.
- **While printing:** the estimated time left and the estimated completion time in
  large type, and a bar along the bottom reading "PRINTING 28%" that fills with green
  as the print goes on. Nothing about waiting jobs is shown while the printer is busy.
- **Otherwise, a status line:** e.g. "Plate needs clearing" or "Paused or error".
- **Advice, once the print is finished:**
  - "Load for next job:" with the job and the filament to load, each with a colour dot.
  - "Set Ready picks up:" with the job, when the loaded filament already covers one.
  - An amber "Low:" line when the spool has less than the job needs.
- **Hold: Set Ready:** appears when the plate is waiting to be cleared. Hold it for a
  second; it turns brighter while held, then sends. A brush against the screen does nothing.
- **Hold: Resume print:** appears when the print is paused. Hold it for a second to
  carry on. Check the printer first: it pauses for a reason, such as a filament runout.
- **Hold: Not ready:** an outlined button on a ready printer, until a job is sent to it.
  Hold it for a second to take a Set Ready back (needs the Watchtower login in the
  MakeReady page's Settings).

### Dimming

After five minutes without a touch the screen dims to a share of the brightness set on
the board's settings page: half, unless you choose otherwise. Whether displays dim, and
how far, is set once for all of them on the MakeReady page, under **Settings → Printer
displays → Settings for all displays**. Touch the screen anywhere to bring it back; that
first touch only wakes the screen and doesn't press anything.

### The queue page

Swipe left or right across the screen, or tap the top bar, to see the jobs waiting for
this printer's model (up to thirty): jobs for shop orders first, then in Watchtower's
order. Each shows its name, print
time, the colours it needs, and a green "ready to run" or red "needs filament" depending
on what this printer has loaded. A label on the right says where the job stands:

- **Queued:** not assigned to a printer yet.
- **Assigned here** / **Soft: here:** assigned to this printer ("soft" is Watchtower's
  own pick, which it may still change).
- **Soft: P1S-3:** soft-assigned to another printer. It is listed because Watchtower may
  move it here once the right filament is loaded.

Jobs fixed to another printer, and jobs for other printer models, are left out. Up to four fit on the screen. With more, three are shown above large **Previous** and
**Next** buttons; a swipe down or up does the same.

**Tap a job** to see the filaments it needs: colour, name, weight and material for each,
marked "loaded" in green if this printer has it or "LOAD" in red if it doesn't. Tap
again to go back to the list.

**Hold: print this next**, at the bottom of that page, moves the job to the top of
Watchtower's queue, so it is the next one sent to a suitable printer that is ready with
its filament loaded. Hold it for a second, as with Set Ready. It changes the queue's
order only; it doesn't start the print on this printer by itself.

Swipe sideways or tap the top bar to return to the printer page; it also returns by
itself after 30 seconds without a touch. Two small dots in the top bar show which page
you are on.

It asks the server every 10 seconds.

## If something is wrong

| What you see | Likely cause |
|---|---|
| Stuck on "Setup: step 1 of 2" | Wrong Wi-Fi password, or a 5 GHz-only network; join the board's network again and re-enter |
| "Can't reach server" | Wrong server address or port (it is shown underneath), or the server isn't reachable from Wi-Fi |
| "Not a MakeReady server" | The address is something else, most often Watchtower itself; enter the MakeReady page's address and port |
| "Token not accepted" | Token mistyped, or revoked in Settings; create a new one and enter it on the board's page |
| "Not found in Watchtower" | The printer was renamed; create a new token |
| Scrambled picture, or inverted colours | The firmware is for a different screen type; see below |
| Touches land in the wrong place | Touch calibration; see below |

**Scrambled picture or wrong colours.** The firmware doesn't match the board's screen
type. Flash one of the other two files listed under "Flash the board". Flashing again
keeps the Wi-Fi, server and token already entered.

**Upside down.** On the board's settings page, under **Screen**, tick (or untick)
**Turn the picture upside down**. The picture turns half a turn straight away, touch follows it, and the board
remembers the choice, so it can be mounted with the USB sockets on either side.

**Touch calibration.** If touches land in the wrong place, change `touch_mirror_x` or
`touch_mirror_y` at the top of `printer-display.yaml` if it is mirrored. For fine
tuning, the log on the board's own page shows a line for each touch with the raw
values; adjust the `calibration` numbers to match the corners.

## Who can change a board's settings

The board's settings page has no login, like the board itself: anyone on your Wi-Fi who
opens its address can change the server or token. The token is never shown back.

## A case

There is a printable case for the 2.8inch ESP32-32E board in [`case/`](case/README.md).

## What a display is allowed to do

A token works for one printer only. With it, a display can read that printer's summary
and send Set Ready (or take it back) and resume a paused print for it, and move a job
from its own list to the top of the queue. It can't see other printers, cameras or settings, and can't
pause or stop a print. Revoke a token under **Settings → Printer displays** if a screen
is lost or replaced.

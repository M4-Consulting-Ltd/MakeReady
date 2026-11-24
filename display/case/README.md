# Case for the 2.8inch ESP32-32E display board

A printable two-part case for the orange LCDWiki **2.8inch ESP32-32E** board (E32R28T):

- a **back shell** the board drops into screen-up, flat on the back for 3M tape, with a
  slot for the USB-C plug;
- a **front surround** that frames the picture and covers the ends of the board.

Four screws go through the surround and the board's own mounting holes into posts in the
shell, holding all three together.

![The case assembled, from the front, with the surround lifted off, and the surround as it prints](preview.png)

Not yet printed or fitted to a board, and the window in the surround is placed from the
usual figures for a 2.8" panel, not from this board: print the fit test, and check the
window against the picture, before relying on it.

## Files

| File | What it is |
|---|---|
| `esp32-32e-fit-test.stl` | A 3 mm slice of the back shell. Prints in a few minutes; lay the board on it to check the outline, the four holes and the USB slot. |
| `esp32-32e-case-back.stl` | The back shell: 90.6 × 54.6 × 12.9 mm. |
| `esp32-32e-case-front.stl` | The surround: 2 mm thick, with four feet that reach past the glass to the PCB and a tongue that closes the USB slot above the plug. |

## Printing and fitting

- Back shell: flat on its back. Surround: face down, as supplied. No supports for either.
  0.2 mm layers, any rigid filament (PETG or ABS/ASA if it sits on a warm printer; PLA
  softens there).
- Drop the board into the shell, lay the surround on top, and fix with four **M3 × 12 mm**
  screws. They cut their own thread in the posts; stop when the surround is down, since
  it is made to sit 0.2 mm clear of the glass so it can't press on the touch layer.
- Stick 3M tape to the flat back.

## Changing it

`make-case.py` holds the measurements at the top and writes the STL files
(`pip install manifold3d trimesh`, then `python3 make-case.py`).

Measured on a board: 86 × 50 mm, PCB 1.4 mm thick, 5.5 mm from the glass to the back of
the PCB, 4.6 mm tallest part on the back, 3.2 mm holes at 77.1 × 41.6 mm centres, USB-C
socket central on its short edge.

Assumed, to be measured: the lit picture is 57.6 × 43.2 mm, starts 14.2 mm from the board
edge at the USB end and 3.4 mm in from each long edge.

#!/usr/bin/env python3
"""Printable case for the LCDWiki 2.8inch ESP32-32E display board (E32R28T).

Two parts. A back shell the board drops into screen-up, flat on the back for 3M tape,
with a slot for the USB-C plug. And a front surround that frames the picture and covers
the ends of the board; four M3 screws go through the surround and the board's own
mounting holes into posts in the shell, holding all three together.

Run:  python3 make-case.py        (needs: pip install manifold3d trimesh)
It writes the STL files next to this script. All sizes are in millimetres; change the
measurements below to suit another board and run it again.
"""
import os

import manifold3d as m3
import numpy as np
import trimesh

# ---- The board, as measured ------------------------------------------------------------
PCB_L, PCB_W = 86.0, 50.0        # outline: long side, short side
STACK = 5.5                      # front of the glass to the back of the PCB
BACK_PARTS = 4.6                 # tallest part on the back of the PCB
HOLE_D = 3.2                     # mounting holes
HOLE_L, HOLE_W = 77.1, 41.6      # hole centres: along the long side, across the short side
PCB_T = 1.4                      # thickness of the PCB alone
USB_OFFSET = 25.0                # centre of the USB-C socket from a long edge (25 = central)
# The lit picture, seen from the front with the USB end on the left. NOT YET MEASURED on
# this board: these are the usual figures for a 2.8" panel, centred along the board.
PICTURE_L, PICTURE_W = 57.6, 43.2   # size of the lit area
PICTURE_FROM_USB_END = 14.2         # board edge at the USB end to the start of the picture
PICTURE_FROM_EDGE = 3.4             # long edge of the board to the picture

# ---- The case ---------------------------------------------------------------------------
FIT = 0.3                        # gap between board and wall, each side
WALL = 2.0                       # side walls
FLOOR = 2.0                      # the flat back the tape sticks to
POST_GAP = 0.6                   # air between the tallest back part and the floor
POST_D = 7.0                     # posts the board sits on
PILOT_D = 2.7                    # hole for an M3 screw to cut its own thread
RIM = 0.2                        # air between the glass and the surround, so it can't press on the touch layer
FRONT = 2.0                      # thickness of the surround
WINDOW_GAP = 0.8                 # how far the window stands back from the picture, all round
HEAD_D, HEAD_SINK = 6.2, 1.0     # recess for the screw heads
THROUGH_D = 3.4                  # clearance hole for an M3 screw
BOSS_D = 6.0                     # the surround's feet on the PCB: small, to stay clear of the glass
USB_W = 13.5                     # width of the slot for the cable's plug
CORNER = 3.0                     # outside corner radius

POST_H = BACK_PARTS + POST_GAP                 # floor to the back of the PCB
IN_L, IN_W = PCB_L + 2 * FIT, PCB_W + 2 * FIT
OUT_L, OUT_W = IN_L + 2 * WALL, IN_W + 2 * WALL
HEIGHT = FLOOR + POST_H + STACK + RIM          # the surround sits on top of this


def rounded_box(length, width, height, radius):
    """A box with rounded vertical edges, centred on x and y, standing on z = 0."""
    corners = [m3.Manifold.cylinder(height, radius, radius, 48).translate((sx * (length / 2 - radius),
                                                                           sy * (width / 2 - radius), 0))
               for sx in (-1, 1) for sy in (-1, 1)]
    return m3.Manifold.batch_hull(corners)


def box(length, width, height):
    return m3.Manifold.cube((length, width, height), True).translate((0, 0, height / 2))


def shell():
    """The back shell, USB end at -x."""
    body = rounded_box(OUT_L, OUT_W, HEIGHT, CORNER)
    body -= box(IN_L, IN_W, HEIGHT).translate((0, 0, FLOOR))            # the pocket
    # Posts under the four mounting holes, joined to the nearest walls so they can't snap off.
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * HOLE_L / 2, sy * HOLE_W / 2
            post = m3.Manifold.cylinder(POST_H, POST_D / 2, POST_D / 2, 48).translate((x, y, FLOOR))
            web_x = box(IN_L / 2 - abs(x), POST_D, POST_H).translate((sx * (abs(x) + (IN_L / 2 - abs(x)) / 2), y, FLOOR))
            web_y = box(POST_D, IN_W / 2 - abs(y), POST_H).translate((x, sy * (abs(y) + (IN_W / 2 - abs(y)) / 2), FLOOR))
            body += post + web_x + web_y
            body -= m3.Manifold.cylinder(POST_H + 1, PILOT_D / 2, PILOT_D / 2, 32).translate((x, y, FLOOR + 0.8))
    # USB-C slot in the short wall at -x, open to the top so the board (whose socket may
    # overhang its edge) can still drop straight in.
    y = PCB_W / 2 - USB_OFFSET
    # The socket hangs below the PCB's back face and a plug's body is about 7 mm tall, so
    # the slot runs from the floor up.
    slot = box(WALL + 2, USB_W, HEIGHT).translate((-(IN_L / 2 + WALL / 2), y, FLOOR))
    return body - slot


def surround():
    """The front frame, drawn face-down as it prints: outer face on z = 0, USB end at -x."""
    plate = rounded_box(OUT_L, OUT_W, FRONT, CORNER)
    # Window over the picture. From the front the USB end is on the left; this part is
    # drawn face-down, which mirrors it across the long axis only, so x is unchanged.
    cx = -PCB_L / 2 + PICTURE_FROM_USB_END + PICTURE_L / 2
    cy = -(-PCB_W / 2 + PICTURE_FROM_EDGE + PICTURE_W / 2)
    window = box(PICTURE_L + 2 * WINDOW_GAP, PICTURE_W + 2 * WINDOW_GAP, FRONT + 2).translate((cx, cy, -1))
    # Chamfer the window on the outer face so the frame doesn't shadow the picture.
    lip = m3.Manifold.batch_hull([
        box(PICTURE_L + 2 * WINDOW_GAP + 2.4, PICTURE_W + 2 * WINDOW_GAP + 2.4, 0.01).translate((cx, cy, 0)),
        box(PICTURE_L + 2 * WINDOW_GAP, PICTURE_W + 2 * WINDOW_GAP, 0.01).translate((cx, cy, 1.2))])
    part = plate - window - lip
    # Bosses that reach down past the glass to the PCB at the four holes, and clamp it.
    boss_h = STACK - PCB_T + RIM
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * HOLE_L / 2, sy * HOLE_W / 2
            part += m3.Manifold.cylinder(boss_h, BOSS_D / 2, BOSS_D / 2, 48).translate((x, y, FRONT))
            part -= m3.Manifold.cylinder(FRONT + boss_h + 2, THROUGH_D / 2, THROUGH_D / 2, 32).translate((x, y, -1))
            part -= m3.Manifold.cylinder(HEAD_SINK + 1, HEAD_D / 2, HEAD_D / 2, 32).translate((x, y, -1))
    return part


def save(solid, name):
    mesh = solid.to_mesh()
    tm = trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts))
    assert tm.is_watertight, name
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    tm.export(path)
    print("%-34s %5.1f x %5.1f x %4.1f mm, %.1f cm3" % (name, *tm.extents, tm.volume / 1000))
    return tm


if __name__ == "__main__":
    save(shell(), "esp32-32e-case-back.stl")
    save(surround(), "esp32-32e-case-front.stl")
    # A 3 mm slice through the shell's posts and walls: prints in minutes and proves the
    # outline, the hole spacing and the USB position before committing to the full shell.
    cut = shell() ^ box(OUT_L + 2, OUT_W + 2, 3.0).translate((0, 0, FLOOR + POST_H - 3.0))
    save(cut.translate((0, 0, -(FLOOR + POST_H - 3.0))), "esp32-32e-fit-test.stl")

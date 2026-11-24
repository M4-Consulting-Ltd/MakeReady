#!/usr/bin/env python3
"""Printable case for the LCDWiki 2.8inch ESP32-32E display board (E32R28T).

A one-piece back shell: the board drops in screen-up and is held by four M3 screws
through its own mounting holes into posts. The back is flat, for 3M tape. Nothing is
opened up except a slot for the USB-C plug.

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
USB_OFFSET = 20.3                # centre of the USB-C socket from the nearer long edge

# ---- The case ---------------------------------------------------------------------------
FIT = 0.3                        # gap between board and wall, each side
WALL = 2.0                       # side walls
FLOOR = 2.0                      # the flat back the tape sticks to
POST_GAP = 0.6                   # air between the tallest back part and the floor
POST_D = 7.0                     # posts the board sits on
PILOT_D = 2.7                    # hole for an M3 screw to cut its own thread
RIM = 0.4                        # how far the wall stands above the glass, to protect it
USB_W = 13.5                     # width of the slot for the cable's plug
CORNER = 3.0                     # outside corner radius

POST_H = BACK_PARTS + POST_GAP                 # floor to the back of the PCB
IN_L, IN_W = PCB_L + 2 * FIT, PCB_W + 2 * FIT
OUT_L, OUT_W = IN_L + 2 * WALL, IN_W + 2 * WALL
HEIGHT = FLOOR + POST_H + STACK + RIM


def rounded_box(length, width, height, radius):
    """A box with rounded vertical edges, centred on x and y, standing on z = 0."""
    corners = [m3.Manifold.cylinder(height, radius, radius, 48).translate((sx * (length / 2 - radius),
                                                                           sy * (width / 2 - radius), 0))
               for sx in (-1, 1) for sy in (-1, 1)]
    return m3.Manifold.batch_hull(corners)


def box(length, width, height):
    return m3.Manifold.cube((length, width, height), True).translate((0, 0, height / 2))


def shell(usb_near_plus_y):
    """usb_near_plus_y: which long edge the USB socket is nearer, seen with the USB end at -x."""
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
    y = (PCB_W / 2 - USB_OFFSET) * (1 if usb_near_plus_y else -1)
    # The socket hangs below the PCB's back face and a plug's body is about 7 mm tall, so
    # the slot runs from the floor up.
    slot = box(WALL + 2, USB_W, HEIGHT).translate((-(IN_L / 2 + WALL / 2), y, FLOOR))
    return body - slot


def save(solid, name):
    mesh = solid.to_mesh()
    tm = trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts))
    assert tm.is_watertight, name
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    tm.export(path)
    print("%-34s %5.1f x %5.1f x %4.1f mm, %.1f cm3" % (name, *tm.extents, tm.volume / 1000))
    return tm


if __name__ == "__main__":
    # Two shells, mirror images of each other in where the USB slot is. Hold the board
    # screen-up with the USB socket on your left: if the socket is nearer the edge away
    # from you, print "far"; if nearer the edge towards you, print "near".
    save(shell(True), "esp32-32e-case-usb-far.stl")
    save(shell(False), "esp32-32e-case-usb-near.stl")
    # A 3 mm slice through the posts and walls: prints in minutes and proves the outline,
    # the hole spacing and the USB position before committing to the full shell.
    for name, far in (("far", True), ("near", False)):
        cut = shell(far) ^ box(OUT_L + 2, OUT_W + 2, 3.0).translate((0, 0, FLOOR + POST_H - 3.0))
        save(cut.translate((0, 0, -(FLOOR + POST_H - 3.0))), "esp32-32e-fit-test-usb-%s.stl" % name)

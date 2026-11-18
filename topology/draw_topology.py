"""Draws the project topology as docs/topology.png.

    python3 topology/draw_topology.py

Needs matplotlib only (no Mininet). Link parameters, addresses and port numbers
are repeated here by hand - keep them in sync with topology/topo.py.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "topology.png")

# (label, s1 port, s2 port) - keep in sync with UPLINKS in topo.py
UPLINK_A = ("uplink A  —  20 Mb/s, 10 ms one way", 5, 1)
UPLINK_B = ("uplink B  —  5 Mb/s, 2 ms one way", 6, 2)

_available = {f.name for f in font_manager.fontManager.ttflist}
plt.rcParams["font.family"] = next(
    (f for f in ("Inter", "Segoe UI", "DejaVu Sans") if f in _available), "sans-serif")

C_HOST = "#E8F1FB"; C_HOST_E = "#4A78B5"
C_SW = "#FDECC8"; C_SW_E = "#C77F17"
C_SRV = "#EFE6F7"; C_SRV_E = "#7B4FA6"
C_OPT = "#FAFAFA"; C_OPT_E = "#7A7A7A"
C_A = "#1F6FB2"; C_B = "#D9822B"
C_LINK = "#6B6B6B"; C_TXT = "#222222"; C_NOTE = "#555555"
DASH = (0, (4, 3))


def box(ax, x, y, label, sub=None, fc=C_HOST, ec=C_HOST_E, w=1.05, h=0.62, ls="-"):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle="round,pad=0.02,rounding_size=0.12",
                                fc=fc, ec=ec, lw=1.8, ls=ls, zorder=3))
    dy = 0.09 if sub else 0
    ax.text(x, y + dy, label, ha="center", va="center", fontsize=11,
            fontweight="bold", color=C_TXT, zorder=4)
    if sub:
        ax.text(x, y - 0.17, sub, ha="center", va="center", fontsize=7.5, color=C_NOTE, zorder=4)


def link(ax, p, q, color=C_LINK, lw=1.6, ls="-"):
    ax.plot([p[0], q[0]], [p[1], q[1]], color=color, lw=lw, ls=ls, zorder=2, solid_capstyle="round")


def port(ax, x, y, text, **kwargs):
    ax.text(x, y, text, fontsize=7, color=C_NOTE, zorder=4, **kwargs)


def uplinks(ax, s1, s2):
    """Two parallel uplinks A (top) and B (bottom) between s1 and s2."""
    x1, x2 = s1[0] + 0.55, s2[0] - 0.55
    ya, yb = s1[1] + 0.42, s1[1] - 0.42
    link(ax, (s1[0], s1[1] + 0.25), (x1, ya))
    link(ax, (s1[0], s1[1] - 0.25), (x1, yb))
    link(ax, (x2, ya), (s2[0], s2[1] + 0.25))
    link(ax, (x2, yb), (s2[0], s2[1] - 0.25))
    link(ax, (x1, ya), (x2, ya), color=C_A, lw=3.2)
    link(ax, (x1, yb), (x2, yb), color=C_B, lw=3.2)
    xm = (x1 + x2) / 2
    label_bg = dict(fc="white", ec="none", pad=2.0)   # hides the dashed control lines behind
    ax.text(xm, ya + 0.17, UPLINK_A[0], ha="center", va="bottom",
            fontsize=9, color=C_A, fontweight="bold", bbox=label_bg, zorder=5)
    ax.text(xm, yb - 0.17, UPLINK_B[0], ha="center", va="top",
            fontsize=9, color=C_B, fontweight="bold", bbox=label_bg, zorder=5)
    port(ax, x1 + 0.06, ya - 0.07, "port %d" % UPLINK_A[1], ha="left", va="top")
    port(ax, x2 - 0.06, ya - 0.07, "port %d" % UPLINK_A[2], ha="right", va="top")
    port(ax, x1 + 0.06, yb + 0.07, "port %d" % UPLINK_B[1], ha="left", va="bottom")
    port(ax, x2 - 0.06, yb + 0.07, "port %d" % UPLINK_B[2], ha="right", va="bottom")
    ax.text(xm, s1[1], "htb + netem, both directions", ha="center", va="center",
            fontsize=7.5, color="#777", style="italic")


def controller(ax, x, y, targets):
    box(ax, x, y, "Ryu / OS-Ken", sub="controller app", fc="#F3F3F3", ec="#444", w=1.4)
    for t in targets:
        link(ax, (x, y - 0.33), (t[0], t[1] + 0.33), color="#444", ls=(0, (3, 3)), lw=1.2)
    ax.text(x + 0.85, y, "OpenFlow 1.3", ha="left", va="center", fontsize=7.5, color="#444")


fig, ax = plt.subplots(figsize=(11.2, 5.5))
S1, S2 = (3.0, 0.0), (7.2, 0.0)
SRV, GW, NET = (9.0, 0.62), (9.0, -0.62), (10.6, -0.62)
EXT = (0.6, -2.05)
hosts = [(0.6, 1.2, "h1"), (0.6, 0.4, "h2"), (0.6, -0.4, "h3"), (0.6, -1.2, "h4")]

for x, y, _name in hosts:
    link(ax, (x + 0.5, y), (S1[0] - 0.5, S1[1]))
link(ax, (EXT[0] + 0.5, EXT[1]), (S1[0] - 0.5, S1[1]), ls=DASH)
for i, (x, y, name) in enumerate(hosts, start=1):
    box(ax, x, y, name, sub="10.0.0.%d" % i, w=0.95)
box(ax, *EXT, "ext", sub="real device", fc=C_OPT, ec=C_OPT_E, w=0.95, ls=DASH)
port(ax, S1[0] - 1.1, S1[1], "ports 1–4", ha="center", va="center")
port(ax, S1[0] - 0.72, S1[1] - 0.62, "port 7", ha="left", va="top")

link(ax, S2, SRV)
link(ax, S2, GW, ls=DASH)
link(ax, GW, NET, ls=DASH)
port(ax, S2[0] + 0.56, S2[1] + 0.44, "port 3", ha="left", va="bottom")
port(ax, S2[0] + 0.56, S2[1] - 0.48, "port 4", ha="left", va="top")
uplinks(ax, S1, S2)
box(ax, *S1, "s1", sub="edge switch", fc=C_SW, ec=C_SW_E)
box(ax, *S2, "s2", sub="never floods", fc=C_SW, ec=C_SW_E)
box(ax, *SRV, "srv", sub="10.0.0.100", fc=C_SRV, ec=C_SRV_E)
ax.text(SRV[0], SRV[1] + 0.45, "test services", ha="center", va="bottom", fontsize=8, color=C_SRV_E)
box(ax, *GW, "gw", sub="10.0.0.254, NAT", fc=C_OPT, ec=C_OPT_E, ls=DASH)
box(ax, *NET, "Internet", fc=C_OPT, ec=C_OPT_E, w=0.95, h=0.5, ls=DASH)
controller(ax, 5.1, 1.75, [S1, S2])

ax.text(0.1, -2.6, "h1–h4, srv and gw share one subnet, 10.0.0.0/24 — no routing. "
        "s1 picks the uplink per flow; s2 never floods, which breaks the s1–s2 loop.",
        fontsize=8.5, color=C_NOTE, va="top")
ax.text(0.1, -2.9, "Dashed parts are optional: gw (--internet) is a NAT gateway to the real Internet, "
        "ext (--attach) is a real device plugged into s1.",
        fontsize=8.5, color=C_NOTE, va="top")

ax.set_xlim(-0.1, 11.3); ax.set_ylim(-3.2, 2.4)
ax.set_aspect("equal"); ax.axis("off")
ax.set_title("Topology — multi-homed site with two uplinks", fontsize=13,
             fontweight="bold", loc="left", pad=10, color=C_TXT)
fig.tight_layout()
os.makedirs(os.path.dirname(OUT), exist_ok=True)
fig.savefig(OUT, dpi=200, facecolor="white")
print("written", os.path.normpath(OUT))

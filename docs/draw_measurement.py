"""Draws the measurement sequence diagram as docs/measurement.png.

    python3 docs/draw_measurement.py

Needs matplotlib only. The intervals shown are the starting values of README
section 4 - keep them in sync by hand.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Rectangle

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "measurement.png")

_available = {f.name for f in font_manager.fontManager.ttflist}
plt.rcParams["font.family"] = next(
    (f for f in ("Inter", "Segoe UI", "DejaVu Sans") if f in _available), "sans-serif")

C_CTRL = "#444444"      # OpenFlow control channel
C_DATA = "#1F6FB2"      # data plane
C_TXT = "#222222"
C_NOTE_FC = "#FFF8E1"; C_NOTE_EC = "#C9A227"
C_FRAME = "#9AA3AD"

X = {"c": 1.7, "s1": 6.2, "s2": 9.9}
LEFT, RIGHT = 0.2, 11.0


def actor(ax, x, label, sub, fc, ec):
    w, h = 1.9, 0.62
    ax.add_patch(FancyBboxPatch((x - w / 2, -h / 2), w, h,
                                boxstyle="round,pad=0.02,rounding_size=0.12",
                                fc=fc, ec=ec, lw=1.8, zorder=3))
    ax.text(x, 0.09, label, ha="center", va="center", fontsize=11, fontweight="bold",
            color=C_TXT, zorder=4)
    ax.text(x, -0.17, sub, ha="center", va="center", fontsize=7.5, color="#555", zorder=4)


def message(ax, y, src, dst, step, text, color=C_CTRL, reply=False):
    x0, x1 = X[src], X[dst]
    ax.annotate("", xy=(x1, y), xytext=(x0, y), zorder=3,
                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.6,
                                linestyle=(0, (4, 3)) if reply else "-",
                                shrinkA=0, shrinkB=0, mutation_scale=13))
    ax.text((x0 + x1) / 2, y + 0.07, "%d.  %s" % (step, text), ha="center", va="bottom",
            fontsize=8.5, color=color, zorder=4,
            bbox=dict(fc="white", ec="none", pad=1.0))


def note(ax, y, text, x0=LEFT + 0.2, x1=RIGHT - 0.2, h=0.42):
    ax.add_patch(Rectangle((x0, y - h / 2), x1 - x0, h, fc=C_NOTE_FC, ec=C_NOTE_EC,
                           lw=1.0, zorder=3))
    ax.text((x0 + x1) / 2, y, text, ha="center", va="center", fontsize=8.5, color=C_TXT, zorder=4)


def heading(ax, y, text):
    ax.text(LEFT + 0.2, y, text, ha="left", va="center", fontsize=8.5, style="italic",
            color="#4A5560", zorder=4, bbox=dict(fc="white", ec="none", pad=1.5))


def frame(ax, y_top, y_bottom, title):
    ax.add_patch(Rectangle((LEFT, y_bottom), RIGHT - LEFT, y_top - y_bottom, fc="none",
                           ec=C_FRAME, lw=1.2, zorder=1))
    ax.text(LEFT + 0.1, y_top - 0.09, "loop", ha="left", va="top", fontsize=8,
            fontweight="bold", color="white", zorder=4,
            bbox=dict(fc=C_FRAME, ec="none", pad=2.5))
    ax.text(LEFT + 0.75, y_top - 0.09, title, ha="left", va="top", fontsize=8.5,
            fontweight="bold", color="#4A5560", zorder=4,
            bbox=dict(fc="white", ec="none", pad=2.5))


BOTTOM = -15.0
fig, ax = plt.subplots(figsize=(10.4, 13.4))
for x in X.values():
    ax.plot([x, x], [-0.31, BOTTOM + 0.2], color="#888", lw=1.0, ls=(0, (2, 3)), zorder=1)
actor(ax, X["c"], "Controller app", "Ryu / OS-Ken", "#F3F3F3", "#444")
actor(ax, X["s1"], "s1", "edge switch", "#FDECC8", "#C77F17")
actor(ax, X["s2"], "s2", "far end of the uplinks", "#FDECC8", "#C77F17")

# ----------------------------------------------------------- control links
frame(ax, -0.7, -4.15, "control links: every T_ctrl (1 s), independent of the uplink probes")
message(ax, -1.55, "c", "s1", 1, "packet_out: reference frame, output = CONTROLLER")
message(ax, -2.1, "s1", "c", 2, "packet_in: frame handed straight back", reply=True)
message(ax, -2.65, "c", "s2", 3, "packet_out: reference frame, output = CONTROLLER")
message(ax, -3.2, "s2", "c", 4, "packet_in: frame handed straight back", reply=True)
note(ax, -3.75, "RTT_ctrl(s1), RTT_ctrl(s2)   →   moving averages, used below")

# ----------------------------------------------------------------- probing
frame(ax, -4.4, -10.3, "uplink probes: every T_probe, separately for each uplink and direction  "
                       "(2 s … 0.2 s, adaptive)")
heading(ax, -5.0, "direction s1 → s2")
message(ax, -5.6, "c", "s1", 5, "packet_out: probe (seq, t_tx), output = uplink port")
message(ax, -6.15, "s1", "s2", 6, "probe over the uplink (delay, loss)", color=C_DATA)
message(ax, -6.7, "s2", "c", 7, "packet_in: probe arrives at t_rx", reply=True)
heading(ax, -7.2, "direction s2 → s1")
message(ax, -7.8, "c", "s2", 8, "packet_out: probe (seq, t_tx), output = uplink port")
message(ax, -8.35, "s2", "s1", 9, "probe over the uplink (delay, loss)", color=C_DATA)
message(ax, -8.9, "s1", "c", 10, "packet_in: probe arrives at t_rx", reply=True)
note(ax, -9.45, "one-way delay = t_rx − t_tx − ½ (RTT_ctrl(sender) + RTT_ctrl(receiver))"
                "   →   moving average, jitter, loss window")
note(ax, -9.95, "no arrival within the timeout   →   probe counted as lost")

# -------------------------------------------------------------- statistics
frame(ax, -10.55, -13.9, "counters: every T_stats  (4 s … 1 s, adaptive)")
message(ax, -11.4, "c", "s1", 11, "PortStatsRequest, FlowStatsRequest")
message(ax, -11.95, "s1", "c", 12, "byte and packet counters, durations", reply=True)
message(ax, -12.5, "c", "s2", 13, "PortStatsRequest, FlowStatsRequest")
message(ax, -13.05, "s2", "c", 14, "byte and packet counters, durations", reply=True)
note(ax, -13.55, "utilisation and counter-based loss per direction, per-flow rate")

# ---------------------------------------------------------------- outcome
note(ax, -14.25, "state of each uplink direction (calm / loaded / critical)   →   next T_probe and T_stats",
     x0=LEFT, x1=RIGHT)
note(ax, -14.75, "threshold crossed   →   preferred uplink of each class is recomputed (Section 5)",
     x0=LEFT, x1=RIGHT)

ax.plot([LEFT + 0.1, LEFT + 0.6], [0.72, 0.72], color=C_CTRL, lw=1.6)
ax.text(LEFT + 0.7, 0.72, "OpenFlow control channel", va="center", fontsize=8, color=C_CTRL)
ax.plot([LEFT + 3.3, LEFT + 3.8], [0.72, 0.72], color=C_DATA, lw=1.6)
ax.text(LEFT + 3.9, 0.72, "data plane (the measured uplink)", va="center", fontsize=8, color=C_DATA)

ax.set_xlim(0.0, 11.2); ax.set_ylim(BOTTOM, 1.0)
ax.set_aspect("equal"); ax.axis("off")
ax.set_title("Measurement — control links, uplink probes, counters", fontsize=13,
             fontweight="bold", loc="left", pad=6, color=C_TXT)
fig.tight_layout()
fig.savefig(OUT, dpi=170, facecolor="white")
print("written", os.path.normpath(OUT))

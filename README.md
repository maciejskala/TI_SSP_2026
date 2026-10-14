# TI_SSP_2026
# Reactive Multi-Homing Link Selection Based on Active Uplink Quality Measurements

SDN project for the *Sieci Sterowane Programowo* (Software-Defined Networks) course, AGH University of Krakow, winter semester 2026/27.

Controller: **Ryu / OS-Ken** (Python, OpenFlow 1.3)
Emulation: **Mininet** with Open vSwitch

## 1. Goal

Design and implement a reactive controller application for a multi-homed site: a local network connected to the outside world through two uplinks with different characteristics (bandwidth, latency, loss). The controller continuously measures the quality of both uplinks and dynamically decides which uplink each new flow should use. When an uplink degrades or fails, affected traffic is migrated to the other uplink; when it recovers, traffic is rebalanced.

The project falls under the course topic *dynamic routing depending on link load / packet loss / delay*.

## 2. Assumptions and scope

### In scope

- Two uplinks (A and B) from a single edge switch towards a common "Internet" endpoint, emulated as two separate paths in Mininet with distinct `TCLink` parameters.
- Active quality measurement of each uplink performed by the controller itself (probe packets injected with `packet_out`, replies captured with `packet_in`), yielding RTT and packet loss per uplink.
- Passive load measurement via periodic OpenFlow port and flow statistics requests.
- Per-flow classification into two traffic classes:
  - **latency-sensitive** (e.g. VoIP/UDP media, SSH, DNS, ICMP),
  - **bulk** (large TCP transfers and everything else).
- Uplink selection for every new flow based on its class and the current uplink scores.
- Reactive migration of flows on uplink degradation (threshold crossed) or failure (port down / link down event), and rebalancing on recovery.
- Hysteresis in the decision logic to avoid flow flapping between uplinks.
- A read-only live dashboard served by the controller (topology, uplink metrics, flow-to-uplink assignment).

### Out of scope

- NAT or per-uplink public addressing. Both uplinks lead to the same destination network, so flows keep their addresses when migrated and TCP sessions survive the switch. The limitations of migrating stateful sessions across NAT-ed ISP uplinks are discussed in the documentation but not implemented.
- Multi-switch topology discovery, Dijkstra-style path computation and loop handling. The topology is fixed and known to the application.
- Deep packet inspection. Classification uses L3/L4 header fields only.
- Elephant-flow detection — a possible extension if time permits (see Section 8).

### Course requirements this design satisfies

- **Reactive control** — every decision is triggered by a network event: a new flow (`packet_in`), a statistics report, a probe result, or a port/link state change.
- **Integral controller module** — the application is a Ryu/OS-Ken `RyuApp`, not an external script using a REST API. The dashboard only reads the application's internal state.
- **Real-time demonstration** on a running virtual network with traffic generators (Section 7).

## 3. Topologies
 
Two topologies share the same uplink section and the same controller application. Topology 1 is the development and baseline environment; Topology 2 is the "production-like" scenario used for the final demonstration.
 
### 3.1 Common uplink section
 
```
                      +----------------+
                      |   uplink A     |
            ┌────┐    |  (20 Mb/s,     |    ┌────┐
   LAN ─────│ s1 │────┤   10 ms)       ├────│ s2 │──── srv ("Internet")
            └────┘    |   uplink B     |    └────┘
                      |  (5 Mb/s,      |
                      |   2 ms)        |
                      +----------------+
```
 
- `s1` — edge switch (OpenFlow 1.3, controlled by the application). All decisions are made here.
- `s2` — aggregation switch on the "Internet" side, also controlled by the application but with a fixed, static set of rules (see loop handling below).
- `srv` — destination for all external traffic (runs `iperf` server, HTTP server, probe responder).
- Both uplinks are plain L2 paths to the same destination network. There is no NAT and no separate ISP router per uplink, so a flow keeps its addresses when migrated between uplinks.
- Uplink parameters are set with Mininet `TCLink` and can be changed at runtime with `tc qdisc ... netem` to emulate degradation (added delay, loss, bandwidth reduction).
Exact bandwidth and delay values are a starting point and will be tuned so that a single bulk flow saturates uplink B but not uplink A.
 
**Loop handling.** Two parallel links between `s1` and `s2` form an L2 loop. To avoid broadcast storms:
 
- `s2` never floods. It carries three static rules installed by the controller at connection time: uplink A port → `srv` port, uplink B port → `srv` port, and `srv` port → the uplink currently designated as *primary* by the controller (updated on failover).
- `s1` sends broadcast frames (ARP) towards the Internet side on the primary uplink only; towards the LAN side it floods normally.
- No spanning-tree protocol is used; this is a deliberate design assumption of the project.
### 3.2 Topology 1 — flat LAN (baseline)
 
```
   h1 ──┐
   h2 ──┤   ┌────┐
   h3 ──┼───│ s1 │════ uplinks A/B ════ s2 ── srv
   h4 ──┘   └────┘
```
 
- `h1..h4` — hosts in a single subnet `10.0.0.0/24`, directly attached to `s1`.
- `srv` is in the same subnet; no routing is involved anywhere.
- Used for development, unit testing of the decision logic and the first part of the demonstration.
### 3.3 Topology 2 — routed site behind the multi-homed edge
 
```
 10.1.0.0/24 ── r1 ──┐
 (h1, h2)            │  10.0.0.0/24
                     ├──────── s1 ════ uplinks A/B ════ s2 ── srv
 10.2.0.0/24 ── r2 ──┤
 (h3, h4)            │
                     │
 10.3.0.0/24 ── r3 ──┘      r1, r2, r3 are also interconnected (OSPF)
 (h5)
```
 
- `r1..r3` — Linux routers (Mininet hosts with `ip_forward=1`) running **FRR with OSPF** between themselves, each serving one LAN subnet. OSPF gives the internal network its own convergence, independent of the controller.
- The upstream interfaces of `r1..r3` and `srv` share the segment `10.0.0.0/24` on the LAN side of `s1`. Routers use `srv` as their default gateway; `srv` has static routes back to `10.x.0.0/24` via the corresponding router.
- `s1` aggregates traffic from all internal subnets and applies the same per-flow uplink selection as in Topology 1.
**Impact on the controller.** The decision logic is unchanged — classification already uses L3/L4 fields, so the fact that frames arrive with a router's source MAC is irrelevant. The only addition is a *prefix → LAN port* mapping for return traffic from the uplinks, learned from `packet_in` (source IP and ingress port) or configured statically. Optionally, a per-site policy can be expressed in the configuration (e.g. subnet `10.3.0.0/24` — guest network — is always placed on the cheaper uplink B unless it is down).
 
**What Topology 2 demonstrates.**
 
- Flows from different subnets are distributed across uplinks by class, not by origin — the decision is genuinely per-flow.
- Two independent layers of reactivity: an internal link failure (`r1–r2` down) is handled by OSPF, while uplink degradation is handled by the controller; neither interferes with the other.
- The edge is transparent to the internal routing domain; adding a fourth router or subnet requires no controller change.
**Fallback.** If running FRR inside Mininet namespaces proves time-consuming, Topology 2 is reduced to static routing on `r1..r3`. The controller-side behaviour and the demonstration value are the same; only the internal-convergence scenario is dropped.
 
## 4. Measurement
 
### Active probing
 
Every `T_probe` (default 1 s) the controller sends a probe packet through each uplink port using `packet_out`. Probes carry a timestamp and sequence number. The remote side (or a flow rule on `s2`) sends them back; the controller matches the reply in `packet_in` and computes:
 
- RTT (exponentially weighted moving average and last sample),
- loss ratio over a sliding window of the last `N` probes.
### Passive statistics
 
Every `T_stats` (default 2 s) the controller issues `OFPPortStatsRequest` and `OFPFlowStatsRequest` to `s1` and derives:
 
- per-uplink utilisation (bytes/s relative to the configured link rate),
- per-flow throughput (used for classification refinement and the optional elephant-flow extension).
### Events
 
- `OFPPortStatus` / port down → immediate failover of all flows on that uplink.
- Probe loss over the window above a hard threshold → uplink declared *down* even if the port is up (silent failure).

## 5. Decision algorithm

For each uplink `u` a score is computed:

```
score(u) = w_rtt * norm(rtt_u) + w_loss * norm(loss_u) + w_util * norm(util_u)
```

where `norm()` scales each metric to `[0, 1]` against configured maxima, and the weights depend on the traffic class:

| Class             | w_rtt | w_loss | w_util |
|-------------------|-------|--------|--------|
| latency-sensitive | high  | high   | low    |
| bulk              | low   | medium | high   |

Lower score is better.

**New flow:** on `packet_in` the flow is classified by L3/L4 fields, the uplink with the lower class-specific score is chosen, and a pair of flow rules (forward and return direction) with `idle_timeout` is installed on `s1`.

**Migration:** a flow is moved to the other uplink only if the score difference exceeds a hysteresis margin `H` for at least `K` consecutive evaluations, or immediately on failure. Migration is done by modifying the flow's output port (`OFPFC_MODIFY`), so the flow is not interrupted.

**Recovery:** when a previously failed uplink becomes healthy again (probes succeed for `M` consecutive intervals), new flows may be assigned to it; existing flows are rebalanced gradually, bulk flows first.

All thresholds and weights are configurable from a single YAML/JSON file.

## 6. Dashboard

A lightweight web page served by the controller process (Ryu `WSGIApplication`) and refreshed every second from a JSON endpoint exposing the application's in-memory state. It shows:

- both uplinks with current RTT, loss, utilisation and status (healthy / degraded / down),
- time-series charts of RTT and utilisation per uplink,
- the list of active flows with class, assigned uplink and throughput,
- a log of recent decisions (assignments, migrations, failovers).

The dashboard is read-only and contains no control logic.

## 7. Demonstration scenario

Traffic generators: `iperf`/`iperf3` (TCP bulk, UDP constant-bitrate "VoIP"), `ping`, `curl` against an HTTP server on `srv`, `hping3` if needed.

1. **Steady state.** Start a UDP constant-bitrate stream (latency-sensitive) and a bulk TCP transfer. Show that the UDP stream is placed on the low-latency uplink B and the TCP transfer on the high-bandwidth uplink A.
2. **Degradation.** Inject delay and loss on uplink B with `netem`. Within a few seconds the UDP stream migrates to A; the bulk transfer stays on A because bandwidth is still available. Show the jitter/loss report of the UDP stream before and after.
3. **Failure.** Bring uplink A down. All flows fail over to B; the bulk transfer slows down but continues. Bring A back up and show the flows being rebalanced.
4. **Control run.** Repeat scenario 2 with the decision logic disabled (static assignment) to show the difference.

Each step is visible both in terminals and on the dashboard.

## 8. Possible extensions

- Elephant-flow detection from flow statistics and pinning of elephants to the high-bandwidth uplink regardless of class.
- A third uplink to show that the scoring generalises beyond two links.
- Per-class bandwidth guarantees using OpenFlow meters.

## 9. Milestones

Aligned with the course schedule:

| Milestone                                 | Deliverable                                          |
|-------------------------------------------|------------------------------------------------------|
| Topic and literature, Git repository      | this README, short literature list                   |
| Topology                                  | Mininet script, parameters, `netem` helper scripts   |
| Applications / traffic generators         | generator scripts, probe responder on `srv`          |
| Algorithm                                 | controller application, configuration, dashboard     |
| Presentation (21.01.2027)                 | live demo, technical documentation, slides           |

## 10. Repository layout (planned)

```
.
├── controller/        # Ryu/OS-Ken application (main app, probing, stats, decision, dashboard)
├── topology/          # Mininet topology and runtime link manipulation scripts
├── traffic/           # traffic generator and probe responder scripts
├── dashboard/         # static HTML/JS for the live view
├── config/            # thresholds, weights, link parameters
├── docs/              # technical documentation, measurements, screenshots
└── README.md
```

## 11. Environment

- Ubuntu 22.04 (VM or container)
- Mininet 2.3.x, Open vSwitch 2.17+
- Python 3.10+, `os-ken` (or Ryu 4.34 on Python 3.8/3.9)
- `iperf3`, `iproute2` (`tc`), `hping3`

## 12. Team

- Maciej Dyrdał
- Mateusz Purol
- Maciej Skała
- Szymon Wątroba

#!/usr/bin/env python3
"""Mininet topology of the multi-homed site.

    h1 --+                                                +-- srv    test services
    h2 --+             uplink A (20 Mb/s, 10 ms)          |
    h3 --+-- s1 =================================== s2 ---+
    h4 --+   |         uplink B (5 Mb/s, 2 ms)            |
    (ext) ---+                                            +-- (gw)   NAT to the real Internet

Run as root:

    sudo python3 topology/topo.py            # remote controller on 127.0.0.1:6653, then CLI
    sudo python3 topology/topo.py --static   # no controller, static bring-up rules, then CLI
    sudo python3 topology/topo.py --test     # no controller, automated bring-up check, exit 0/1

Optional, combinable with every mode above:

    --internet       add gw (10.0.0.254) on s2: NAT to the real Internet through the
                     machine Mininet runs on; h1..h4 get a default route and DNS
    --attach IFACE   plug an existing interface of this machine into s1 (port 7), so
                     that a device connected to it becomes a client of the LAN

The static rules are a bring-up aid for the topology milestone only. They pin
all traffic to one uplink and never react to anything; uplink selection is the
job of the controller application.

Both switches use fail-mode "secure": without a controller (and without
--static) they forward nothing. This is intentional - the two parallel s1-s2
links form an L2 loop, so a flooding switch would cause a broadcast storm.
"""

import argparse
import json
import os
import re
import sys
import time
from functools import partial

from mininet.cli import CLI
from mininet.link import Intf, TCIntf, TCLink
from mininet.log import error, info, output, setLogLevel, warn
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.nodelib import NAT
from mininet.topo import Topo

# ---------------------------------------------------------------------------
# Topology parameters - single source of truth, mirrored in README section 3.
# ---------------------------------------------------------------------------
N_HOSTS = 4
SUBNET = '10.0.0.0/24'
HOST_IP = '10.0.0.%d/24'            # h1..h4 -> 10.0.0.1 .. 10.0.0.4
HOST_MAC = '00:00:00:00:00:%02x'    # h1..h4 -> ..:01 .. ..:04
SRV_IP = '10.0.0.100/24'
SRV_MAC = '00:00:00:00:01:00'
GW_IP = '10.0.0.254/24'             # only with --internet
GW_MAC = '00:00:00:00:02:00'
BROADCAST = 'ff:ff:ff:ff:ff:ff'

S1_DPID = '0000000000000001'
S2_DPID = '0000000000000002'
S1_LAN_PORTS = list(range(1, N_HOSTS + 1))    # s1 port i <-> h<i>
S1_UPLINK_PORT = {'A': 5, 'B': 6}
S1_EXT_PORT = 7                               # only with --attach
S2_UPLINK_PORT = {'A': 1, 'B': 2}
S2_SRV_PORT = 3
S2_GW_PORT = 4                                # only with --internet

DEFAULT_DNS = '1.1.1.1'
RESOLV_FILE = '/tmp/multihoming-resolv.conf'

# bw in Mb/s; delay and loss (in %) apply to each direction separately, so the
# round-trip time over an uplink is 2 x delay. queue is the netem limit in
# packets. Starting values, to be tuned (README section 3).
UPLINKS = {
    'A': dict(bw=20, delay='10ms', loss=0, max_queue_size=100),
    'B': dict(bw=5, delay='2ms', loss=0, max_queue_size=100),
}


class Gateway(NAT):
    """Mininet's NAT node: it lives in the root namespace and masquerades the
    LAN subnet behind the address of the machine Mininet runs on.

    The stock class also appends to /etc/network/interfaces and restarts the
    network service of the machine; that part is skipped here.
    """

    def setManualConfig(self, intf):
        pass


class MultiHomingTopo(Topo):
    """LAN hosts -- s1 == two parallel uplinks == s2 -- srv [and gw]."""

    def build(self, internet=False):
        s1 = self.addSwitch('s1', dpid=S1_DPID)
        s2 = self.addSwitch('s2', dpid=S2_DPID)
        for i in S1_LAN_PORTS:
            host = self.addHost('h%d' % i, ip=HOST_IP % i, mac=HOST_MAC % i)
            self.addLink(host, s1, port2=i)
        for name in sorted(UPLINKS):
            self.addLink(s1, s2, port1=S1_UPLINK_PORT[name],
                         port2=S2_UPLINK_PORT[name], **UPLINKS[name])
        srv = self.addHost('srv', ip=SRV_IP, mac=SRV_MAC)
        self.addLink(srv, s2, port2=S2_SRV_PORT)
        if internet:
            gw = self.addHost('gw', cls=Gateway, inNamespace=False,
                              ip=GW_IP, mac=GW_MAC, subnet=SUBNET)
            self.addLink(gw, s2, port2=S2_GW_PORT)


class ShapedIntf(TCIntf):
    """TCIntf with an explicit htb quantum.

    Without it tc prints "quantum of class 50001 is big" for every rate above
    16 Mb/s. The quantum only matters when several classes share a link.
    """

    def bwCmds(self, **kwargs):
        cmds, parent = TCIntf.bwCmds(self, **kwargs)
        return [c + ' quantum 1514' if ' htb rate ' in c else c for c in cmds], parent


class ShapedLink(TCLink):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('cls1', ShapedIntf)
        kwargs.setdefault('cls2', ShapedIntf)
        TCLink.__init__(self, *args, **kwargs)


def build_net(controller_ip, controller_port, with_controller, internet=False, attach=None):
    switch = partial(OVSSwitch, protocols='OpenFlow13', failMode='secure')
    net = Mininet(topo=MultiHomingTopo(internet=internet), switch=switch, link=ShapedLink,
                  controller=None, autoSetMacs=False, autoStaticArp=False)
    if attach:
        # An interface that already exists on this machine becomes port 7 of s1.
        Intf(attach, node=net['s1'], port=S1_EXT_PORT)
    if with_controller:
        net.addController('c0', controller=RemoteController,
                          ip=controller_ip, port=controller_port)
    return net


def disable_ipv6(net):
    """Keep IPv6 neighbour discovery / MLD noise out of the data plane.

    Only interfaces created by this script are touched: gw sits in the root
    namespace, and an attached interface belongs to the machine.
    """
    for host in net.hosts:
        if host.inNamespace:
            host.cmd('sysctl -qw net.ipv6.conf.all.disable_ipv6=1 '
                     'net.ipv6.conf.default.disable_ipv6=1')
        else:
            for intf in host.intfList():
                if intf.name.startswith(host.name + '-'):
                    host.cmd('sysctl -qw net.ipv6.conf.%s.disable_ipv6=1' % intf.name)
    for switch in net.switches:
        for intf in switch.intfList():
            if intf.name.startswith(switch.name + '-'):
                switch.cmd('sysctl -qw net.ipv6.conf.%s.disable_ipv6=1' % intf.name)


def enable_internet(net, dns):
    """Default route and DNS for the LAN hosts; gw itself is set up by Mininet."""
    with open(RESOLV_FILE, 'w') as handle:
        handle.write('nameserver %s\n' % dns)
    gw_ip = GW_IP.split('/')[0]
    for i in S1_LAN_PORTS:
        host = net['h%d' % i]
        host.cmd('ip route replace default via %s' % gw_ip)
        # Every Mininet host has a private mount namespace, so this resolv.conf
        # is seen by the host only; the machine keeps its own.
        host.cmd('mount --bind %s /etc/resolv.conf' % RESOLV_FILE)


def delay_ms(delay):
    """'10ms' -> 10.0, '500us' -> 0.5, '1s' -> 1000.0"""
    match = re.fullmatch(r'\s*([\d.]+)\s*(us|ms|s)\s*', str(delay))
    if not match:
        raise ValueError('bad delay %r (use e.g. 10ms)' % (delay,))
    return float(match.group(1)) * {'us': 0.001, 'ms': 1.0, 's': 1000.0}[match.group(2)]


# ---------------------------------------------------------------------------
# Uplink manipulation: shaping, failure, static bring-up rules
# ---------------------------------------------------------------------------
class Uplinks:
    """Runtime control of the two uplinks (used by the CLI and the self-test)."""

    def __init__(self, net, static, internet=False, dns=None, attach=None):
        self.net = net
        self.static = static
        self.internet = internet
        self.dns = dns
        self.attach = attach
        self.primary = None
        self.params = {name: dict(p) for name, p in UPLINKS.items()}
        s1 = net['s1']
        self.links = {name: s1.intfs[port].link for name, port in S1_UPLINK_PORT.items()}

    def name(self, token):
        name = token.strip().upper()
        if name not in self.links:
            raise ValueError('unknown uplink %r (use A or B)' % token)
        return name

    def intfs(self, name):
        link = self.links[name]
        return link.intf1, link.intf2

    # -- link quality -------------------------------------------------------
    def shape(self, name, **changes):
        """Re-apply htb/netem on both ends of an uplink with changed parameters."""
        self.params[name].update(changes)
        self._apply(name)

    def restore(self, name):
        self.params[name] = dict(UPLINKS[name])
        self._apply(name)

    def _apply(self, name):
        info('*** uplink %s: ' % name)
        for intf in self.intfs(name):
            intf.config(**self.params[name])
        info('\n')

    # -- link state ---------------------------------------------------------
    def set_state(self, name, up):
        for intf in self.intfs(name):
            intf.ifconfig('up' if up else 'down')

    def is_up(self, name):
        return all(intf.isUp() for intf in self.intfs(name))

    # -- static bring-up rules ----------------------------------------------
    def set_primary(self, name):
        """Install static rules that pin all traffic to one uplink.

        s1: unicast by destination MAC; broadcast from the LAN side goes to
            the other LAN ports and to the primary uplink only; broadcast from
            an uplink goes to the LAN side only (never back to an uplink).
        s2: never floods - frames for srv or gw go to their port, everything
            else coming from srv or gw goes to the primary uplink.
        An attached external device has a MAC that is not known in advance, so
        s1 sends every frame with an unknown destination to its port.
        Re-running with the other uplink overwrites the same matches in place.
        """
        if not self.static:
            raise RuntimeError('static rules are only available with --static / --test')
        s1, s2 = self.net['s1'], self.net['s2']
        up1, up2 = S1_UPLINK_PORT[name], S2_UPLINK_PORT[name]
        behind_s2 = {SRV_MAC: S2_SRV_PORT}
        if self.internet:
            behind_s2[GW_MAC] = S2_GW_PORT
        lan_side = S1_LAN_PORTS + ([S1_EXT_PORT] if self.attach else [])
        flows = []

        for i in S1_LAN_PORTS:
            flows.append((s1, 'priority=100,dl_dst=%s,actions=output:%d' % (HOST_MAC % i, i)))
        for mac in behind_s2:
            flows.append((s1, 'priority=100,dl_dst=%s,actions=output:%d' % (mac, up1)))
        for port in lan_side:
            outs = [p for p in lan_side if p != port] + [up1]
            flows.append((s1, 'priority=50,in_port=%d,dl_dst=%s,actions=%s'
                          % (port, BROADCAST, _outputs(outs))))
        for port in S1_UPLINK_PORT.values():
            flows.append((s1, 'priority=50,in_port=%d,dl_dst=%s,actions=%s'
                          % (port, BROADCAST, _outputs(lan_side))))
        if self.attach:
            for port in S1_LAN_PORTS + list(S1_UPLINK_PORT.values()):
                flows.append((s1, 'priority=10,in_port=%d,actions=output:%d' % (port, S1_EXT_PORT)))

        local_ports = list(behind_s2.values())
        for mac, port in behind_s2.items():
            flows.append((s2, 'priority=100,dl_dst=%s,actions=output:%d' % (mac, port)))
        for port in S2_UPLINK_PORT.values():
            flows.append((s2, 'priority=50,in_port=%d,dl_dst=%s,actions=%s'
                          % (port, BROADCAST, _outputs(local_ports))))
        for port in local_ports:
            others = [p for p in local_ports if p != port]
            flows.append((s2, 'priority=50,in_port=%d,dl_dst=%s,actions=%s'
                          % (port, BROADCAST, _outputs([up2] + others))))
            flows.append((s2, 'priority=10,in_port=%d,actions=output:%d' % (port, up2)))

        for switch, flow in flows:
            out = switch.cmd("ovs-ofctl -O OpenFlow13 add-flow %s '%s'" % (switch.name, flow)).strip()
            if out:
                raise RuntimeError('add-flow on %s failed: %s' % (switch.name, out))
        self.primary = name

    # -- reporting ----------------------------------------------------------
    def describe(self):
        lines = []
        for name in sorted(self.links):
            intf1, intf2 = self.intfs(name)
            p = self.params[name]
            line = ('uplink %s: %s <-> %s  %g Mb/s, delay %s, loss %g%%, %s'
                    % (name, intf1.name, intf2.name, p['bw'], p['delay'], p.get('loss') or 0,
                       'up' if self.is_up(name) else 'DOWN'))
            if p.get('jitter'):
                line += ', jitter %s' % p['jitter']
            if self.static and name == self.primary:
                line += '  [primary]'
            lines.append(line)
        if self.internet:
            lines.append('internet: gw %s on s2 port %d, NAT through this machine; '
                         'h1..h%d use it as default route, DNS %s'
                         % (GW_IP.split('/')[0], S2_GW_PORT, N_HOSTS, self.dns))
        if self.attach:
            lines.append('external: %s is s1 port %d (client: an address in %s%s)'
                         % (self.attach, S1_EXT_PORT, SUBNET,
                            ', gateway %s' % GW_IP.split('/')[0] if self.internet else ''))
        return '\n'.join(lines)


def _outputs(ports):
    return ','.join('output:%d' % p for p in ports)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class ProjectCLI(CLI):
    """Mininet CLI extended with uplink commands (type 'help' for the list)."""

    def __init__(self, net, uplinks, **kwargs):
        self.uplinks = uplinks
        CLI.__init__(self, net, **kwargs)

    def _run(self, func, *args):
        try:
            func(*args)
        except (ValueError, RuntimeError, IndexError) as exc:
            error('*** %s\n' % exc)
            return
        output(self.uplinks.describe() + '\n')

    def do_uplinks(self, _line):
        "uplinks - show the current parameters and state of both uplinks"
        output(self.uplinks.describe() + '\n')

    def do_uplink(self, line):
        "uplink A|B - (--static only) pin all traffic to the given uplink"
        self._run(lambda: self.uplinks.set_primary(self.uplinks.name(line)))

    def do_degrade(self, line):
        """degrade A|B [delay=50ms] [loss=5] [bw=2] [jitter=5ms] [queue=100]
        Change the quality of an uplink at runtime (both directions).
        loss in %, bw in Mb/s, queue in packets. Example: degrade B delay=80ms loss=3"""
        def apply():
            tokens = line.split()
            name = self.uplinks.name(tokens[0])
            changes = {}
            for token in tokens[1:]:
                key, _, value = token.partition('=')
                if key == 'delay':
                    delay_ms(value)
                    changes['delay'] = value
                elif key == 'jitter':
                    delay_ms(value)
                    changes['jitter'] = value
                elif key == 'loss':
                    changes['loss'] = float(value)
                elif key == 'bw':
                    changes['bw'] = float(value)
                elif key == 'queue':
                    changes['max_queue_size'] = int(value)
                else:
                    raise ValueError('unknown parameter %r' % token)
            if not changes:
                raise ValueError('nothing to change, e.g.: degrade B delay=80ms loss=3')
            self.uplinks.shape(name, **changes)
        self._run(apply)

    def do_restore(self, line):
        "restore A|B|all - set an uplink back to its default parameters"
        def apply():
            names = sorted(UPLINKS) if line.strip().lower() == 'all' else [self.uplinks.name(line)]
            for name in names:
                self.uplinks.restore(name)
        self._run(apply)

    def do_fail(self, line):
        "fail A|B - bring an uplink down (both ends), switches report port down"
        self._run(lambda: self.uplinks.set_state(self.uplinks.name(line), False))

    def do_recover(self, line):
        "recover A|B - bring an uplink back up"
        self._run(lambda: self.uplinks.set_state(self.uplinks.name(line), True))


# ---------------------------------------------------------------------------
# Automated bring-up check (--test)
# ---------------------------------------------------------------------------
def ping_stats(src, dst_ip, count=10):
    """Return (loss in %, average RTT in ms or None)."""
    out = src.cmd('ping -c %d -i 0.2 -W 1 %s' % (count, dst_ip))
    loss = re.search(r'([\d.]+)% packet loss', out)
    rtt = re.search(r'= [\d.]+/([\d.]+)/', out)
    return (float(loss.group(1)) if loss else 100.0,
            float(rtt.group(1)) if rtt else None)


def tcp_mbps(client, server, seconds=5):
    """TCP throughput client -> server in Mb/s measured with iperf3, or None."""
    if not server.cmd('which iperf3').strip():
        return None
    proc = server.popen('iperf3 -s -1')
    time.sleep(0.5)
    try:
        out = client.cmd('iperf3 -c %s -t %d -J' % (server.IP(), seconds))
        report = json.loads(out[out.index('{'):])
        return report['end']['sum_received']['bits_per_second'] / 1e6
    except (ValueError, KeyError):
        return None
    finally:
        proc.terminate()
        proc.wait()


def self_test(net, uplinks):
    h1, srv = net['h1'], net['srv']
    srv_ip = srv.IP()
    results = []

    def check(ok, text):
        results.append(bool(ok))
        output('  [%s] %s\n' % (' OK ' if ok else 'FAIL', text))

    def rtt_check(label, expected):
        # Wide enough for timer and clock inaccuracy of a VM (several percent
        # under WSL2), tight enough to catch a delay that is missing or applied
        # in one direction only.
        loss, rtt = ping_stats(h1, srv_ip)
        ok = loss == 0 and rtt is not None and 0.85 * expected - 0.5 <= rtt <= 1.3 * expected + 3.0
        check(ok, '%s: h1 -> srv RTT %s ms, loss %g%% (expected about %g ms)'
              % (label, '%.1f' % rtt if rtt is not None else 'n/a', loss, expected))

    output('*** Bring-up check (static rules, no controller)\n')
    nodes = ', '.join(host.name for host in net.hosts)
    for name in sorted(UPLINKS):
        params = UPLINKS[name]
        label = 'uplink %s' % name
        uplinks.set_primary(name)
        loss = net.pingAll(timeout=1)
        check(loss == 0, '%s: pingall between %s, %g%% loss' % (label, nodes, loss))
        rtt_check(label, 2 * delay_ms(params['delay']))
        mbps = tcp_mbps(h1, srv)
        if mbps is None:
            output('  [SKIP] %s: iperf3 not available, throughput not measured\n' % label)
        else:
            check(0.7 * params['bw'] <= mbps <= 1.15 * params['bw'],
                  '%s: h1 -> srv TCP throughput %.1f Mb/s (configured %g Mb/s)'
                  % (label, mbps, params['bw']))

    # Runtime degradation and restore (traffic is on uplink B at this point).
    uplinks.shape('B', delay='50ms')
    rtt_check('uplink B degraded to 50 ms', 100.0)
    uplinks.restore('B')
    rtt_check('uplink B restored', 2 * delay_ms(UPLINKS['B']['delay']))

    # Failure of one uplink leaves the other one usable.
    uplinks.set_state('B', False)
    loss, _ = ping_stats(h1, srv_ip, count=3)
    check(loss == 100, 'uplink B down: traffic pinned to B is lost (%g%% loss)' % loss)
    uplinks.set_primary('A')
    loss, _ = ping_stats(h1, srv_ip, count=5)
    check(loss == 0, 'uplink B down: traffic moved to A gets through (%g%% loss)' % loss)
    uplinks.set_state('B', True)
    check(uplinks.is_up('B'), 'uplink B recovered')

    if uplinks.internet:
        internet_test(h1, uplinks, check)

    passed = all(results)
    output('*** RESULT: %s (%d/%d checks passed)\n'
           % ('PASS' if passed else 'FAIL', sum(results), len(results)))
    return passed


def internet_test(h1, uplinks, check):
    """Real Internet through gw: reachability over each uplink, DNS, HTTPS."""
    target = uplinks.dns
    rtts = {}
    for name in sorted(UPLINKS):
        uplinks.set_primary(name)
        loss, rtt = ping_stats(h1, target, count=5)
        rtts[name] = rtt
        check(loss < 100 and rtt is not None,
              'Internet over uplink %s: h1 -> %s RTT %s ms, loss %g%%'
              % (name, target, '%.1f' % rtt if rtt is not None else 'n/a', loss))
    if rtts['A'] is not None and rtts['B'] is not None:
        # The Internet part of the path is the same, so the difference between
        # the two readings is the difference between the uplinks.
        expected = 2 * (delay_ms(UPLINKS['A']['delay']) - delay_ms(UPLINKS['B']['delay']))
        diff = rtts['A'] - rtts['B']
        check(0.5 * expected <= diff <= 1.5 * expected + 5.0,
              'Internet traffic follows the pinned uplink: RTT over A minus RTT over B '
              '= %.1f ms (expected about %g ms)' % (diff, expected))
    answer = h1.cmd('getent ahostsv4 example.com').split()
    check(bool(answer), 'DNS from h1 through %s: example.com -> %s'
          % (target, answer[0] if answer else 'no answer'))
    if h1.cmd('which curl').strip():
        code = h1.cmd('curl -s -o /dev/null -m 10 -w "%{http_code}" https://example.com').strip()
        check(code[:1] in ('2', '3'), 'HTTPS from h1: https://example.com -> HTTP %s' % (code or 'no reply'))
    else:
        output('  [SKIP] curl not available, HTTPS not checked\n')


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='Multi-homed site topology (Mininet)')
    parser.add_argument('--static', action='store_true',
                        help='no controller: install static bring-up rules and open the CLI')
    parser.add_argument('--test', action='store_true',
                        help='no controller: run the automated bring-up check and exit')
    parser.add_argument('--internet', action='store_true',
                        help='add gw on s2: NAT to the real Internet through this machine')
    parser.add_argument('--dns', default=DEFAULT_DNS,
                        help='DNS server for h1..h4 with --internet (default %s)' % DEFAULT_DNS)
    parser.add_argument('--attach', metavar='IFACE',
                        help='plug an existing interface of this machine into s1 (port %d)' % S1_EXT_PORT)
    parser.add_argument('--controller-ip', default='127.0.0.1')
    parser.add_argument('--controller-port', type=int, default=6653)
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit('Mininet needs root: sudo python3 %s' % ' '.join(sys.argv))
    if args.attach and not os.path.exists('/sys/class/net/%s' % args.attach):
        sys.exit('--attach: no such interface: %s' % args.attach)

    static = args.static or args.test
    setLogLevel('info')
    net = build_net(args.controller_ip, args.controller_port, with_controller=not static,
                    internet=args.internet, attach=args.attach)
    passed = True
    try:
        net.start()
        disable_ipv6(net)
        if args.internet:
            enable_internet(net, args.dns)
        uplinks = Uplinks(net, static, internet=args.internet, dns=args.dns, attach=args.attach)
        if args.test:
            passed = self_test(net, uplinks)
        else:
            if static:
                uplinks.set_primary('A')
                info('*** Static bring-up rules installed, no controller in use\n')
            elif net.waitConnected(timeout=5):
                info('*** s1 and s2 are connected to the controller at %s:%d\n'
                     % (args.controller_ip, args.controller_port))
            else:
                warn('*** No controller at %s:%d yet - the switches forward nothing until '
                     'the controller application connects (fail-mode secure).\n'
                     % (args.controller_ip, args.controller_port))
            output(uplinks.describe() + '\n')
            output('*** Extra commands: uplinks, degrade, restore, fail, recover%s (see help)\n'
                   % (', uplink' if static else ''))
            ProjectCLI(net, uplinks)
    finally:
        net.stop()
        if args.internet and os.path.exists(RESOLV_FILE):
            os.remove(RESOLV_FILE)
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())

# SPDX-License-Identifier: GPL-2.0-only
"""Check nss_dsa_conduit/nss_dsa_fw_mask against a fake sysfs.

The helpers read nothing but /sys/class/net, so each case here builds that
tree in a tempdir and the sourced copy is rewritten to look there. Covered:
a two-CPU-port board with the wan port on the second CPU port (Redmi
AX5400, CMCC PZ-L8, Cudy P5 from board.d, and a CR8806 whose wan conduit
was moved by hand) - the firmware needs both GMACs; the same board with
every user port on one conduit - the idle CPU port stays out; a GMAC with
a WAN PHY of its own, up and down; and a switchless board.
"""
from pathlib import Path
import os
import subprocess
import tempfile

tree = Path(__file__).resolve().parents[4]
shell = os.environ.get('NSS_TEST_SH', 'sh')
source = (tree / 'package/nss/nss-tools/files/functions.sh').read_text(encoding='utf-8')

GMAC_NODE = {0: '39c00000.ethernet', 1: '39d00000.ethernet'}

# name -> gmac index, is a DSA CPU port, admin up, its user ports
CPU0_WAN = {
    'eth0': (0, True, True, ['wan']),
    'eth1': (1, True, True, ['lan1', 'lan2', 'lan3']),
}
ONE_CONDUIT = {
    'eth0': (0, True, True, []),
    'eth1': (1, True, True, ['lan1', 'lan2', 'lan3', 'wan']),
}
OWN_PHY_UP = {
    'eth0': (0, False, True, []),
    'eth1': (1, True, True, ['lan1', 'lan2', 'lan3']),
}
OWN_PHY_DOWN = {
    'eth0': (0, False, False, []),
    'eth1': (1, True, True, ['lan1', 'lan2', 'lan3']),
}
SWITCHLESS = {'eth0': (0, False, True, [])}

# name, board, expected fw_mask, expected conduit, expected user ports
cases = [
    ('wan on the second CPU port', CPU0_WAN, '0x3', 'eth1', 'lan1 lan2 lan3'),
    ('every user port on one conduit', ONE_CONDUIT, '0x2', 'eth1', 'lan1 lan2 lan3 wan'),
    ('own WAN PHY, up', OWN_PHY_UP, '0x3', 'eth1', 'lan1 lan2 lan3'),
    ('own WAN PHY, down', OWN_PHY_DOWN, '0x2', 'eth1', 'lan1 lan2 lan3'),
    ('switchless', SWITCHLESS, '0x1', '', ''),
]


def build(root, board):
    """Write the fake /sys/class/net for one board."""
    net = root / 'sys/class/net'
    net.mkdir(parents=True)
    for name, (index, is_cpu, up, ports) in board.items():
        d = net / name
        d.mkdir()
        # nss_gmac_index() readlinks device/ and matches the DT node name.
        (root / 'devices').mkdir(exist_ok=True)
        node = root / 'devices' / GMAC_NODE[index]
        node.mkdir(exist_ok=True)
        os.symlink(os.path.relpath(node, d), d / 'device')
        (d / 'flags').write_text('0x%x\n' % (0x1000 | (0x3 if up else 0x2)))
        if is_cpu:
            (d / 'dsa').mkdir()
        for p in ports:
            u = net / p
            u.mkdir(exist_ok=True)
            # A user port is an upper of its conduit and has a switch id.
            (u / 'phys_switch_id').write_text('1a2b3c4d5e6f\n')
            (u / 'device').mkdir(exist_ok=True)
            drv = root / 'devices' / 'qca8k'
            drv.mkdir(exist_ok=True)
            os.symlink(os.path.relpath(drv, u / 'device'), u / 'device' / 'driver')
            (d / ('upper_' + p)).touch()


failures = []
for name, board, mask, conduit, ports in cases:
    with tempfile.TemporaryDirectory(prefix='dsa-fw-mask-') as directory:
        root = Path(directory)
        build(root, board)
        # set -e, as nss-dwmac-up sources these: a helper that trips it fails here.
        script = 'set -e\n' + source.replace('/sys/class/net', str(root / 'sys/class/net')) + '''
nss_dsa_fw_mask
# One key per line: the values hold spaces.
echo "mask=$NSS_FW_MASK"
echo "conduit=$NSS_CONDUIT"
echo "if=$NSS_CONDUIT_IF"
echo "ports=$NSS_USER_PORTS"
echo "driver=$NSS_SWITCH_DRIVER"
echo "tagger=$(nss_dsa_tagger "${NSS_CONDUIT:-none}" || echo -)"
'''
        (root / 'run.sh').write_text(script)
        r = subprocess.run([shell, str(root / 'run.sh')], capture_output=True, text=True)
        out = dict(
            line.split('=', 1) for line in r.stdout.splitlines() if '=' in line
        ) if r.returncode == 0 else {}
        want = {'mask': mask, 'conduit': conduit}
        got = {k: out.get(k, '<missing>') for k in want}
        # ports= and the tagger only mean anything when there is a switch.
        if conduit:
            want['driver'], want['tagger'] = 'qca8k', 'qca-8021q'
            got['driver'], got['tagger'] = out.get('driver'), out.get('tagger')
        if r.returncode != 0 or got != want:
            failures.append('%-32s rc=%d want %s got %s%s' % (
                name, r.returncode, want, got,
                '\n    stderr: ' + r.stderr.strip() if r.stderr.strip() else ''))
            continue
        # NSS_USER_PORTS keeps the busiest conduit's ports, space separated.
        reported = ' '.join(sorted(out.get('ports', '').split()))
        if reported != ' '.join(sorted(ports.split())):
            failures.append('%-32s user ports: want %r got %r' % (name, ports, reported))
            continue
        print('ok   %-32s %s conduit %s' % (name, mask, conduit or 'none'))

if failures:
    print('\nFAILED (%s):' % shell)
    for f in failures:
        print('  ' + f)
    raise SystemExit(1)
print('\nall %d cases pass (%s)' % (len(cases), shell))

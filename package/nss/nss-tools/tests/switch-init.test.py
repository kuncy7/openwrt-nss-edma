# SPDX-License-Identifier: GPL-2.0-only
"""Check the switch_to_trunk() fabric step without touching the host's sysfs.

Both fabrics: qca8337 (unbind qca8k, insmod qca8337-nss.ko) and rtl8367s
(unbind rtl8365mb-mdio, insmod rtl8367s-nss.ko), plus none and an unknown
name. A failed insmod or unbind must keep the plane off (wifi_load 0, no
takeover); a loaded module or fabric=none must skip everything.
"""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile

tree = Path(__file__).resolve().parents[4]
shell = os.environ.get('NSS_TEST_SH', 'sh')
source = (tree/'package/nss/nss-tools/files/nss-dwmac.init').read_text(encoding='utf-8')
source = source.replace('. /lib/functions.sh', ':').replace('. /lib/nss/functions.sh', ':')
stubs = r'''
uci() {
    case "$*" in
        *nss.general.fabric) echo "$test_fabric" ;;
        *nss.general.switch_dev) echo "$test_dev" ;;
        *nss.general.vtu) echo "$test_vtu" ;;
        *nss.general.switch_args) echo "$test_args" ;;
    esac
}
board_name() { echo xiaomi,redmi-ax5400; }
insmod() {
    printf 'insmod %s\n' "$*" >> "$calls"
    case "$*" in *identify=1*) return "$identify_rc" ;; esac
    return "$insmod_rc"
}
rmmod() { printf 'rmmod %s\n' "$*" >> "$calls"; }
modprobe() { echo unexpected-modprobe >> "$calls"; return 1; }
sleep() { :; }
logger() { :; }
wifi_load() { echo "wifi $1" >> "$calls"; }
procd_open_instance() { echo unexpected-takeover >> "$calls"; }
'''
QCA = 'cpu_port=6 ports=0x7e'
# name, fabric, driver, vtu, switch_args, loaded, fail_unbind, insmod_rc, action, expected rc
cases = [
    ('VLAN load failure', 'qca8337', 'qca8k', '1:6t,2u,3u,4u;2:5t,1u', QCA, False, False, 1, 'start_service', 1),
    ('flat load failure', 'qca8337', 'qca8k', '', QCA, False, False, 1, 'start_service', 1),
    ('successful load', 'qca8337', 'qca8k', '1:6t,2u,3u,4u;2:5t,1u', QCA, False, False, 0, 'switch_to_trunk', 0),
    ('flat load', 'qca8337', 'qca8k', '', QCA, False, False, 0, 'switch_to_trunk', 0),
    ('service restart', 'qca8337', 'qca8k', '', QCA, True, False, 1, 'switch_to_trunk', 0),
    ('switchless board', 'none', 'qca8k', '', '', False, False, 1, 'switch_to_trunk', 0),
    ('unbind failure', 'qca8337', 'qca8k', '', QCA, False, True, 0, 'start_service', 1),
    ('rtl8367s load', 'rtl8367s', 'rtl8365mb-mdio', '1:6t,1u,2u,3u,4u;2:6t,0u', '', False, False, 0, 'switch_to_trunk', 0),
    ('rtl8367s load failure', 'rtl8367s', 'rtl8365mb-mdio', '1:6t,1u,2u,3u,4u;2:6t,0u', '', False, False, 1, 'start_service', 1),
    ('rtl8367s service restart', 'rtl8367s', 'rtl8365mb-mdio', '', '', True, False, 1, 'switch_to_trunk', 0),
    ('rtl8367s unbind failure', 'rtl8367s', 'rtl8365mb-mdio', '', '', False, True, 0, 'start_service', 1),
    ('unknown fabric', 'rtl9999', 'qca8k', '', '', False, False, 0, 'start_service', 1),
    # A family C RTL8367S: the module refuses the chip, so nothing is unbound.
    ('rtl8367s wrong chip', 'rtl8367s', 'rtl8365mb-mdio', '1:6t,1u,2u,3u,4u;2:6t,0u', '', False, False, 0, 'start_service', 1),
]
for name, fabric, driver, vtu, args, loaded, fail_unbind, insmod_rc, action, expected in cases:
    with tempfile.TemporaryDirectory(prefix='switch-service-') as directory:
        path = Path(directory)
        module = path/'module'/(fabric + '_nss')
        mdio = path/'mdio'/driver
        calls = path/'calls'
        calls.touch()
        if loaded:
            module.mkdir(parents=True)
        (mdio/'test-device').mkdir(parents=True)
        if fail_unbind:
            (mdio/'unbind').mkdir()  # Redirecting to a directory must fail.
        else:
            (mdio/'unbind').touch()
        # The other driver's device list is empty: an unbind must go to the
        # right driver, never to whichever one has a device.
        other = path/'mdio'/('rtl8365mb-mdio' if driver == 'qca8k' else 'qca8k')
        other.mkdir(parents=True)
        (other/'unbind').touch()
        script = source.replace('/sys/module/', str(path/'module') + '/')
        script = script.replace('/sys/bus/mdio_bus/drivers/', str(path/'mdio') + '/')
        script += '\n' + stubs
        for key, value in [('test_fabric', fabric), ('test_dev', 'test-device'), ('test_vtu', vtu),
                           ('test_args', args), ('calls', str(calls)), ('insmod_rc', str(insmod_rc)),
                           ('identify_rc', '1' if name == 'rtl8367s wrong chip' else '0')]:
            script += f'{key}={shlex.quote(value)}\n'
        script += action + '\n'
        result = subprocess.run([shell, '-s'], input=script, text=True, capture_output=True)
        assert result.returncode == expected, (name, result.returncode, result.stderr)
        log = calls.read_text(encoding='utf-8')
        assert 'unexpected-' not in log, (name, log)
        if expected:
            assert 'wifi 0\n' in log, (name, log)
        unbound = '' if fail_unbind else (mdio/'unbind').read_text(encoding='utf-8')
        assert (other/'unbind').read_text(encoding='utf-8') == '', (name, 'wrong driver unbound')
        loads = [l for l in log.splitlines() if l.startswith('insmod ') and 'identify=1' not in l]
        asked = [l for l in log.splitlines() if l.startswith('insmod ') and 'identify=1' in l]
        # The chip is asked about exactly once, on rtl8367s only, before any
        # unbind, and the probe load is removed again when it succeeded.
        assert len(asked) == (1 if fabric == 'rtl8367s' and not loaded else 0), (name, log)
        assert log.count('rmmod rtl8367s_nss') == (1 if asked and name != 'rtl8367s wrong chip' else 0), (name, log)
        if loaded or fabric in ('none', 'rtl9999') or fail_unbind or name == 'rtl8367s wrong chip':
            assert not loads, (name, log)
            assert unbound == '', (name, 'unbind written', unbound)
        else:
            assert unbound == 'test-device\n', (name, 'unbind', unbound)
            assert len(loads) == 1, (name, log)
            assert f'/{fabric}-nss.ko' in log, (name, log)
            assert (args in log) if args else ('cpu_port' not in log), (name, log)
            assert ('vlans=' + vtu in log) if vtu else ('vlans=' not in log), (name, log)
        print('PASS:', name)

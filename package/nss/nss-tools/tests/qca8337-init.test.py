# SPDX-License-Identifier: GPL-2.0-only
"""Check switch setup failure propagation without touching the host's sysfs."""
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
        *nss.general.switch_dev) echo test-device ;;
        *nss.general.vtu) echo "$test_vtu" ;;
        *nss.general.switch_args) echo 'cpu_port=6 ports=0x7e' ;;
    esac
}
board_name() { echo xiaomi,redmi-ax5400; }
insmod() { printf 'insmod %s\n' "$*" >> "$calls"; return "$insmod_rc"; }
modprobe() { echo unexpected-modprobe >> "$calls"; return 1; }
sleep() { :; }
logger() { :; }
wifi_load() { echo "wifi $1" >> "$calls"; }
procd_open_instance() { echo unexpected-takeover >> "$calls"; }
'''
cases = [
    ('VLAN load failure', 'qca8337', '1:6t,2u,3u,4u;2:5t,1u', False, False, 1, 'start_service', 1),
    ('flat load failure', 'qca8337', '', False, False, 1, 'start_service', 1),
    ('successful load', 'qca8337', '1:6t,2u,3u,4u;2:5t,1u', False, False, 0, 'switch_to_trunk', 0),
    ('flat load', 'qca8337', '', False, False, 0, 'switch_to_trunk', 0),
    ('service restart', 'qca8337', '', True, False, 1, 'switch_to_trunk', 0),
    ('switchless board', 'none', '', False, False, 1, 'switch_to_trunk', 0),
    ('unbind failure', 'qca8337', '', False, True, 0, 'start_service', 1),
]
for name, fabric, vtu, loaded, fail_unbind, insmod_rc, action, expected in cases:
    with tempfile.TemporaryDirectory(prefix='qca8337-service-') as directory:
        path = Path(directory)
        module = path/'module'
        mdio = path/'mdio'
        calls = path/'calls'
        calls.touch()
        if loaded:
            module.mkdir()
        if fail_unbind:
            (mdio/'test-device').mkdir(parents=True)
            (mdio/'unbind').mkdir()  # Redirecting to a directory must fail.
        script = source.replace('/sys/module/qca8337_nss', str(module))
        script = script.replace('/sys/bus/mdio_bus/drivers/qca8k', str(mdio))
        script += '\n' + stubs
        for key, value in [('test_fabric', fabric), ('test_vtu', vtu), ('calls', str(calls)), ('insmod_rc', str(insmod_rc))]:
            script += f'{key}={shlex.quote(value)}\n'
        script += action + '\n'
        result = subprocess.run([shell, '-s'], input=script, text=True, capture_output=True)
        assert result.returncode == expected, (name, result.stderr)
        log = calls.read_text(encoding='utf-8')
        assert 'unexpected-' not in log, (name, log)
        if expected:
            assert 'wifi 0\n' in log, (name, log)
        if loaded or fabric == 'none' or fail_unbind:
            assert 'insmod' not in log, (name, log)
        else:
            assert log.count('insmod ') == 1, (name, log)
            assert 'cpu_port=6 ports=0x7e' in log, (name, log)
            assert ('vlans=' + vtu in log) if vtu else ('vlans=' not in log), (name, log)
        print('PASS:', name)

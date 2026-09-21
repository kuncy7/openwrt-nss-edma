"""Run the first-boot board table against a disposable uci state."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

source = Path(__file__).resolve().parents[4]
shell = os.environ.get("NSS_TEST_SH", "sh")
defaults = source / 'package/nss/nss-tools/files/nss-dwmac.defaults'
text = defaults.read_text(encoding='utf-8')
subprocess.run([shell, '-n', str(defaults)], check=True)
assert text.count('. /lib/functions.sh') == 1

# uci over a flat JSON map: 'network.cfg1.name' -> 'wan'. Anonymous sections
# are named here the way config_load names them.
uci = r'''#!/usr/bin/env python3
import json, os, sys
path = os.environ['UCI_STATE']
state = json.load(open(path))
args = [a for a in sys.argv[1:] if a != '-q']
op, key = args[0], (args[1] if len(args) > 1 else '')
if op == 'get':
    if key not in state:
        sys.exit(1)
    print(state[key])
    sys.exit(0)
if op == 'set':
    key, value = key.split('=', 1)
    state[key] = value
elif op == 'delete':
    # A section's options go with it, as they do under the real uci.
    for k in [k for k in state if k == key or k.startswith(key + '.')]:
        del state[k]
elif op == 'add':
    # uci add <config> <type>: a new anonymous section, its name on stdout
    i = 1
    while '%s.new%d' % (key, i) in state:
        i += 1
    name = 'new%d' % i
    state['%s.%s' % (key, name)] = args[2]
    print(name)
elif op == 'add_list':
    key, value = key.split('=', 1)
    state[key] = (state.get(key, '') + ' ' + value).strip()
elif op == 'sections':
    print(' '.join(k.split('.')[1] for k, v in state.items()
                   if k.count('.') == 1 and k.startswith(key + '.') and v == args[2]))
    sys.exit(0)
elif op != 'commit':
    raise SystemExit('unexpected uci call: %r' % (args,))
json.dump(state, open(path, 'w'))
'''
functions = r'''
board_name() { printf '%s\n' "$TEST_BOARD"; }
logger() { :; }
config_load() { CONFIG_LOADED="$1"; }
config_get() { eval "$1=\$(uci -q get $CONFIG_LOADED.$2.$3)"; }
config_foreach() {
	_fn="$1"; _type="$2"
	for _s in $(uci sections "$CONFIG_LOADED" "$_type"); do "$_fn" "$_s"; done
}
'''

# The RA74's config with a 'config device' section and a conduit per port -
# what LuCI writes. board.d itself leaves the conduits in /etc/board.json and
# none in /etc/config/network; the dsa cases below feed it that way too.
ra74 = {
    'network.lan': 'interface', 'network.lan.device': 'br-lan',
    'network.@device[0]': 'device', 'network.@device[0].name': 'br-lan',
    'network.@device[0].ports': 'lan1 lan2 lan3',
    'network.wan': 'interface', 'network.wan.device': 'wan',
    'network.wan6': 'interface', 'network.wan6.device': 'wan',
    'network.cfg1': 'device', 'network.cfg1.name': 'lan1', 'network.cfg1.conduit': 'eth1',
    'network.cfg2': 'device', 'network.cfg2.name': 'wan', 'network.cfg2.conduit': 'eth0',
    'network.cfg4': 'device', 'network.cfg4.name': 'lan2', 'network.cfg4.conduit': 'eth1',
    'network.cfg5': 'device', 'network.cfg5.name': 'lan3', 'network.cfg5.conduit': 'eth1',
}
b3000 = {k: v for k, v in ra74.items() if not k.startswith('network.cfg')}
b3000['network.@device[0].ports'] = 'lan1 lan2'

with tempfile.TemporaryDirectory(prefix='nss-dwmac-defaults-') as directory:
    tmp = Path(directory)
    (tmp / 'uci').write_text(uci)
    (tmp / 'uci').chmod(0o755)
    (tmp / 'defaults.sh').write_text(text.replace('. /lib/functions.sh', functions))

    # The dsa branch reads two sysfs trees: the switch drivers' device lists
    # (a bound switch is the whole test) and the radios' firmware memory mode.
    # Point both at this directory so the branch can be driven from here.
    mdio = tmp / 'mdio' / 'qca8k'
    mdio.mkdir(parents=True)
    (mdio / '90000.mdio-1:11').touch()
    (tmp / 'dt').mkdir()
    # /etc/board.json through jshn: a stand-in that runs in any sh, and the
    # real jshn.sh with the host jshn when the tree has been built (it needs
    # ash or bash - dash has no 'export -n').
    (tmp / 'jq.py').write_text(r'''
import json, sys
f, path, op = sys.argv[1:4]
arg = sys.argv[4] if len(sys.argv) > 4 else None
d = json.load(open(f))
for p in [x for x in path.split('/') if x]:
    d = d[p]
if op == 'keys':
    print(' '.join(d))
elif op == 'type':
    v = d.get(arg) if isinstance(d, dict) else None
    print('object' if isinstance(v, dict) else 'array' if isinstance(v, list)
          else '' if v is None else 'string')
elif op == 'get':
    v = d.get(arg)
    print('' if v is None else v)
''')
    (tmp / 'jshn.sh').write_text('''
json_load_file() { __jf="$1"; __jp=''; [ -r "$1" ]; }
__jq() { python3 "%s" "$__jf" "$__jp" "$@"; }
json_get_type() { eval "$1=\\$(__jq type \\"\\$2\\")"; }
json_select() { if [ "$1" = .. ]; then __jp="${__jp%%/*}"; else __jp="$__jp/$1"; fi; }
json_get_keys() { eval "$1=\\$(__jq keys)"; }
json_get_var() { eval "$1=\\$(__jq get \\"\\$2\\")"; }
json_cleanup() { :; }
''' % (tmp / 'jq.py'))
    real_jshn = source / 'staging_dir/host/share/libubox/jshn.sh'
    host_bin = source / 'staging_dir/host/bin'
    have_real_jshn = real_jshn.exists() and (host_bin / 'jshn').exists() and shutil.which('bash')

    def dsa_script(name, jshn):
        (tmp / name).write_text(
            text.replace('. /lib/functions.sh', functions)
                .replace('/sys/bus/mdio_bus/drivers/', str(tmp / 'mdio') + '/')
                .replace('/sys/firmware/devicetree/base/', str(tmp / 'dt') + '/')
                .replace('/etc/board.json', str(tmp / 'board.json'))
                .replace('/usr/share/libubox/jshn.sh', str(jshn)))
    dsa_script('defaults-dsa.sh', tmp / 'jshn.sh')
    if have_real_jshn:
        dsa_script('defaults-dsa-real.sh', real_jshn)

    def run(board, state, script='defaults.sh', sh=None, path=''):
        state_file = tmp / 'state.json'
        state_file.write_text(json.dumps(state))
        env = dict(os.environ, TEST_BOARD=board, UCI_STATE=str(state_file),
                   PATH=str(tmp) + ':' + path + os.environ['PATH'])
        done = subprocess.run([sh or shell, str(tmp / script)], env=env,
                              capture_output=True, text=True)
        assert done.returncode == 0, (board, done.stderr)
        # jshn prints its warnings on stdout; the script must not trigger any.
        assert 'WARNING' not in done.stdout, (board, done.stdout)
        return json.loads(state_file.read_text())

    def run_dsa(board, state, board_json=None):
        bj = tmp / 'board.json'
        if board_json is None:
            bj.unlink(missing_ok=True)
        else:
            bj.write_text(json.dumps(board_json))
        return run(board, state, 'defaults-dsa.sh')

    def run_dsa_both(board, state, board_json):
        # The same case through the stand-in and, where built, the real jshn.
        r = run_dsa(board, state, board_json)
        if have_real_jshn:
            real = run(board, state, 'defaults-dsa-real.sh', sh='bash', path=str(host_bin) + ':')
            assert real == r, (board, 'real jshn disagrees', real, r)
        return r

    def check(result, expected, board):
        for key, value in expected.items():
            assert result.get(key) == value, (board, key, result.get(key), value)

    # Fresh RA74: both CPU links, WAN on GMAC0, the wan conduit section follows.
    r = run('xiaomi,redmi-ax5400', ra74)
    check(r, {
        'nss.general.fw_mask': '0x3', 'nss.general.trunk': 'eth1',
        'nss.general.trunk_if': '1', 'nss.general.extra_ports': '0:eth0',
        'nss.general.vtu': '1:6t,2u,3u,4u;2:5t,1u',
        'nss.general.switch_args': 'cpu_port=6 ports=0x7e wake_phys=90000.mdio-1:00,'
                                   '90000.mdio-1:01,90000.mdio-1:02,90000.mdio-1:03,90000.mdio-1:04',
        'nss.general.wifi_offload': '0', 'nss.general.topology': 'vlan-trunk',
        'network.wan.device': 'eth0.2', 'network.wan6.device': 'eth0.2',
        'network.@device[0].ports': 'eth1.1',
        'network.cfg2.name': 'eth0.2', 'network.cfg1.name': 'lan1',
        'network.cfg1.conduit': 'eth1',
    }, 'ra74')
    assert 'network.cfg2.conduit' not in r
    assert run('xiaomi,redmi-ax5400', r) == r, 'second run must change nothing'

    # Cudy P5: the same two CPU links, but LAN uses ports 1-3 and WAN port 4.
    r = run('cudy,p5', ra74)
    check(r, {
        'nss.general.fw_mask': '0x3', 'nss.general.trunk': 'eth1',
        'nss.general.trunk_if': '1', 'nss.general.extra_ports': '0:eth0',
        'nss.general.vtu': '1:6t,1u,2u,3u;2:5t,4u',
        'nss.general.switch_args': 'cpu_port=6 ports=0x7e wake_phys=90000.mdio-1:00,'
                                   '90000.mdio-1:01,90000.mdio-1:02,90000.mdio-1:03,90000.mdio-1:04',
        'nss.general.wifi_offload': '1', 'nss.general.topology': 'vlan-trunk',
        'network.wan.device': 'eth0.2', 'network.wan6.device': 'eth0.2',
        'network.@device[0].ports': 'eth1.1',
        'network.cfg2.name': 'eth0.2', 'network.cfg1.name': 'lan1',
        'network.cfg1.conduit': 'eth1',
    }, 'cudy p5')
    assert 'network.cfg2.conduit' not in r
    assert run('cudy,p5', r) == r, 'Cudy P5 second run must change nothing'

    # An admin's Wi-Fi offload choice survives the board's host default.
    r = run('xiaomi,redmi-ax5400', dict(ra74, **{'nss.general.wifi_offload': '1'}))
    assert r['nss.general.wifi_offload'] == '1'

    # A tagged ISP VLAN already on eth0 is kept, and the VTU follows it.
    r = run('xiaomi,redmi-ax5400', dict(ra74, **{'network.wan.device': 'eth0.35',
                                                  'network.wan6.device': 'eth0.35'}))
    check(r, {'network.wan.device': 'eth0.35', 'nss.general.vtu': '1:6t,2u,3u,4u;35:5t,1u',
              'network.cfg2.name': 'wan'}, 'ra74 vlan 35')

    # No wan6 interface: none is created.
    r = run('xiaomi,redmi-ax5400', {k: v for k, v in ra74.items() if not k.startswith('network.wan6')})
    assert 'network.wan6.device' not in r

    # A migrated config is left alone, single-link layout included.
    single = dict(ra74, **{'nss.general.topology': 'vlan-trunk', 'network.wan.device': 'eth1.2'})
    assert run('xiaomi,redmi-ax5400', single) == single

    # GL-B3000 control: WAN on the trunk, a cloned MAC moves to eth0.2.
    r = run('glinet,gl-b3000', dict(b3000, **{'network.cfg3': 'device', 'network.cfg3.name': 'wan',
                                              'network.cfg3.macaddr': '02:00:00:00:00:01'}))
    check(r, {'nss.general.fw_mask': '0x2', 'network.wan.device': 'eth0.2',
              'network.@device[0].ports': 'eth0.1', 'network.cfg3.name': 'eth0.2',
              'network.cfg3.macaddr': '02:00:00:00:00:01', 'nss.general.wifi_offload': '1'}, 'b3000')

    # LAN-only trunk (Xunison D50): the WAN netdev stays.
    r = run('xunison,exigo-hub-d50-5g', b3000)
    check(r, {'network.wan.device': 'wan', 'network.@device[0].ports': 'eth1',
              'nss.general.topology': 'lan-trunk', 'nss.general.extra_ports': '0:wan'}, 'd50')

    # TP-Link EX511 v2: a headerless RTL8367D that keeps its DSA driver. One
    # flat LAN on the untagged trunk, no VTU, no fabric module; the five DSA
    # user ports leave br-lan but each gets a bare interface holding it up,
    # and the WAN socket - which can transmit but never receive - loses its
    # DHCP: wan stays with proto none, wan6 goes altogether.
    ex511 = dict(b3000, **{'network.@device[0].ports': 'lan1 lan2 lan3 lan4',
                           'network.wan.proto': 'dhcp', 'network.wan6.proto': 'dhcpv6'})
    ports = ('wan', 'lan1', 'lan2', 'lan3', 'lan4')
    r = run('tplink,ex511-v2', ex511)
    check(r, {
        'nss.general.fw_mask': '0x2', 'nss.general.trunk': 'eth0',
        'nss.general.trunk_if': '1', 'nss.general.fabric': 'none',
        'nss.general.wifi_offload': '1', 'nss.general.topology': 'lan-trunk',
        'network.@device[0].ports': 'eth0',
        'network.wan.device': 'wan', 'network.wan.proto': 'none',
    }, 'ex511')
    for key in ('nss.general.vtu', 'nss.general.switch_args', 'nss.general.extra_ports',
                'nss.general.switch_dev'):
        assert key not in r, ('ex511', key, r[key])
    assert not [k for k in r if k.startswith('network.wan6')], ('ex511', 'wan6 survived')
    for port in ports:
        check(r, {'network.port_%s' % port: 'interface', 'network.port_%s.device' % port: port,
                  'network.port_%s.proto' % port: 'none', 'network.port_%s.auto' % port: '1'},
              'ex511 port ' + port)
    assert run('tplink,ex511-v2', r) == r, 'second run must change nothing'

    # The port interfaces are only added where missing: a config that already
    # holds one (no marker yet, so the table still runs) keeps it as it is.
    r = run('tplink,ex511-v2', dict(ex511, **{'network.port_lan1': 'interface',
                                              'network.port_lan1.device': 'lan1',
                                              'network.port_lan1.proto': 'static',
                                              'network.port_lan1.ipaddr': '10.0.0.2'}))
    check(r, {'network.port_lan1.proto': 'static', 'network.port_lan1.ipaddr': '10.0.0.2',
              'network.port_lan2.proto': 'none'}, 'ex511 existing port')
    assert 'network.port_lan1.auto' not in r

    # wan present, wan6 already absent: wan is still neutered, nothing else appears.
    r = run('tplink,ex511-v2', {k: v for k, v in ex511.items() if not k.startswith('network.wan6')})
    assert r['network.wan.proto'] == 'none'
    assert not [k for k in r if k.startswith('network.wan6')], ('ex511 no wan6', 'wan6 appeared')

    # A wan someone already moved onto a VLAN of the trunk is not on the dead
    # port; it works, and keeps its protocol. wan6 with it.
    r = run('tplink,ex511-v2', dict(ex511, **{'network.wan.device': 'eth0.35',
                                              'network.wan6.device': 'eth0.35'}))
    check(r, {'network.wan.device': 'eth0.35', 'network.wan.proto': 'dhcp',
              'network.wan6.device': 'eth0.35', 'network.wan6.proto': 'dhcpv6',
              'network.port_wan.proto': 'none'}, 'ex511 wan on a vlan')

    # The same board with wan and wan6 already gone (a dumb AP someone
    # trimmed by hand): the port interfaces still appear, nothing else does.
    r = run('tplink,ex511-v2', {k: v for k, v in ex511.items() if not k.startswith('network.wan')})
    assert 'network.port_wan.proto' in r
    assert not [k for k in r if k.startswith('network.wan')], ('ex511 no wan', 'wan reappeared')

    # With the switch driver bound the dsa topology is picked and the trunk
    # table never runs. On the AX5400 board.d splits the ports across both CPU
    # links - lan1-3 on eth1, wan on eth0 - and this topology cannot keep that:
    # only the armed conduit's ports reach the firmware, and on this board the
    # odd link is the dead-RX GMAC0 (openwrt#24696). The WAN port follows the
    # majority onto eth1; nothing else about the config is touched.
    #
    # A fresh flash first, as it really is: board.d's split sits in
    # /etc/board.json and /etc/config/network has no device section for any
    # port (forum #402 - the WAN stayed on eth0 until one was added by hand).
    # wan gets a new section of its own; the lan ports, already on eth1 by
    # board.json, get nothing.
    split = {'model': {'id': 'xiaomi,redmi-ax5400'}, 'network_device': {
        'lan1': {'conduit': 'eth1'}, 'lan2': {'conduit': 'eth1'},
        'lan3': {'conduit': 'eth1'}, 'wan': {'conduit': 'eth0'}}}
    fresh = dict(b3000, **{'network.@device[0].ports': 'lan1 lan2 lan3'})
    r = run_dsa_both('xiaomi,redmi-ax5400', fresh, split)
    check(r, {'nss.general.topology': 'dsa', 'network.new1': 'device',
              'network.new1.name': 'wan', 'network.new1.conduit': 'eth1',
              'network.wan.device': 'wan', 'network.@device[0].ports': 'lan1 lan2 lan3'},
          'ra74 dsa fresh')
    assert len([k for k in r if k.endswith('.conduit')]) == 1, ('ra74 dsa fresh', r)

    # A device section that names wan already (a MAC override) takes the
    # conduit: a second section for the same device is not made.
    mac = dict(fresh, **{'network.cfg3': 'device', 'network.cfg3.name': 'wan',
                         'network.cfg3.macaddr': '02:00:00:00:00:01'})
    r = run_dsa_both('xiaomi,redmi-ax5400', mac, split)
    check(r, {'network.cfg3.name': 'wan', 'network.cfg3.conduit': 'eth1',
              'network.cfg3.macaddr': '02:00:00:00:00:01'}, 'ra74 dsa mac section')
    assert not [k for k in r if k.startswith('network.new')], ('ra74 dsa mac section', r)

    # A section that already put wan on eth1 overrides board.json: nothing to do.
    moved = dict(fresh, **{'network.cfg3': 'device', 'network.cfg3.name': 'wan',
                           'network.cfg3.conduit': 'eth1'})
    assert run_dsa_both('xiaomi,redmi-ax5400', moved, split) == dict(
        moved, **{'nss.general.topology': 'dsa', 'nss.general.enabled': '1',
                  'nss.general.wifi_offload': '1', 'nss.general.fw_logbuf': '256'}), 'ra74 dsa override kept'

    # A board.json with network_device entries but no split, and one with no
    # network_device at all: no conduit anywhere, and jshn is not made to warn.
    for bj in ({'network_device': {'lan1': {'conduit': 'eth1'}, 'wan': {'conduit': 'eth1'}}},
               {'network_device': {'wan': {'macaddr': '02:00:00:00:00:02'}}},
               {'model': {'id': 'glinet,gl-b3000'}}):
        r = run_dsa_both('glinet,gl-b3000', b3000, bj)
        assert not [k for k in r if k.endswith('.conduit')], ('no split', bj, r)

    # The same split set by LuCI instead, as uci sections: the sections move.
    r = run_dsa('xiaomi,redmi-ax5400', ra74)
    check(r, {'nss.general.topology': 'dsa', 'network.cfg2.conduit': 'eth1',
              'network.cfg1.conduit': 'eth1', 'network.cfg4.conduit': 'eth1',
              'network.cfg5.conduit': 'eth1', 'network.cfg2.name': 'wan',
              'network.wan.device': 'wan', 'network.@device[0].ports': 'lan1 lan2 lan3'}, 'ra74 dsa')
    for key in ('nss.general.vtu', 'nss.general.trunk', 'nss.general.switch_args',
                'nss.general.fw_mask'):
        assert key not in r, ('ra74 dsa', key, r[key])
    assert run_dsa('xiaomi,redmi-ax5400', r) == r, 'second run must change nothing'

    # A board with no conduit assignments at all (the GL-B3000): the branch
    # leaves the network config exactly as netifd generated it.
    r = run_dsa('glinet,gl-b3000', b3000)
    check(r, {'nss.general.topology': 'dsa', 'network.@device[0].ports': 'lan1 lan2',
              'network.wan.device': 'wan'}, 'b3000 dsa')
    assert not [k for k in r if k.endswith('.conduit')], ('b3000 dsa', 'a conduit appeared')

    # One port on each link: there is nothing here to say which link works, so
    # both conduits are left as they are.
    even = dict(b3000, **{'network.cfg1': 'device', 'network.cfg1.name': 'lan1',
                          'network.cfg1.conduit': 'eth1',
                          'network.cfg2': 'device', 'network.cfg2.name': 'wan',
                          'network.cfg2.conduit': 'eth0'})
    r = run_dsa('cmcc,pz-l8', even)
    check(r, {'nss.general.topology': 'dsa', 'network.cfg1.conduit': 'eth1',
              'network.cfg2.conduit': 'eth0'}, 'even split dsa')

    # A radio asking for firmware memory mode 1 in its DTS does not turn the
    # Wi-Fi offload off: 13 of the 17 IPQ5018 boards declare mode 1 and the
    # ones measured (AX6000, Archer AX55) run wifili on it. The old code set
    # wifi_offload=0 here, which cost an MR5500 owner 4x the throughput.
    radio = tmp / 'dt' / 'soc@0' / 'wifi@c000000'
    radio.mkdir(parents=True)
    (radio / 'qcom,ath11k-fw-memory-mode').write_bytes(bytes([0, 0, 0, 1]))
    r = run_dsa('glinet,gl-b3000', b3000)
    check(r, {'nss.general.wifi_offload': '1'}, 'memory mode 1 keeps the offload on')

    # A value set by hand still wins, in either direction.
    r = run_dsa('glinet,gl-b3000', dict(b3000, **{'nss.general.wifi_offload': '0'}))
    check(r, {'nss.general.wifi_offload': '0'}, 'hand-set wifi_offload=0 kept')

print('PASS: RA74 and Cudy P5 dual link, rerun, Wi-Fi choice kept, tagged WAN, '
      'no wan6, migrated config, B3000 MAC clone, D50 LAN-only trunk, '
      'EX511 headerless switch, dsa conduit consolidation (AX5400 fresh from board.json, '
      'existing wan section, uci override, LuCI sections, B3000, even split), '
      'Wi-Fi offload on with firmware memory mode 1')

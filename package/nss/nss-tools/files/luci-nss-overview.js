'use strict';
'require baseclass';
'require fs';

// Status -> Overview: NSS core load (Min/Avg/Max per core, the firmware's own
// figures), the connections ECM has handed to the firmware, and the SoC
// temperatures by block. Read-only; the full report is Status -> NSS Offload.

var LOAD = '/sys/kernel/debug/qca-nss-drv/stats/cpu_load_ubi',
    ECM4 = '/sys/kernel/debug/ecm/ecm_nss_ipv4/accelerated_count',
    ECM6 = '/sys/kernel/debug/ecm/ecm_nss_ipv6/accelerated_count',
    THERMAL = '/sys/class/thermal';

// Thermal zone type -> block. ipq807x names the NSS cores nssN, ipq5018 names
// its one core ubi32; the Wi-Fi blocks are wcss-*. Anything else (top-glue,
// gephy, cluster) is grouped as the rest of the SoC.
var BLOCKS = [
	[ /^cpu|^cluster/, _('CPU temperature') ],
	[ /^nss|^ubi32/,   _('NSS temperature') ],
	[ /^wcss/,         _('Wi-Fi temperature') ],
	[ /./,             _('Other SoC temperature') ]
];

document.head.append(E('style', { 'type': 'text/css' }, [ `
.nss-pills { display: inline-flex; border-radius: 4px; border: 1px solid var(--border-color-high, #ccc); overflow: hidden }
.nss-pills span { padding: 0 6px }
.nss-pills .v { min-width: 3.5em; text-align: center; background-color: var(--primary-color-medium, #2196f3); color: var(--on-primary-color, #fff) }
` ]));

function pills(pairs) {
	var nodes = [];
	pairs.forEach(function(p) {
		if (p[0] != null)
			nodes.push(E('span', {}, [ p[0] ]));
		nodes.push(E('span', { 'class': 'v' }, [ p[1] ]));
	});
	return E('div', { 'class': 'nss-pills' }, nodes);
}

// "Core N:" then a "Min Avg Max" header and a "1% 3% 4%" line, per core.
function parseLoad(text) {
	var cores = [], core = null;

	(text || '').split(/\n/).forEach(function(line) {
		var m = line.match(/^Core (\d+):/);
		if (m) {
			core = m[1];
			return;
		}
		if (core != null && line.match(/%/)) {
			var v = line.trim().split(/\s+/);
			cores.push({ core: core, min: v[0], avg: v[1], max: v[2] });
			core = null;
		}
	});

	return cores;
}

function readThermal() {
	return L.resolveDefault(fs.list(THERMAL), []).then(function(dirs) {
		return Promise.all(dirs.filter(function(d) {
			return d.name.indexOf('thermal_zone') == 0;
		}).map(function(d) {
			return Promise.all([
				L.resolveDefault(fs.read(THERMAL + '/' + d.name + '/type'), null),
				L.resolveDefault(fs.read(THERMAL + '/' + d.name + '/temp'), null)
			]).then(function(r) {
				var t = parseInt(r[1], 10);
				return (r[0] && !isNaN(t)) ? { type: r[0].trim(), temp: t / 1000 } : null;
			});
		}));
	}).then(function(zones) {
		var groups = BLOCKS.map(function(b) { return { title: b[1], temps: [] }; });
		zones.forEach(function(z) {
			if (!z)
				return;
			for (var i = 0; i < BLOCKS.length; i++)
				if (BLOCKS[i][0].test(z.type)) {
					groups[i].temps.push(z.temp);
					break;
				}
		});
		return groups.filter(function(g) { return g.temps.length; });
	});
}

function deg(v) {
	return '%.1f°C'.format(v);
}

return baseclass.extend({
	title: _('NSS Offload'),

	load: function() {
		return Promise.all([
			L.resolveDefault(fs.read(LOAD), null),
			L.resolveDefault(fs.read(ECM4), null),
			L.resolveDefault(fs.read(ECM6), null),
			readThermal()
		]);
	},

	render: function(data) {
		var cores = parseLoad(data[0]);

		// No NSS driver loaded (or no access): leave the overview alone.
		if (!cores.length)
			return null;

		var rows = [];

		cores.forEach(function(c) {
			rows.push([ cores.length > 1 ? _('NSS core %s load').format(c.core) : _('NSS core load'),
				pills([ [ 'min', c.min ], [ 'avg', c.avg ], [ 'max', c.max ] ]) ]);
		});

		if (data[1] != null)
			rows.push([ _('ECM accelerated connections'),
				pills([ [ 'IPv4', String(data[1]).trim() ], [ 'IPv6', String(data[2] || '0').trim() ] ]) ]);

		data[3].forEach(function(g) {
			var t = g.temps,
			    min = Math.min.apply(null, t),
			    max = Math.max.apply(null, t),
			    avg = t.reduce(function(a, b) { return a + b; }, 0) / t.length;

			rows.push([ g.title, (t.length > 1)
				? pills([ [ 'min', deg(min) ], [ 'avg', deg(avg) ], [ 'max', deg(max) ] ])
				: pills([ [ null, deg(t[0]) ] ]) ]);
		});

		return E('table', { 'class': 'table' }, rows.map(function(r) {
			return E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td left', 'width': '33%' }, [ r[0] ]),
				E('td', { 'class': 'td left' }, [ r[1] ])
			]);
		}));
	}
});

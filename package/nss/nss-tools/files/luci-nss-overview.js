'use strict';
'require baseclass';
'require fs';

// Status -> Overview: the NSS core load (Avg/Max per core, from the firmware's
// own instruction counts) and the connections ECM has handed to it. Read-only,
// two small debugfs files; the full report is on Status -> NSS Offload.

var LOAD = '/sys/kernel/debug/qca-nss-drv/stats/cpu_load_ubi',
    ECM4 = '/sys/kernel/debug/ecm/ecm_nss_ipv4/accelerated_count',
    ECM6 = '/sys/kernel/debug/ecm/ecm_nss_ipv6/accelerated_count';

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
			var v = line.trim().split(/\s+/).map(function(s) { return s.replace('%', ''); });
			cores.push({ core: core, min: v[0], avg: v[1], max: v[2] });
			core = null;
		}
	});

	return cores;
}

return baseclass.extend({
	title: _('NSS Offload'),

	load: function() {
		return Promise.all([
			L.resolveDefault(fs.read(LOAD), null),
			L.resolveDefault(fs.read(ECM4), null),
			L.resolveDefault(fs.read(ECM6), null)
		]);
	},

	render: function(data) {
		var cores = parseLoad(data[0]);

		// No NSS driver loaded (or no access): leave the overview alone.
		if (!cores.length)
			return null;

		var table = E('table', { 'class': 'table' });

		cores.forEach(function(c) {
			table.appendChild(E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td left', 'width': '33%' }, [ cores.length > 1 ? _('NSS core %s load').format(c.core) : _('NSS core load') ]),
				E('td', { 'class': 'td left' }, [ _('Min: %s%% Avg: %s%% Max: %s%%').format(c.min, c.avg, c.max) ])
			]));
		});

		if (data[1] != null)
			table.appendChild(E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td left', 'width': '33%' }, [ _('ECM accelerated connections') ]),
				E('td', { 'class': 'td left' }, [ '%s IPv4 / %s IPv6'.format(String(data[1]).trim(), String(data[2] || '0').trim()) ])
			]));

		return table;
	}
});

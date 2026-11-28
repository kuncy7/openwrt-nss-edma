# OpenWrt with NSS hardware offload for IPQ50xx (IPQ5018)

This is OpenWrt main with Qualcomm NSS hardware offload for IPQ5018
routers. The NSS firmware runs on top of the upstream `stmmac` ethernet
driver on kernel 6.18, and the switch keeps its DSA driver. It does not use
the out-of-tree `qca-nss-dp` and `qca-ssdk` drivers.

Offloaded: IPv4 NAT and routing through ECM, PPPoE, 802.1Q VLAN and
VLAN-aware bridges, the NSS qdiscs for SQM, and ath11k Wi-Fi offload. It is
validated on the GL.iNet GL-B3000 and the TP-Link Archer AX55 v1, and users
run it on more IPQ5018 boards.

Everything else is in **[README.ipq50xx.md](README.ipq50xx.md)**: what the
commits are, how to build, how the plane comes up, the two topologies,
[memory profiles](README.ipq50xx.md#memory-profiles), the support matrix,
the boards, and how to port another IPQ5018 board.

- Prebuilt images are on the
  [Releases page](https://github.com/kuncy7/openwrt-nss-edma/releases).
- The NSS packages (driver, ECM, qdiscs, firmware) are in the
  [kuncy7/nss-packages](https://github.com/kuncy7/nss-packages/tree/ipq50xx-rebase)
  feed, branch `ipq50xx-rebase`. `feeds.conf.default` already names it.
- Discussion and test reports are in the
  [forum thread](https://forum.openwrt.org/t/ipq5018-nss-offload-on-kernel-6-18-with-the-upstream-ethernet-stack-gl-b3000/253014).

## Where this tree comes from

The tree is a fork of Julius Bairaktaris's `nss-edma-rework`: OpenWrt main
with the vendor NSS stack (`qca-nss-drv`, ECM, the NSS qdiscs, ath11k
offload) on the upstream `qca_edma` and `qca_ppe` drivers of IPQ807x and
IPQ60xx. The IPQ5018 port is layered on it. The idea is the same, the
ethernet driver and the glue module are different.

In October 2026 that project became
[PPE-NSS](https://github.com/JuliusBairaktaris/openwrt-ppe-nss): NSS offload
without the vendor stack, with the wired traffic in the hardware PPE. Its
vendor-stack line is archived. IPQ5018 has no PPE, and its wired
acceleration is the NSS firmware itself, so this branch stays on the vendor
stack and carries that line by itself from here on. There is no upstream of
it left to sync with. The base is `nss-edma-rework` as it was archived under
the `vendor-nss-archive` tag, which sits on OpenWrt main of 9 October 2026.

The IPQ807x and IPQ60xx parts of that line are still in the tree as they
were, but nothing here builds or tests them. For those SoCs use
[openwrt-ppe-nss](https://github.com/JuliusBairaktaris/openwrt-ppe-nss).

## Branches

| Branch | What |
|---|---|
| `c3po-tag-8021q` | The default branch. Pull requests go here. |
| `ipq50xx-rebase` | Frozen on 2026-09-17. It proved that the offload works with the upstream ethernet driver. |
| `ipq50xx-nss` | Archive of the series before the rebase. |
| `julius-archive/vendor-nss-archive` | The base of the default branch: `nss-edma-rework` at its `vendor-nss-archive` tag. |
| `julius-archive/nss-edma-rework` | The final state of `nss-edma-rework`, with its archive notice. |
| `julius-archive/nss-edma-7.3-vendor-20261011` | The vendor stack with the 7.3 testing kernel. |

The feed has the matching branches (`julius-archive/vendor-nss-archive`,
`julius-archive/edma-nss`, `julius-archive/edma-nss-7.3-feed-20261011`) and
a copy of the pinned source archives in its `nss-source-tarballs` release.
[kuncy7/mac80211-nss-staging](https://github.com/kuncy7/mac80211-nss-staging)
is a fork of the repository the mac80211 NSS patch series was exported
from. The `julius-archive` branches are kept as they were and take no
changes.

## Building

```sh
git clone https://github.com/kuncy7/openwrt-nss-edma.git
cd openwrt-nss-edma
./scripts/feeds update -a && ./scripts/feeds install -a
make menuconfig   # qualcommax/ipq50xx, nss-tools-dwmac as <*>
make -j$(nproc)
```

The *Quick start* section of [README.ipq50xx.md](README.ipq50xx.md) has the
details and the checks worth doing on a fresh image.

## Acknowledgements

- [Julius Bairaktaris](https://github.com/JuliusBairaktaris) for the tree
  this is layered on, and for the idea of running NSS on the upstream
  drivers. His work can be supported through
  [GitHub Sponsors](https://github.com/sponsors/JuliusBairaktaris) or
  [PayPal](https://paypal.me/JuliusBairaktaris).
- [George Moussalem](https://github.com/georgemoussalem) for the ipq50xx
  target and the upstream stmmac and uniphy conversion.
- [Robert Marko (robimarko)](https://github.com/robimarko) for maintaining the
  OpenWrt qualcommax target.
- [qosmio](https://github.com/qosmio/openwrt-ipq) for the NSS packaging and
  firmware tarballs the feed builds on.
- Qualcomm and CodeLinaro for the open-source NSS host components.

The people who tested, reported and contributed boards are named in
[README.ipq50xx.md](README.ipq50xx.md).

## License

GPL-2.0, see [LICENSE](LICENSE). The NSS components keep their upstream
licenses.

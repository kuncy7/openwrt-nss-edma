# TP-Link Archer AX55 v1 — instalacja OpenWrt (bez lutowania)

**Dotyczy wyłącznie wariantu z przełącznikiem RTL8367S.** Egzemplarze z RTL8367D
(też sprzedawane jako „v1”) nie są obsługiwane — obraz wstanie z działającym
Wi-Fi, ale bez portów przewodowych. Wersję sprzętu poznasz dopiero po zalogowaniu
(patrz punkt 6), więc miej pod ręką ścieżkę powrotu do stocka (sekcja *Powrót do
firmware fabrycznego*).

> **Ryzyko:** modyfikujesz bootowalny system routera. Robisz to na własną
> odpowiedzialność. Powrót do firmware fabrycznego jest zawsze możliwy przez
> web-recovery (opisany na końcu) i nie wymaga lutowania.

Instalacja nie wymaga konsoli szeregowej. Cała sztuczka polega na tym,
że **stary (2022–2024) firmware TP-Linka jest w formacie MD5, nie RSA** 
— więc można wgrać zmodyfikowany obraz stocka z włączonym telnetem
przez zwykłą stronę aktualizacji, dostać roota,  
i z niego wpisać OpenWrt na flash.

---

## Co jest potrzebne

- PC z Linuksem (do rozpakowania/przepakowania firmware'u).
- Fabryczny firmware AX55 v1 w wersji **2024 (v1.3.3) lub starszej** — te są
  nieszyfrowane. Wersja 2025 (v1.5.10) jest zaszyfrowana (AES „Cloud”) i się nie
  rozpakuje; ale zmodyfikowany obraz zbudowany z 2024 wgra się nawet na
  egzemplarzu, który wyszedł z fabryki z 2025 (strona aktualizacji przyjmuje
  downgrade).
- Narzędzia: `github.com/lmadarassy/tp-link-ax55-fw-hacks` (rozpakowanie i
  przepakowanie z poprawką MD5), `ubi_reader`, `squashfs-tools`.
- Obraz OpenWrt dla AX55: `...-tplink_archer-ax55-v1-squashfs-factory.ubi`
  (z buildów, w których AX55 jest wspierany).

---

## 1. Zbuduj obraz stocka z włączonym telnetem

```sh
git clone https://github.com/lmadarassy/tp-link-ax55-fw-hacks
cd tp-link-ax55-fw-hacks
# rozpakowanie MUSI iść jako root — inaczej gubi węzeł /dev/console i obraz się
# nie zbootuje:
sudo ./01-unpack.sh sciezka/do/stock-2024.bin
```

W rozpakowanym systemie dopisz w `squashfs-root/etc/rc.local`, **przed** `exit 0`:

```sh
telnetd -l /bin/sh &
```

To daje roota na telnecie bez hasła (port 23) — nie trzeba ruszać `/etc/shadow`.
Przepakuj:

```sh
sudo ./02-repack.sh          # ustawia MD5 + flagę "Proud" (wyłącza ścieżkę RSA)
# wynik: ax55-telnet.bin
```

## 2. Wgraj zmodyfikowany firmware

Zaloguj się do panelu fabrycznego (domyślnie `192.168.0.1`), wejdź w
**Advanced → System → Firmware Upgrade** i wgraj `ax55-telnet.bin`. Router
zaakceptuje obraz (MD5, nie RSA) i zrestartuje się.

## 3. Wejdź na roota

```sh
telnet 192.168.0.1
```

Powinieneś od razu dostać `uid=0(root)`. To jest fabryczny system (Linux 4.4)
z otwartym telnetem — stąd wpiszemy OpenWrt na flash.

## 4. Przenieś obraz OpenWrt na router

Na PC (podłączonym do LAN routera, np. `192.168.0.212`) uruchom prosty serwer:

```sh
python3 -m http.server 8080
```

Na routerze (telnet):

```sh
cd /tmp
wget http://192.168.0.212:8080/openwrt-...-tplink_archer-ax55-v1-squashfs-factory.ubi -O factory.ubi
```

## 5. Wpisz OpenWrt do nieaktywnego slotu

Router ma dwa sloty systemu: `rootfs` (mtd11, slot 0) i `rootfs_1` (mtd12, slot 1).
Sprawdź, z którego aktualnie bootuje:

```sh
cat /proc/cmdline        # zobaczysz ubi.mtd=rootfs_1  → bootuje ze slotu 1
```

Pisz do **drugiego** slotu (jeśli bootuje ze slotu 1 → piszesz do mtd11), żeby
stock został jako awaryjny fallback:

```sh
mtd erase /dev/mtd11
mtd write /tmp/factory.ubi /dev/mtd11
fw_setenv tp_boot_idx 0
fw_setenv config_name config@mp03.3
reboot
```

> **Uwaga:** używaj `mtd write`, **nie** `ubiformat` — fabryczny `ubiformat` na tym
> routerze się wywala (błąd arytmetyczny). I wpisuj plik **factory.ubi**, nie
> sysupgrade.bin.

Po restarcie router wstaje na OpenWrt: Wi-Fi „OpenWrt”, adres `192.168.1.1`
(obraz z pełnym wsparciem daje od razu wszystkie 5 portów i oba radia).

## 6. Sprawdź wariant przełącznika

```sh
dmesg | grep rtl8365
```

- `found an RTL8367S switch` → masz obsługiwany wariant, wszystko działa.
- brak / błąd probe → to egzemplarz z **RTL8367D**: Wi-Fi działa, portów
  przewodowych nie ma i (na razie) nie będzie. Wróć do stocka (niżej).

## Aktualizacje

Kiedy jesteś już na OpenWrt, kolejne wersje wgrywaj **normalnym sysupgrade**
(LuCI → System → Backup/Flash Firmware, albo `sysupgrade` z CLI) — cała
zabawa z telnetem jest jednorazowa.

---

## Powrót do firmware fabrycznego (bez lutowania)

Web-recovery przyjmuje **wyłącznie oryginalny, podpisany firmware TP-Linka**
(sprawdza podpis RSA), więc jest to pewna droga odzysku:

1. Wyłącz router.
2. Przytrzymaj **Reset** i włącz zasilanie; trzymaj ~10 s.
3. Ustaw PC na stały adres `192.168.0.10`, wejdź w przeglądarce na
   `http://192.168.0.1`.
4. Wgraj oficjalny firmware `.bin` ze strony TP-Linka.

To odtwarza fabryczny system. Instalację OpenWrt można potem powtórzyć od
punktu 2 (obraz telnet nadal jest zaakceptowany przez stronę aktualizacji).

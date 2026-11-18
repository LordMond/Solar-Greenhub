#!/usr/bin/env python3
"""Echte Datenquellen für Solar und Akkus: VE.Direct (Victron) und Modbus RTU (RS485-USB-Adapter).

Diese Datei verbindet die Oberfläche mit der echten Anlage. Sie liefert dieselben Messwerte
(PowerReading), die auch die Simulation (power.SimPower) erzeugt. Dadurch ändert sich in der
Oberfläche und im Schutz (PowerManager) nichts, wenn man von der Simulation auf Hardware wechselt.

Benötigt: pip install pyserial   (Ubuntu/Raspberry Pi: sudo apt install python3-serial)
Einstellungen: hardware.toml im Projektordner (Vorlage: hardware.example.toml). Dort stehen
Anzahl der Panels/Akkus, die Schnittstellen (/dev/serial/by-id/...) und bei Modbus die Register.

Zwei Wege, die man mit --power wählt:

    vedirect   Victron-Geräte über das VE.Direct-Textprotokoll (19200 Baud). Pro Solarregler ein
               USB-Kabel. Akkus über BMV/SmartShunt: ein Wächter pro Akku (ergibt "Akku 1..4"),
               oder einer für die ganze Bank, oder keiner (dann nur Näherung aus den Reglern).
    modbus     Beliebige Geräte, die Modbus RTU über RS485 sprechen. Welche Werte in welchem Register
               stehen, trägt man in hardware.toml ein (steht im Datenblatt des Geräts). Jeder Akku
               kann eine eigene Geräteadresse haben.

Wie die Daten ankommen (wichtig fürs Verständnis): Beide Quellen lesen im HINTERGRUND in eigenen
Threads und merken sich den letzten gültigen Wert. read() liefert nur diesen Wert und wartet nie
auf die Schnittstelle. Dadurch kann die Oberfläche nicht hängen, wenn ein Kabel wackelt. Ist der
Wert älter als einige Sekunden, wirft read() SourceError; der PowerManager erkennt den Datenausfall
und sperrt nach 30 s die Steckdosen.

Aufbau der Datei:

    Hilfen           soc_from_voltage, EnergyCounter, battery_soc_estimate, reading_from_packs
    VE.Direct        VeDirectParser (Protokoll), VeDirectReader (Thread je Port), VeDirectPower
    Modbus           crc16/build_request/parse_response (Protokoll), ModbusRtuClient (Port),
                     Reg (ein Register), ModbusPower (Thread, liest alle Register)
    Konfiguration    load_power_config und build_source lesen hardware.toml

Neue Hardware anbinden: Eine Klasse von PowerSource ableiten, read() liefert ein PowerReading
(und wirft SourceError bei fehlenden Daten); danach in build_source() eintragen.
"""
from __future__ import annotations

import logging
import math
import struct
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from power import (BatteryPackReading, PowerConfig, PowerReading, PowerSource, SourceError,
                   StringReading, bank_summary)

log = logging.getLogger("greenhouse.hw")


# --------------------------------------------------------------------------
# Gemeinsame Helfer
# --------------------------------------------------------------------------
def soc_from_voltage(v: float) -> float:
    """Grobe Näherung für 48-V-LiFePO4 (16s). Nur Notbehelf ohne echten Ladezustand."""
    return max(0.0, min(100.0, (v - 46.0) / 8.0 * 100.0))


class EnergyCounter:
    """Tagesertrag selbst aufsummieren, falls das Gerät ihn nicht liefert."""

    def __init__(self) -> None:
        self.wh, self.last, self.day = 0.0, time.monotonic(), time.localtime().tm_yday

    def add(self, power_w: float) -> float:
        """Leistung (Watt) seit dem letzten Aufruf in Energie umrechnen und aufsummieren; gibt kWh zurück."""
        now, day = time.monotonic(), time.localtime().tm_yday
        if day != self.day:
            self.day, self.wh = day, 0.0
        self.wh += power_w * (now - self.last) / 3600.0
        self.last = now
        return self.wh / 1000.0


def battery_soc_estimate(v: float, pcfg: PowerConfig) -> float:
    """Näherung aus der Spannung EINES Akkus (in Reihe: 4 x 12 V -> auf 48 V hochrechnen)."""
    pack_v = v * pcfg.batteries if pcfg.battery_layout == "series" else v
    return soc_from_voltage(pack_v)


def reading_from_packs(strings, energy_kwh: float, packs, layout: str, load_w: float | None = None) -> PowerReading:
    """Baut den Gesamt-Messwert aus den Einzel-Akkus (Reihe/parallel siehe bank_summary)."""
    bv, bi, soc, temp = bank_summary(packs, layout)
    if load_w is None:
        load_w = max(0.0, sum(s.power_w for s in strings) - bv * bi)
    return PowerReading(
        strings=tuple(strings),
        solar_energy_today_kwh=round(energy_kwh, 2),
        battery_voltage_v=round(bv, 1),
        battery_current_a=round(bi, 1),
        battery_soc_pct=round(soc, 1),
        battery_temp_c=round(temp, 1) if not math.isnan(temp) else math.nan,
        load_w=round(load_w),
        battery_packs=tuple(packs),
    )


# --------------------------------------------------------------------------
# VE.Direct (Victron, Textprotokoll, 19200 Baud)
# --------------------------------------------------------------------------
class VeDirectParser:
    """Zustandsautomat für das VE.Direct-Textprotokoll von Victron (inklusive Prüfsumme).

    Das Gerät sendet etwa einmal pro Sekunde einen "Frame": mehrere Zeilen nach dem Muster
    (Zeilenumbruch) Bezeichnung (Tabulator) Wert, z.B. V = Batteriespannung in mV, I = Strom in mA,
    VPV = Panelspannung in mV, PPV = Panelleistung in W, H20 = Ertrag heute in 0,01 kWh,
    SOC = Ladezustand in Promille, T = Temperatur in °C. Den Abschluss bildet die Zeile
    "Checksum" mit EINEM Prüfbyte: Die Summe aller Bytes des Frames (inklusive dieses Bytes)
    muss modulo 256 genau 0 ergeben, sonst wird der Frame verworfen (Übertragungsfehler).
    Nachrichten, die mit ':' beginnen (HEX-Protokoll), werden ignoriert.

    Der Parser bekommt beliebige Bytes-Häppchen (so wie sie von der Schnittstelle kommen) und meldet
    vollständige, gültige Frames. Nach einem Fehler oder Müll am Anfang synchronisiert er sich
    beim nächsten Frame von selbst wieder.
    """

    WAIT, KEY, VALUE, CHECKSUM, HEX = range(5)

    def __init__(self) -> None:
        self.state = self.WAIT
        self.key, self.value = bytearray(), bytearray()
        self.sum = 0
        self.fields: dict[str, str] = {}

    def feed(self, data: bytes) -> list[dict[str, str]]:
        """Füttert Rohbytes ein, liefert alle vollständigen, gültigen Frames."""
        frames = []
        for b in data:
            if b == 0x3A and self.state != self.CHECKSUM:  # ':' = HEX-Nachricht, ignorieren
                self.state = self.HEX
                continue
            if self.state == self.HEX:
                if b == 0x0A:
                    self.state = self.WAIT
                continue
            self.sum = (self.sum + b) & 0xFF
            if self.state == self.WAIT:
                if b == 0x0A:
                    self.state = self.KEY
            elif self.state == self.KEY:
                if b == 0x09:
                    self.state = self.CHECKSUM if bytes(self.key) == b"Checksum" else self.VALUE
                else:
                    self.key.append(b)
            elif self.state == self.VALUE:
                if b == 0x0D:
                    self.fields[self.key.decode("ascii", "replace")] = self.value.decode("ascii", "replace")
                    self.key.clear()
                    self.value.clear()
                    self.state = self.WAIT
                else:
                    self.value.append(b)
            else:  # CHECKSUM
                if self.sum == 0:
                    frames.append(self.fields)
                else:
                    log.debug("VE.Direct: Prüfsumme falsch, Frame verworfen")
                self.fields = {}
                self.key.clear()
                self.value.clear()
                self.sum, self.state = 0, self.WAIT
        return frames


class VeDirectReader:
    """Liest einen VE.Direct-Anschluss dauerhaft im Hintergrund (eigener Thread) und merkt sich den
    letzten gültigen Frame.

    Bricht die Verbindung ab (Kabel gezogen), versucht der Thread nach 3 Sekunden automatisch neu zu
    verbinden. latest() liefert den Frame oder wirft SourceError, wenn er zu alt ist.
    """

    def __init__(self, port: str, max_age_s: float = 5.0) -> None:
        self.port, self.max_age_s = port, max_age_s
        self._frame: dict[str, str] | None = None
        self._stamp = 0.0
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True, name=f"vedirect-{port}").start()

    def _run(self) -> None:
        """Thread-Schleife: Port öffnen, Bytes lesen, Frames parsen, bei Fehlern neu verbinden."""
        import serial

        parser = VeDirectParser()
        while True:
            try:
                with serial.Serial(self.port, 19200, timeout=1.0) as ser:
                    log.info("VE.Direct verbunden: %s", self.port)
                    while True:
                        for frame in parser.feed(ser.read(max(1, ser.in_waiting))):
                            with self._lock:
                                self._frame, self._stamp = frame, time.monotonic()
            except Exception as e:  # SerialException, OSError, ...
                log.warning("VE.Direct %s: %s – neuer Versuch in 3 s", self.port, e)
                time.sleep(3)

    def latest(self) -> dict[str, str]:
        """Letzter gültiger Frame; wirft SourceError, wenn keiner da oder älter als max_age_s ist."""
        with self._lock:
            frame, stamp = self._frame, self._stamp
        if frame is None or time.monotonic() - stamp > self.max_age_s:
            raise SourceError(f"VE.Direct {self.port}: keine aktuellen Daten")
        return frame


def _num(frame: dict[str, str], key: str, scale: float = 1.0, default: float | None = None) -> float | None:
    """Zahl aus einem Frame; '---' (nicht verfügbar) und fehlende Felder ergeben default."""
    try:
        return float(frame[key]) * scale
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class VeDirectConfig:
    """Einstellungen für VE.Direct aus hardware.toml, Abschnitt [vedirect].

        mppt_ports  ein Anschluss pro Solarregler, in der Reihenfolge Panel 1, Panel 2, ...
        bmv_ports   Batteriewächter: ENTWEDER einer pro Akku (so viele wie batteries) ODER einer für
                    die ganze Bank (ein Eintrag, dann nur Gesamtwerte) ODER leer
    """
    mppt_ports: tuple[str, ...] = ()   # ein Solarregler (MPPT) pro Panel/String
    bmv_ports: tuple[str, ...] = ()    # Batteriewächter: einer pro Akku ODER einer für die ganze Bank


class VeDirectPower(PowerSource):
    """Solar über MPPT-Solarregler, Akkus über Batteriewächter (BMV/SmartShunt) von Victron.

    Je nach Anzahl der Batteriewächter (bmv_ports) gibt es drei Betriebsarten:

        einer pro Akku   Einzelwerte aller Akkus (Anzeige "Akku 1..n"); die Bank wird nach der
                         Verschaltung (Reihe/parallel) daraus berechnet
        ein Wächter      nur Gesamtwerte der Bank
        keiner           Näherung aus den Solarreglern: Batteriespannung und Ladestrom stehen auch
                         in deren Daten; der Ladezustand wird nur grob aus der Spannung geschätzt

    Der Verbrauch wird als Differenz berechnet: Solarleistung minus Leistung an der Bank.
    """

    def __init__(self, cfg: VeDirectConfig, pcfg: PowerConfig, mppts=None, bmvs=None) -> None:
        if not cfg.mppt_ports and mppts is None:
            raise ValueError("hardware.toml: [vedirect] mppt_ports ist leer")
        self.pcfg = pcfg
        self.mppts = mppts if mppts is not None else [VeDirectReader(p) for p in cfg.mppt_ports]
        self.bmvs = bmvs if bmvs is not None else [VeDirectReader(p) for p in cfg.bmv_ports]
        if len(self.bmvs) not in (0, 1, pcfg.batteries):
            raise ValueError(f"hardware.toml: bmv_ports braucht 0, 1 oder {pcfg.batteries} Einträge")

    def read(self) -> PowerReading:
        """Setzt aus den letzten Frames aller Geräte ein PowerReading zusammen.

        Wirft SourceError, wenn ein Gerät keine aktuellen Daten hat oder Spannung/Strom fehlen. Felder, die
        das Gerät als "---" (nicht verfügbar) meldet, z.B. Ladezustand vor der ersten Synchronisation des
        BMV, werden durch eine Schätzung aus der Spannung ersetzt.
        """
        frames = [d.latest() for d in self.mppts]
        strings, energy_kwh, charge_a = [], 0.0, 0.0
        for f in frames:
            vpv, ppv = _num(f, "VPV", 0.001, 0.0), _num(f, "PPV", 1.0, 0.0)
            strings.append(StringReading(round(vpv, 1), round(ppv / vpv, 2) if vpv > 1 else 0.0))
            energy_kwh += _num(f, "H20", 0.01, 0.0)   # Ertrag heute
            charge_a += _num(f, "I", 0.001, 0.0)      # Ladestrom in den Akku
        solar_w = sum(s.power_w for s in strings)

        if len(self.bmvs) == self.pcfg.batteries:      # Einzelwerte je Akku
            packs = []
            for n, d in enumerate(self.bmvs, 1):
                b = d.latest()
                v, i = _num(b, "V", 0.001), _num(b, "I", 0.001)
                if v is None or i is None:
                    raise SourceError(f"BMV {n}: Spannung/Strom fehlen")
                soc, temp = _num(b, "SOC", 0.1), _num(b, "T")
                if soc is None:  # z.B. noch nicht synchronisiert ("---")
                    soc = battery_soc_estimate(v, self.pcfg)
                packs.append(BatteryPackReading(f"Akku {n}", round(v, 2), round(soc, 1),
                                                round(temp, 1) if temp is not None else math.nan, round(i, 1)))
            return reading_from_packs(strings, energy_kwh, packs, self.pcfg.battery_layout)

        if self.bmvs:                                   # ein Wächter für die ganze Bank
            b = self.bmvs[0].latest()
            v, i, soc, temp = _num(b, "V", 0.001), _num(b, "I", 0.001), _num(b, "SOC", 0.1), _num(b, "T")
        else:                                           # Näherung aus den Solarreglern
            v, i, soc, temp = _num(frames[0], "V", 0.001), charge_a, None, None
        if v is None or i is None:
            raise SourceError("Batteriewächter/MPPT: Spannung oder Strom fehlt")
        if soc is None:
            soc = soc_from_voltage(v)
        return PowerReading(
            strings=tuple(strings),
            solar_energy_today_kwh=round(energy_kwh, 2),
            battery_voltage_v=round(v, 1),
            battery_current_a=round(i, 1),
            battery_soc_pct=round(soc, 1),
            battery_temp_c=round(temp, 1) if temp is not None else math.nan,
            load_w=round(max(0.0, solar_w - v * i)),
        )


# --------------------------------------------------------------------------
# Modbus RTU über RS485-USB (eigene schlanke Umsetzung, nur pyserial nötig)
# --------------------------------------------------------------------------
def crc16(data: bytes) -> int:
    """CRC-16 nach Modbus (Startwert 0xFFFF, Polynom 0xA001). Prüfsumme am Ende jedes Modbus-RTU-Telegramms."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_request(unit: int, function: int, address: int, count: int) -> bytes:
    """Baut eine Modbus-Leseanfrage: Geräteadresse, Funktion (3 = Holding-, 4 = Input-Register),
    Startadresse, Anzahl Register, danach die CRC (niedriges Byte zuerst).
    """
    body = struct.pack(">BBHH", unit, function, address, count)
    return body + struct.pack("<H", crc16(body))


def parse_response(frame: bytes, unit: int, function: int, count: int) -> list[int]:
    """Prüft eine Modbus-Antwort und liefert die Register als Liste von 16-Bit-Zahlen.

    Wirft SourceError bei: zu kurzer Antwort, falscher CRC (Übertragungsfehler), falscher Geräteadresse,
    Fehlermeldung des Geräts (Funktionscode mit gesetztem Bit 0x80, z.B. "ungültige Adresse") oder
    unerwartetem Format.
    """
    if len(frame) < 5:
        raise SourceError("Modbus: Antwort zu kurz")
    if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
        raise SourceError("Modbus: CRC-Fehler")
    if frame[0] != unit:
        raise SourceError(f"Modbus: Antwort von Gerät {frame[0]} statt {unit}")
    if frame[1] == function | 0x80:
        raise SourceError(f"Modbus: Gerät meldet Fehlercode {frame[2]}")
    if frame[1] != function or frame[2] != 2 * count or len(frame) != 5 + 2 * count:
        raise SourceError("Modbus: unerwartete Antwort")
    return list(struct.unpack(f">{count}H", frame[3:3 + 2 * count]))


class ModbusRtuClient:
    """Liest Modbus-RTU-Register über eine serielle Schnittstelle (RS485-USB-Adapter).

    Eigene, schlanke Umsetzung auf Basis von pyserial, damit keine zusätzliche Modbus-Bibliothek
    nötig ist (deren Schnittstelle ändert sich je nach Version). Das Objekt ist thread-sicher: Ein Lock
    sorgt dafür, dass immer nur eine Anfrage gleichzeitig den Bus belegt. ser ist ein geöffnetes
    pyserial-Objekt (oder ein Ersatz im Test).
    """

    def __init__(self, ser) -> None:
        self._ser, self._lock = ser, threading.Lock()

    @classmethod
    def open(cls, port: str, baudrate: int = 9600, parity: str = "N", stopbits: int = 1,
             timeout_s: float = 0.5) -> "ModbusRtuClient":
        """Öffnet die serielle Schnittstelle mit 8 Datenbits und den Werten aus hardware.toml."""
        import serial

        return cls(serial.Serial(port, baudrate, bytesize=8, parity=parity, stopbits=stopbits, timeout=timeout_s))

    def read(self, unit: int, function: int, address: int, count: int) -> list[int]:
        """Liest count Register ab address vom Gerät unit und liefert sie als Liste.

        Ablauf: Eingangspuffer leeren, Anfrage senden, die ersten 5 Bytes lesen (reicht für eine Fehler-
        antwort), bei normaler Antwort den Rest lesen, dann prüfen. Wirft SourceError bei Zeitüberschreitung
        oder fehlerhafter Antwort.
        """
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(build_request(unit, function, address, count))
            head = self._ser.read(5)
            if len(head) < 5:
                raise SourceError("Modbus: keine Antwort (Zeitüberschreitung)")
            frame = head if head[1] & 0x80 else head + self._ser.read(2 * count)
        return parse_response(frame, unit, function, count)


@dataclass(frozen=True)
class Reg:
    """Ein Modbus-Register: wo es liegt und wie der Rohwert in eine Messgröße umgerechnet wird.

        address   Registeradresse (steht im Datenblatt)
        scale     Rohwert mal scale ergibt den Messwert (0.1 bei "Wert in 0,1 V"; negativ dreht das
                  Vorzeichen, falls das Gerät "Entladen" positiv meldet)
        signed    True, wenn der Rohwert eine Zahl mit Vorzeichen ist (Zweierkomplement)
        function  3 = Holding-Register, 4 = Input-Register
        count     1 = 16-Bit-Wert, 2 = 32-Bit-Wert (höheres Wort zuerst)
        unit      Geräteadresse am Bus; bei mehreren Akkus hat jeder oft seine eigene
    """

    address: int
    scale: float = 1.0       # Rohwert * scale = Messwert (negativ, um das Vorzeichen zu drehen)
    signed: bool = False
    function: int = 3        # 3 = Holding-Register, 4 = Input-Register
    count: int = 1           # 1 = 16 Bit, 2 = 32 Bit (High-Word zuerst)
    unit: int = 1            # Geräteadresse (Slave-ID)

    def decode(self, words: list[int]) -> float:
        """Wandelt die gelesenen 16-Bit-Wörter in die Messgröße um (inkl. Vorzeichen und Skalierung)."""
        raw = 0
        for w in words:
            raw = (raw << 16) | w
        bits = 16 * len(words)
        if self.signed and raw >= 1 << (bits - 1):
            raw -= 1 << bits
        return raw * self.scale


@dataclass(frozen=True)
class ModbusConfig:
    """Einstellungen für Modbus aus hardware.toml, Abschnitt [modbus]: Schnittstelle (port), Baudrate,
    Parität, Stoppbits, Abfrageabstand (poll_s), Zeitlimit je Anfrage (timeout_s) und die Registertabelle
    regs (Name der Messgröße, zugeordnetes Reg).
    """
    port: str
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    poll_s: float = 1.0
    timeout_s: float = 0.5
    regs: dict[str, Reg] = field(default_factory=dict)


class ModbusPower(PowerSource):
    """Solar und Akkus über Modbus RTU. Ein Hintergrund-Thread liest alle Register reihum und merkt sich
    das Ergebnis; read() liefert nur diesen Wert.

    Pflicht-Register in hardware.toml:
        string<n>_voltage, string<n>_current       je Panel (V, A)
      und entweder
        battery<n>_voltage, battery<n>_current     je Akku (A positiv = lädt), optional
                                                   battery<n>_soc und battery<n>_temp
      oder für die ganze Bank
        battery_voltage, battery_current           optional battery_soc und battery_temp
    Optional zusätzlich: energy_today (kWh), load_power (W).

    Fehlende optionale Register werden berechnet bzw. geschätzt (Ertrag durch Aufsummieren, Verbrauch
    als Differenz, Ladezustand grob aus der Spannung). Fehlt ein Pflichtregister, bricht der Start
    mit einer klaren Meldung ab, welche Einträge in hardware.toml fehlen.
    """

    def __init__(self, cfg: ModbusConfig, pcfg: PowerConfig, client: ModbusRtuClient | None = None,
                 start: bool = True) -> None:
        self.cfg, self.pcfg = cfg, pcfg
        self.per_pack = "battery1_voltage" in cfg.regs
        need: list[str] = []
        if self.per_pack:
            for n in range(1, pcfg.batteries + 1):
                need += [f"battery{n}_voltage", f"battery{n}_current"]
        else:
            need += ["battery_voltage", "battery_current"]
        for n in range(1, pcfg.strings + 1):
            need += [f"string{n}_voltage", f"string{n}_current"]
        missing = [name for name in need if name not in cfg.regs]
        if missing:
            raise ValueError("hardware.toml: Register fehlen: " + ", ".join(missing))
        self.client = client or ModbusRtuClient.open(cfg.port, cfg.baudrate, cfg.parity, cfg.stopbits, cfg.timeout_s)
        self._energy = EnergyCounter()
        self._latest: PowerReading | None = None
        self._stamp = 0.0
        self._lock = threading.Lock()
        if start:
            threading.Thread(target=self._loop, daemon=True, name="modbus").start()

    def _get(self, name: str) -> float | None:
        """Liest ein benanntes Register (None, wenn es nicht konfiguriert ist)."""
        reg = self.cfg.regs.get(name)
        if reg is None:
            return None
        return reg.decode(self.client.read(reg.unit, reg.function, reg.address, reg.count))

    def _poll(self) -> PowerReading:
        """Liest alle Register einmal und baut daraus ein PowerReading (Einzel-Akkus oder Bank)."""
        strings = [StringReading(round(self._get(f"string{n}_voltage"), 1), round(self._get(f"string{n}_current"), 2))
                   for n in range(1, self.pcfg.strings + 1)]
        solar_w = sum(s.power_w for s in strings)
        energy = self._get("energy_today")
        if energy is None:
            energy = self._energy.add(solar_w)
        load = self._get("load_power")

        if self.per_pack:
            packs = []
            for n in range(1, self.pcfg.batteries + 1):
                v, i = self._get(f"battery{n}_voltage"), self._get(f"battery{n}_current")
                soc, temp = self._get(f"battery{n}_soc"), self._get(f"battery{n}_temp")
                if soc is None:
                    soc = battery_soc_estimate(v, self.pcfg)
                packs.append(BatteryPackReading(f"Akku {n}", round(v, 2), round(soc, 1),
                                                round(temp, 1) if temp is not None else math.nan, round(i, 1)))
            return reading_from_packs(strings, energy, packs, self.pcfg.battery_layout, load)

        v, i = self._get("battery_voltage"), self._get("battery_current")
        soc, temp = self._get("battery_soc"), self._get("battery_temp")
        return PowerReading(
            strings=tuple(strings),
            solar_energy_today_kwh=round(energy, 2),
            battery_voltage_v=round(v, 1),
            battery_current_a=round(i, 1),
            battery_soc_pct=round(soc if soc is not None else soc_from_voltage(v), 1),
            battery_temp_c=round(temp, 1) if temp is not None else math.nan,
            load_w=round(load if load is not None else max(0.0, solar_w - v * i)),
        )

    def _loop(self) -> None:
        """Thread-Schleife: pollen, Ergebnis speichern, warten. Fehler werden nur beim ersten Mal geloggt
        (und die Wiederkehr der Verbindung), damit das Log nicht volläuft.
        """
        warned = False
        while True:
            try:
                r = self._poll()
                with self._lock:
                    self._latest, self._stamp = r, time.monotonic()
                if warned:
                    log.info("Modbus: Verbindung wieder da")
                    warned = False
            except Exception as e:
                if not warned:
                    log.warning("Modbus: %s", e)
                    warned = True
            time.sleep(self.cfg.poll_s)

    def read(self) -> PowerReading:
        """Letzter gültiger Wert; wirft SourceError, wenn keiner da oder älter als einige Sekunden."""
        with self._lock:
            r, stamp = self._latest, self._stamp
        if r is None or time.monotonic() - stamp > max(5.0, 5 * self.cfg.poll_s):
            raise SourceError("Modbus: keine aktuellen Daten")
        return r


# --------------------------------------------------------------------------
# Einstellungen aus hardware.toml
# --------------------------------------------------------------------------
def load_power_config(path: str) -> PowerConfig:
    """[power]-Abschnitt aus hardware.toml; ohne Datei gelten die Standardwerte."""
    import tomllib

    p = Path(path)
    if not p.exists():
        return PowerConfig()
    data = dict(tomllib.loads(p.read_text(encoding="utf-8")).get("power", {}))
    if "capacity_wh" in data:  # alter Schlüssel (Gesamtkapazität) -> Kapazität pro Akku
        total = data.pop("capacity_wh")
        data.setdefault("battery_capacity_wh", total / data.get("batteries", PowerConfig().batteries))
    if "durations_min" in data:
        data["durations_min"] = tuple(data["durations_min"])
    return replace(PowerConfig(), **data)


def build_source(kind: str, path: str, pcfg: PowerConfig) -> PowerSource:
    """Erzeugt aus hardware.toml die gewünschte Datenquelle: kind ist "vedirect" oder "modbus".

    Fehlt die Datei, bricht das Programm mit einem Hinweis ab (Vorlage kopieren und anpassen). Alte
    Schlüssel aus früheren Versionen (bmv_port) werden weiter verstanden.
    """
    import tomllib

    p = Path(path)
    if not p.exists():
        raise SystemExit(f"{path} fehlt – hardware.example.toml kopieren und anpassen")
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    if kind == "vedirect":
        v = data.get("vedirect", {})
        bmvs = tuple(v.get("bmv_ports", ()))
        if not bmvs and v.get("bmv_port"):  # alter Schlüssel: ein Wächter für die Bank
            bmvs = (v["bmv_port"],)
        return VeDirectPower(VeDirectConfig(tuple(v.get("mppt_ports", ())), bmvs), pcfg)
    if kind == "modbus":
        m = data.get("modbus", {})
        regs = {name: Reg(**d) for name, d in m.get("regs", {}).items()}
        return ModbusPower(ModbusConfig(
            port=m["port"], baudrate=m.get("baudrate", 9600), parity=m.get("parity", "N"),
            stopbits=m.get("stopbits", 1), poll_s=m.get("poll_s", 1.0),
            timeout_s=m.get("timeout_s", 0.5), regs=regs), pcfg)
    raise ValueError(f"Unbekannte Quelle: {kind}")

#!/usr/bin/env python3
"""Solar, Akkus und 230-V-Steckdosen: Datenmodell, Simulation und Schutzlogik (48-V-System).

Diese Datei beschreibt die "Energieseite" des Projekts, getrennt von der Gewächshaus-Regelung
in greenhouse.py. Sie enthält bewusst KEINE Oberfläche und keine Hardware-Zugriffe auf Solarregler
(die stehen in power_hw.py). Dadurch lässt sich alles ohne Anlage testen.

Der Datenfluss in einem Satz:

    PowerSource.read()  ->  PowerReading  ->  PowerManager.step()  ->  Oberfläche / Steckdosen

Die Bausteine:

    Messwerte    PowerReading (alles zu einem Zeitpunkt), darin StringReading (ein Solar-Panel)
                 und BatteryPackReading (ein einzelner Akku); bank_summary() rechnet aus den
                 Einzel-Akkus die Werte der ganzen Bank
    Konfiguration  PowerConfig (Anzahl Panels/Akkus, Kapazität, Abschaltgrenzen, Timer-Auswahl)
    Steckdosen   GpioRelay (Relais am Raspberry Pi) und Outlet (Steckdose mit Abschalt-Timer)
    Datenquellen PowerSource ist die Schnittstelle. SimPower liefert simulierte Werte; echte Werte
                 kommen aus power_hw.py (VE.Direct, Modbus)
    Schutz       PowerManager liest die Quelle, überwacht den Akku und sperrt die Steckdosen

Das Akku-System: vier 12-V-Akkus in Reihe ergeben die 48-V-Bank (battery_layout = "series").
In Reihe fließt durch alle Akkus derselbe Strom, die Spannungen addieren sich. Deshalb entscheidet
der schwächste Akku über den Ladezustand der Bank; ein einzelner leerer Akku würde sonst im
Mittelwert untergehen. Vorzeichen-Konvention überall: Strom und Leistung positiv = Akku wird
GELADEN, negativ = Akku wird ENTLADEN.

Die Steckdosen laufen nie dauerhaft: Jede Steckdose bekommt beim Einschalten einen Timer und
schaltet danach von selbst wieder ab (siehe Outlet). Zusätzlich gibt der PowerManager sie nur
frei, wenn der Akku ausreichend geladen ist UND aktuelle Messdaten vorliegen.

Die Simulation (SimPower) lässt sich über das Simulations-Labor (sim_panel.py) beeinflussen:
Wetter, Tageszeit, Zusatzlast und Akku-Ladestand.
"""
from __future__ import annotations

import logging
import math
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from greenhouse import Actuator

log = logging.getLogger("greenhouse.power")


class SourceError(Exception):
    """Keine (aktuellen) Messdaten von Solarregler/Akku."""


@dataclass(frozen=True)
class PowerConfig:
    """Einstellungen für Solar, Akkus und Steckdosen (unveränderlich nach dem Anlegen).

    Normalerweise kommen die Werte aus dem Abschnitt [power] der Datei hardware.toml
    (siehe power_hw.load_power_config); ohne Datei gelten die Standardwerte hier.

        strings               Anzahl der Solar-Eingänge (Panels bzw. Strings, je ein Solarregler)
        string_peak_w         Spitzenleistung eines Panels in Watt (Maß für die Balkenlänge in der Anzeige
                              und für die Simulation)
        batteries             Anzahl der Akkus (hier 4)
        battery_capacity_wh   nutzbare Energie PRO Akku (12 V x ca. 146 Ah = 1750 Wh)
        battery_layout        "series" (4 x 12 V in Reihe = 48 V) oder "parallel" (mehrere 48-V-Akkus
                              nebeneinander); bestimmt, wie aus den Einzel-Akkus die Bank berechnet wird
        soc_cutoff_pct        fällt der SCHWÄCHSTE Akku darunter, werden die Steckdosen abgeschaltet
        soc_resume_pct        erst darüber werden sie wieder freigegeben (Hysterese: 20 % aus, 30 % an,
                              damit sie nicht ständig an- und ausgehen, wenn der Wert um die Grenze pendelt)
        durations_min         Timer-Auswahl für die Steckdosen in Minuten (Knöpfe in der Oberfläche)
    """
    strings: int = 2
    string_peak_w: float = 600.0
    batteries: int = 4                    # Anzahl Akkus
    battery_capacity_wh: float = 1750.0   # nutzbare Kapazität pro Akku (12 V x ca. 146 Ah)
    battery_layout: str = "series"        # "series" = in Reihe (4 x 12 V = 48 V), "parallel"
    soc_cutoff_pct: float = 20.0
    soc_resume_pct: float = 30.0
    durations_min: tuple[int, ...] = (15, 30, 60, 120)

    def __post_init__(self) -> None:
        """Prüft die Eingabe direkt beim Anlegen: ein falscher Layout-Name fällt sofort auf."""
        if self.battery_layout not in ("series", "parallel"):
            raise ValueError("battery_layout muss 'series' oder 'parallel' sein")

    @property
    def capacity_wh(self) -> float:
        """Gesamt-Kapazität aller Akkus (Energie addiert sich in Reihe wie parallel)."""
        return self.batteries * self.battery_capacity_wh


@dataclass(frozen=True)
class StringReading:
    """Messwerte eines Solar-Panels (bzw. Strings) am Eingang des Solarreglers.

    Spannung und Strom sind die Werte auf der Panel-Seite (hier ca. 85-95 V), nicht die der Akkus.
    """
    voltage_v: float
    current_a: float

    @property
    def power_w(self) -> float:
        """Leistung des Panels in Watt (Spannung mal Strom)."""
        return self.voltage_v * self.current_a


@dataclass(frozen=True)
class BatteryPackReading:
    """Messwerte eines einzelnen Akkus ("Akku 1" bis "Akku n").

    voltage_v      Spannung dieses Akkus (bei 12-V-Akkus in Reihe: ca. 12-13,5 V)
    soc_pct        Ladezustand in Prozent
    temp_c         Temperatur in Grad Celsius; math.nan, wenn nicht gemessen wird
    current_a      Strom in Ampere (positiv = lädt), None, wenn nicht gemessen
    """
    name: str
    voltage_v: float
    soc_pct: float
    temp_c: float
    current_a: float | None = None


@dataclass(frozen=True)
class PowerReading:
    """Alle Solar-/Akku-Messwerte zu EINEM Zeitpunkt. Das ist das Datenpaket, das die Quellen
    (SimPower, VeDirectPower, ModbusPower) liefern und das die Oberfläche anzeigt.

        strings                  ein StringReading pro Panel
        solar_energy_today_kwh   Ertrag seit Tagesbeginn in kWh
        battery_*                Werte der GANZEN Akku-Bank (Spannung, Strom, Ladezustand, Temperatur)
        load_w                   Gesamtverbrauch in Watt (Gewächshaus, Steckdosen, Wechselrichter-Grundlast)
        outlet_loads_w           Leistung je Steckdose (leer, wenn nicht gemessen)
        battery_packs            Einzelwerte je Akku (leer, wenn die Quelle nur Gesamtwerte liefert)

    Vorzeichen: battery_current_a und battery_power_w sind positiv beim Laden, negativ beim Entladen.
    """
    strings: tuple[StringReading, ...]
    solar_energy_today_kwh: float
    battery_voltage_v: float
    battery_current_a: float
    battery_soc_pct: float
    battery_temp_c: float
    load_w: float
    outlet_loads_w: tuple[float, ...] = ()
    battery_packs: tuple[BatteryPackReading, ...] = ()

    @property
    def solar_power_w(self) -> float:
        """Gesamte Solarleistung aller Panels in Watt."""
        return sum(s.power_w for s in self.strings)

    @property
    def battery_power_w(self) -> float:
        """Leistung an der Akku-Bank in Watt: positiv = lädt, negativ = entlädt."""
        return self.battery_voltage_v * self.battery_current_a

    @property
    def lowest_soc_pct(self) -> float:
        """Schwächster Akku (für den Schutz), sonst der Gesamtwert."""
        return min((p.soc_pct for p in self.battery_packs), default=self.battery_soc_pct)


def bank_summary(packs, layout: str) -> tuple[float, float, float, float]:
    """Gesamtwerte der Akku-Bank aus den Einzel-Akkus: (Spannung, Strom, Ladezustand, Temperatur).

    Der Zusammenhang hängt von der Verschaltung ab:

        series    (Reihe, 4 x 12 V): Die Spannungen addieren sich, der Strom ist in allen Akkus gleich
                  (daher der Mittelwert der gemessenen Ströme), der Ladezustand ist der des SCHWÄCHSTEN
                  Akkus, weil er die Bank begrenzt.
        parallel  (z.B. mehrere 48-V-Akkus): Die Spannung ist der Mittelwert, die Ströme addieren sich,
                  der Ladezustand ist der Mittelwert.

    Die Temperatur ist in beiden Fällen die des wärmsten Akkus (der kritischste Wert); math.nan, wenn
    keine Temperatur gemessen wird.
    """
    n = len(packs)
    temps = [p.temp_c for p in packs if not math.isnan(p.temp_c)]
    temp = max(temps) if temps else math.nan
    currents = [p.current_a for p in packs if p.current_a is not None]
    volts, socs = [p.voltage_v for p in packs], [p.soc_pct for p in packs]
    if layout == "series":
        return sum(volts), (sum(currents) / len(currents) if currents else 0.0), min(socs), temp
    return sum(volts) / n, sum(currents), sum(socs) / n, temp


class GpioRelay(Actuator):
    """Relais an einem GPIO-Pin des Raspberry Pi, schaltet eine 230-V-Steckdose (pip install gpiozero).

    Sicherheit: Das Relais startet IMMER im ausgeschalteten Zustand (initial_value=False), auch nach
    einem Neustart oder Absturz des Programms. active_high=False passt zu den üblichen Relaisplatinen,
    die bei LOW schalten; bei einer Platine mit umgekehrter Logik auf True stellen. Für echte 230 V
    gehören geprüfte Relais bzw. Schütze und ein fachgerechter Anschluss durch eine Elektrofachkraft
    dazu, die GPIO-Pins allein sind dafür nicht gemacht.
    """
    def __init__(self, name: str, pin: int, active_high: bool = False) -> None:
        super().__init__(name)
        from gpiozero import OutputDevice
        self._dev = OutputDevice(pin, active_high=active_high, initial_value=False)

    def _set(self, level: float) -> None:
        """Relais einschalten (Pegel ab 0.5) oder ausschalten. Ein Relais kennt nur an/aus."""
        self._dev.on() if level >= 0.5 else self._dev.off()


class Outlet:
    """Eine 230-V-Steckdose mit Abschalt-Timer: Sie bleibt nie dauerhaft an.

    Das ist der Kerngedanke: Beim Einschalten wird (in der Oberfläche) immer eine Dauer gewählt
    (15 / 30 / 60 / 120 Minuten). Danach schaltet tick() die Steckdose von selbst wieder ab. So bleibt
    kein Verbraucher versehentlich über Nacht angeschlossen und leert den Akku.

    Die Abschaltung hängt am regelmäßigen Aufruf von tick(); den übernimmt PowerManager.step()
    bei jedem Takt. Deshalb ist es wichtig, dass dieser Takt nie abreißt (siehe App.tick in der
    Oberfläche).
    """
    def __init__(self, name: str, relay: Actuator) -> None:
        self.name, self.relay = name, relay
        self.off_at: float | None = None

    @property
    def is_on(self) -> bool:
        """True, solange das Relais angezogen ist."""
        return self.relay.is_on

    def turn_on(self, minutes: int | None) -> None:
        """Steckdose einschalten und den Abschaltzeitpunkt merken (minutes Minuten ab jetzt).

        Wird die Methode bei einer schon laufenden Steckdose erneut aufgerufen, startet der Timer neu
        (so verlängert man die Laufzeit). Ohne minutes (None) gäbe es keinen Timer, die Oberfläche übergibt
        aber immer eine Dauer.
        """
        self.relay.on()
        self.off_at = time.monotonic() + minutes * 60 if minutes else None

    def turn_off(self) -> None:
        """Steckdose ausschalten und den Timer löschen."""
        self.relay.off()
        self.off_at = None

    def remaining_s(self) -> float | None:
        """Verbleibende Sekunden bis zur automatischen Abschaltung (None ohne Timer)."""
        return None if self.off_at is None else max(0.0, self.off_at - time.monotonic())

    def tick(self) -> None:
        """Regelmäßig aufrufen: schaltet die Steckdose ab, sobald ihr Timer abgelaufen ist."""
        if self.is_on and self.off_at is not None and time.monotonic() >= self.off_at:
            log.info("%s: Timer abgelaufen", self.name)
            self.turn_off()


class PowerSource(ABC):
    """Schnittstelle für alles, was Solar-/Akku-Messwerte liefert.

    Der PowerManager kennt nur diese Schnittstelle. Ob die Werte simuliert sind (SimPower) oder von
    echter Hardware kommen (power_hw.VeDirectPower, power_hw.ModbusPower), ist ihm egal.
    """
    @abstractmethod
    def read(self) -> PowerReading:
        """Liefert die aktuellen Messwerte als PowerReading.

        Gibt es gerade keine gültigen Daten, soll die Quelle SourceError (oder eine andere Exception)
        werfen. Der PowerManager erkennt das als Datenausfall und sperrt nach einer Wartezeit die
        Steckdosen (Schutz, weil der Akku-Ladezustand dann unbekannt ist).
        """


class RealPower(PowerSource):
    """Alter Platzhalter für echte Hardware. Wird nicht mehr benutzt: Die echten Quellen stehen in
    power_hw.py (VeDirectPower, ModbusPower). Bleibt zur Rückwärtsverträglichkeit erhalten.
    """
    def read(self) -> PowerReading:
        """Nicht umgesetzt (siehe power_hw.py)."""
        raise NotImplementedError("Solar-/Akku-Hardware noch nicht angebunden")


def _clamp(x: float, lo: float, hi: float) -> float:
    """Begrenzt x auf den Bereich lo..hi."""
    return max(lo, min(hi, x))


class SimPower(PowerSource):
    """Simulierte Solaranlage mit 48-V-Akkubank aus 4 x 12 V (oder mehr/weniger Akkus) und Verbrauchern.

    Was simuliert wird (bei jedem read()):

        Sonne        Tageskurve (Sinus, Sonnenaufgang 6 Uhr, Mittag 12 Uhr). Ein Tag dauert DAY_S = 600 s
                     (Zeitraffer 144x). Im Live-Wetter-Modus läuft die Zeit in Echtzeit (siehe unten).
        Wetter       Über env (siehe sim_panel.SimEnvironment) vorgegeben: Bewölkung und Jahreszeit-Faktor,
                     oder im Live-Modus die echte Sonnenstrahlung in W/m2. Ohne env: zufälliger Wolkenfaktor.
        Solar        Jedes Panel liefert Spitzenleistung mal Sonne mal Wetter (Panel 2 ein wenig schwächer usw.)
        Verbrauch    45 W Grundlast + Lüfter 25 W + Pumpe 60 W (je nach Leistung) + Steckdosen
                     (Steckdose 1: 200 W, Steckdose 2: 150 W, wenn an) + Zusatzlast aus dem Simulations-Labor
        Akku         Solar minus Verbrauch lädt oder entlädt die Bank; ist sie voll, regelt der Laderegler
                     die Leistung ab. Spannung folgt dem Ladezustand (46-54 V).
        Einzel-Akkus  Vier 12-V-Blöcke teilen sich die Bankspannung, mit leichter Streuung bei Ladezustand
                     und Temperatur. Anzahl der Akkus (num_packs) und Panels (num_strings) lassen sich zur
                     Laufzeit in der Oberfläche ändern, um "was wäre wenn" auszuprobieren.

    Wichtig bei Live-Wetter: Dort läuft die Simulation in Echtzeit (kein Zeitraffer), sonst würde der
    Akku 144-mal zu schnell laden/entladen.
    """

    DAY_S = 600.0

    def __init__(self, cfg: PowerConfig, fan: Actuator, pump: Actuator,
                 outlets: list[Outlet], t0: float | None = None, seed: int | None = None, env=None) -> None:
        self.cfg, self.fan, self.pump, self.outlets, self.env = cfg, fan, pump, outlets, env
        self.rng = random.Random(seed)
        self.t0 = t0 if t0 is not None else time.monotonic()
        self.last = time.monotonic()
        self.soc, self.cloud = 70.0, 0.9
        self.energy_wh, self.day = 0.0, 0
        self.outlet_w = [65.0, 150.0]
        self.fan_total_w = 7.5
        self.bat_temp = 18.0
        self.num_strings = cfg.strings
        self.num_packs = cfg.batteries
        self.wh_per_pack = cfg.battery_capacity_wh

    def read(self) -> PowerReading:
        """Ein simulierter Messzyklus: rechnet die Zeit seit dem letzten Aufruf in Energie um.

        Ablauf: Zeit und Tageszeit bestimmen, Sonne und Wetter, Leistung je Panel, Verbrauch, Energiebilanz,
        Ladezustand und Spannung der Bank, Werte der Einzel-Akkus. Das Ergebnis ist ein vollständiges
        PowerReading, genau wie es auch eine echte Quelle liefern würde.
        """
        g = self.rng.gauss
        now = time.monotonic()
        live = bool(getattr(self.env, "live_siegen", False))   # Live-Wetter: echte Zeit, kein Zeitraffer
        scale = 1.0 if live else 86400.0 / self.DAY_S
        dt_h = (now - self.last) * scale / 3600.0
        self.last = now
        t = now - self.t0
        day = time.localtime().tm_yday if live else int(t // self.DAY_S)
        if day != self.day:
            self.day, self.energy_wh = day, 0.0

        h_sim = self.env.manual_hour if (self.env is not None and self.env.manual_hour is not None) else (getattr(self.env, "sim_hour", None) if self.env is not None else None)
        if h_sim is not None:
            sun = max(0.0, math.sin(((h_sim - 6.0) / 24.0) * 2 * math.pi))
        else:
            sun = max(0.0, math.sin(t / self.DAY_S * 2 * math.pi))

        if self.env is not None:
            w_scen = str(getattr(self.env, "weather_scenario", "auto")).lower()
            scen_mul = {"gewitter": 0.15, "sturm": 0.25, "nebel": 0.35, "regen": 0.30, "klar": 1.15}.get(w_scen, 1.0)
            weather_factor = max(0.03, min(1.0, (1.0 - self.env.cloud_cover_pct / 100.0) * self.env.sun_peak_factor * scen_mul))
            extra_load = self.env.extra_load_w
        else:
            self.cloud = _clamp(self.cloud + g(0, 0.04), 0.5, 1.0)
            weather_factor = self.cloud
            extra_load = 0.0

        strings = []
        for k in range(self.num_strings):
            sf = min(1.1, max(0.0, self.env.live_radiation_wm2 / 1000.0)) if (self.env is not None and getattr(self.env, "live_radiation_wm2", None) is not None) else (sun * weather_factor)
            p = self.cfg.string_peak_w * (1.0 - 0.05 * k) * sf
            if p < 1.0:
                strings.append(StringReading(0.0, 0.0))
                continue
            v = 85.0 + 10.0 * sun + g(0, 0.3)
            strings.append(StringReading(round(v, 1), round(p / v, 2)))
        solar_w = sum(s.power_w for s in strings)

        outlet_loads = tuple(w if o.is_on else 0.0 for w, o in zip(self.outlet_w, self.outlets))
        inv_idle = 4.0 if any(o.is_on for o in self.outlets) else 0.0
        fan_w = self.fan_total_w * (self.fan.level ** 1.6) if self.fan.level > 0 else 0.0
        load = 7.0 + fan_w + 28.0 * self.pump.level + inv_idle + sum(outlet_loads) + extra_load

        out_base = self.env.outside_temp_base_c if self.env is not None else 20.0
        room_target = max(1.0, out_base * 0.75 + 4.0) + 1.5 * sun
        self.bat_temp += (room_target - self.bat_temp) * min(0.2, 0.02 + dt_h * 0.5)

        if getattr(self, "fault_temp_c", None) is not None:
            self.bat_temp = float(self.fault_temp_c)
        elif self.env is not None:
            t_out = self.env.live_temp_c if (getattr(self.env, "live_siegen", False) and getattr(self.env, "live_temp_c", None) is not None) else self.env.outside_temp_base_c
            self.bat_temp = round(t_out + 1.5, 1)
        solar_usable = solar_w * 0.96
        if solar_usable > load:
            surplus = solar_usable - load
            if self.bat_temp < 2.0:
                surplus = 0.0
            elif self.bat_temp < 10.0:
                surplus *= (self.bat_temp - 2.0) / 8.0
            net = surplus
        else:
            net = solar_usable - load
        if self.bat_temp < 0.0:
            net = -7.0
            strings = [StringReading(0.0, 0.0) for _ in strings]  # Frost-Schlafmodus: BMS kappt Ladung & WR, nur DC-Wandler + Pi (7 W) laufen
        elif self.bat_temp < 2.0 and net > 0:
            net = 0.0   # BMS-Frostschutz: keine Ladung unter 2 °C
        if self.soc >= 100.0 and net > 0:
            net = 0.0

        temp_cap = _clamp(1.0 - max(0.0, 15.0 - self.bat_temp) * 0.015, 0.65, 1.0)
        total_cap_wh = max(500.0, self.num_packs * self.wh_per_pack * temp_cap)
        self.soc = _clamp(self.soc + net * dt_h / total_cap_wh * 100.0, 0.0, 100.0)
        self.energy_wh += solar_w * dt_h

        r_int = 0.02 * (1.0 + max(0.0, 20.0 - self.bat_temp) * 0.04)
        v_bat = 46.0 + 8.0 * self.soc / 100.0
        i_bat = net / v_bat
        v_bat += r_int * i_bat

        n_packs = max(1, min(8, self.num_packs))
        pack_v_base = v_bat / 4.0
        packs = tuple(
            BatteryPackReading(
                name=f"Akku {idx + 1}",
                voltage_v=round(float(self.fault_cell_v) if (getattr(self, "fault_cell_v", None) is not None and idx == 1) else (pack_v_base + g(0, 0.03)), 2),
                soc_pct=round(_clamp(self.soc + g(0, 0.8), 0.0, 100.0), 1),
                temp_c=round(self.bat_temp + (idx % 4) * 0.2 + abs(i_bat) * 0.02 + g(0, 0.1), 1),
                current_a=round(i_bat / (2.0 if n_packs > 4 else 1.0), 1),
            )
            for idx in range(n_packs)
        )

        return PowerReading(
            strings=tuple(strings),
            solar_energy_today_kwh=round(self.energy_wh / 1000.0, 2),
            battery_voltage_v=round(v_bat, 1),
            battery_current_a=round(i_bat, 1),
            battery_soc_pct=round(self.soc, 1),
            battery_temp_c=round(self.bat_temp + g(0, 0.1), 1),
            load_w=round(load),
            outlet_loads_w=outlet_loads,
            battery_packs=packs,
        )



class PowerManager:
    """Liest die Solar-/Akku-Daten, schützt den Akku und kontrolliert, ob die Steckdosen laufen dürfen.

    Regeln (in der Reihenfolge, wie sie greifen):

        1. Datenausfall: Liefert die Quelle länger als stale_after_s (30 s) keine Daten, ist der
           Ladezustand unbekannt. Dann werden die Steckdosen abgeschaltet und gesperrt
           ("Keine Akkudaten"). Nach dem Start gilt dieselbe Frist als Schonzeit.
        2. Akku zu leer: Fällt der SCHWÄCHSTE Akku unter soc_cutoff_pct, werden die Steckdosen
           abgeschaltet und gesperrt ("Akku zu niedrig").
        3. Freigabe: Erst wenn alle Akkus wieder mindestens soc_resume_pct haben UND Daten da sind,
           darf wieder eingeschaltet werden. Die Lücke zwischen Sperr- und Freigabegrenze (Hysterese)
           verhindert ständiges An/Aus.

    Zusätzlich ruft step() bei jedem Takt die Timer aller Steckdosen auf (Outlet.tick) und zwar IMMER
    als Erstes, damit die Zeitabschaltung auch bei Datenausfall funktioniert.

    Der Manager wird von der Oberfläche einmal pro Sekunde aufgerufen (App.tick).
    """
    def __init__(self, cfg: PowerConfig, source: PowerSource, outlets: list[Outlet],
                 stale_after_s: float = 30.0) -> None:
        self.cfg, self.source, self.outlets = cfg, source, outlets
        self.stale_after_s = stale_after_s
        self.locked, self.lock_reason = False, ""
        self.stale = False
        self.last: PowerReading | None = None
        self._last_ok = time.monotonic()
        self._warned = False

    @property
    def can_switch_on(self) -> bool:
        """True, wenn jetzt eingeschaltet werden darf: nicht gesperrt, aktuelle Daten da und mindestens
        ein erster Messwert vorhanden. Die Oberfläche macht damit den Einschalt-Knopf grau.
        """
        return not self.locked and not self.stale and self.last is not None

    def _lock(self, reason: str) -> None:
        """Steckdosen sperren: Grund merken (für die Anzeige) und sofort alle abschalten."""
        if self.locked and self.lock_reason == reason:
            return
        self.locked, self.lock_reason = True, reason
        log.warning("Steckdosen aus und gesperrt: %s", reason)
        self.shutdown()

    def step(self) -> PowerReading | None:
        """Ein Takt: Timer prüfen, Messwerte holen, Akku-Schutz anwenden. Gibt den letzten bekannten
        Messwert zurück (oder None, solange noch nie einer vorlag).

        Fehler der Quelle werden hier abgefangen und führen nicht zum Absturz. Der erste Fehler wird
        geloggt (nicht jede Sekunde neu), und die Steckdosen werden erst nach stale_after_s gesperrt,
        damit ein kurzer Aussetzer nicht sofort alles abschaltet.
        """
        for o in self.outlets:
            o.tick()
        try:
            r = self.source.read()
        except Exception as e:
            self.stale = True
            if not self._warned:
                log.warning("Keine Solar-/Akkudaten: %s", e)
                self._warned = True
            if not self.locked and time.monotonic() - self._last_ok > self.stale_after_s:
                self._lock("Keine Akkudaten")
            return self.last

        if self._warned:
            log.info("Solar-/Akkudaten wieder da")
            self._warned = False
        self.stale, self._last_ok, self.last = False, time.monotonic(), r
        min_v = min((p.voltage_v for p in r.battery_packs), default=13.0)
        if min_v < 11.5:
            self._lock("Fail-Safe: Zell-Unterspannung (< 11,5 V)")
            return r
        if r.battery_temp_c < 0.0:
            self._lock("Frost-Schlafmodus (< 0 °C) – nur DC-Wandler & Pi aktiv")
            return r
        soc = r.lowest_soc_pct   # schwächster Akku zählt
        if soc < self.cfg.soc_cutoff_pct and not self.locked:
            self._lock("Akku zu niedrig")
        elif self.locked and soc >= self.cfg.soc_resume_pct:
            self.locked, self.lock_reason = False, ""
            log.info("Akku wieder bei %.0f%% – Steckdosen freigegeben", soc)
        elif self.locked:
            self.lock_reason = "Akku zu niedrig"
        return r

    def switch_on(self, outlet: Outlet, minutes: int | None) -> bool:
        """Steckdose einschalten (für minutes Minuten), falls das erlaubt ist. Gibt True zurück, wenn
        eingeschaltet wurde, False bei Sperre oder fehlenden Daten.
        """
        if not self.can_switch_on:
            return False
        outlet.turn_on(minutes)
        return True

    def switch_off(self, outlet: Outlet) -> None:
        """Steckdose jederzeit ausschalten (Ausschalten ist nie gesperrt)."""
        outlet.turn_off()

    def shutdown(self) -> None:
        """Alle Steckdosen ausschalten (beim Beenden des Programms und bei Sperre)."""
        for o in self.outlets:
            o.turn_off()

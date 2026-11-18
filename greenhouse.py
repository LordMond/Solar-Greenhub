#!/usr/bin/env python3
"""Gewächshaus-Steuerung – der Kern: Messwerte, Ausgabegeräte und die Regelung.

Diese Datei ist das Herzstück der Steuerung. Sie kennt weder die Oberfläche noch die
Solaranlage. Das ist Absicht: Die Regelung soll auch ohne Bildschirm laufen und sich
einzeln testen lassen. Alles Weitere baut darauf auf:

    greenhouse.py      (diese Datei)  Sensoren, Lüfter, Pumpe und die Regelung
    power.py           Solar, Akkus und die 230-V-Steckdosen (Modell + Schutzlogik)
    power_hw.py        echte Solar-/Akku-Hardware (VE.Direct, Modbus) und hardware.toml
    greenhouse_gui.py  Touch-Oberfläche, die alles zusammensteckt
    sim_panel.py       Simulations-Labor (Fenster mit Reglern für Wetter und Tageszeit)
    siegen_wetter.py   Live-Wetter aus dem Internet für den Simulations-Modus
    greenhouse_plot.py ältere Diagramm-Oberfläche mit matplotlib (nur Desktop)

Aufbau dieser Datei (von unten nach oben):

    Config      alle Schwellwerte und Pumpen-Einstellungen an einer Stelle
    Reading     ein Messzyklus: Temperatur, Luftfeuchte, Bodenfeuchte, Steuerspannung
    Actuator    Ausgabegerät, stufenlos von 0.0 (aus) bis 1.0 (voll)
                SimActuator merkt sich nur den Zustand, GpioActuator schaltet einen
                PWM-Ausgang am Raspberry Pi
    Sensors     liefert ein Reading: SimSensors (simuliert), PiSensors (Platzhalter)
    Controller  die eigentliche Regelung: ein Aufruf von step() ist ein Regelzyklus

Ein Regelzyklus heißt "messen, entscheiden, schalten":

    1. sensors.read() liefert ein Reading.
    2. Controller.step() vergleicht es mit den Schwellwerten aus Config.
    3. Lüfter und Pumpe werden über set_level() geschaltet.

Wer step() aufruft, bestimmt den Takt: die Kommandozeile am Dateiende oder die Oberfläche
in greenhouse_gui.py (dort einmal pro Sekunde).

Warum ist das so gebaut? Die wichtigsten Sicherheitsgedanken:

    - Die Pumpen-Obergrenze steckt im Actuator selbst (max_level) und nicht nur in der
      Automatik. So gilt sie auch, wenn jemand die Pumpe von Hand hochregelt. Eine 12-V-Pumpe
      an 24 V würde sonst durchbrennen.
    - Fällt die Steuerspannung zu tief, geht alles aus (Unterspannungsschutz), in jedem Modus.
    - Die Pumpe läuft in festen Zyklen (Laufzeit, dann Einwirkpause) und hat ein Stundenlimit.
      Steigt die Bodenfeuchte trotz Gießen nicht (defekter Sensor, leerer Tank, Schlauch ab),
      sperrt sich die Automatik, statt endlos zu pumpen.

Start (Simulation, läuft auf jedem Rechner):  python greenhouse.py --sim
Start (echte Hardware):                         python greenhouse.py
Versionsbezeichnung anzeigen:                   python greenhouse.py --version
"""
from __future__ import annotations

import argparse
import logging
import math
import random
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass

log = logging.getLogger("greenhouse")

# Versionsbezeichnung des Projekts. Steht an EINER Stelle und wird von hier aus im Log, in der
# Oberfläche (Kopfzeile) und bei "--version" angezeigt. Bei einer neuen Version nur hier ändern.
BUILD = "Greenhouse_build_0.27.3_STABLE"


# --------------------------------------------------------------------------
# Konfiguration & Messwerte
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    """Alle einstellbaren Werte der Regelung an einer Stelle.

    Das Objekt ist "frozen" (unveränderlich): Nach dem Anlegen kann sich im laufenden Betrieb
    nichts mehr unbemerkt ändern. Die Felder bilden drei Gruppen:

        Takt        interval_s: Sekunden zwischen zwei Regelzyklen (Standard der Oberfläche: 1 s)
        Klima       temp_max_c / temp_hyst_c: Lüfter an über 28 °C, aus unter 26 °C (Hysterese, damit
                    der Lüfter nicht ständig an- und ausgeht); soil_min_pct / soil_ok_pct: Pumpe darf
                    starten unter 35 % Bodenfeuchte und stoppt spätestens bei 55 %
        Spannung    battery_min_v / battery_min_v_24v: Abschaltschwelle der Steuerspannung für 12 V
                    bzw. 24 V (siehe undervoltage_v)
        Pumpe       pump_voltage_v, pump_rated_v, pump_max_pwm: welche Spannung liegt an, wofür ist
                    die Pumpe gebaut, wie viel Leistung ist erlaubt (siehe pump_limit);
                    pump_run_s / pump_soak_s: Laufzeit und Einwirkpause eines Gieß-Zyklus;
                    pump_max_per_hour_s, pump_min_gain_pct, pump_fault_runs: Überflutungsschutz

    Hinweis: Die 12-V- und 24-V-Werte beziehen sich auf die STEUERSPANNUNG der Elektronik und Pumpe,
    nicht auf das 48-V-Solarsystem (das steht in power.py / PowerConfig).
    """
    interval_s: float = 2.0
    temp_max_c: float = 28.0
    temp_hyst_c: float = 2.0
    fan_count: int = 3
    fan_power_per_unit_w: float = 2.5
    soil_min_pct: float = 35.0
    soil_ok_pct: float = 55.0
    battery_min_v: float = 11.5

    battery_min_v_24v: float = 22.0   # Abschaltschwelle bei 24-V-Steuerspannung

    # Pumpen-Profil & Spannungswahl (12 V oder 24 V)
    pump_voltage_v: int = 12          # Versorgungsspannung der Pumpe: 12 oder 24
    pump_rated_v: float = 12.0        # Nennspannung der Pumpe (Typenschild)
    pump_run_s: float = 60.0          # Dauer eines Bewässerungs-Zyklus in Sekunden
    pump_soak_s: float = 180.0        # Einwirkzeit nach dem Gießen, bevor neu gemessen wird
    pump_max_pwm: float = 1.0         # zusätzliches Leistungs-Limit (0..1)
    pump_max_per_hour_s: float = 600.0  # max. Laufzeit pro Stunde (Überflutungsschutz)
    pump_min_gain_pct: float = 2.0    # so viel muss die Bodenfeuchte nach einem Zyklus steigen
    pump_fault_runs: int = 3          # so viele Zyklen ohne Anstieg -> Pumpen-Automatik sperren

    @property
    def undervoltage_v(self) -> float:
        """Abschaltschwelle passend zur Steuerspannung (12 V / 24 V)."""
        return self.battery_min_v if self.pump_voltage_v <= 12 else self.battery_min_v_24v

    @property
    def pump_limit(self) -> float:
        """Maximale Pumpen-Leistung (0..1). Schützt z.B. eine 12-V-Pumpe an 24 V automatisch."""
        return max(0.0, min(1.0, self.pump_max_pwm, self.pump_rated_v / self.pump_voltage_v))


@dataclass(frozen=True)
class Reading:
    """Ein Messzyklus der Sensoren (alle Werte zum gleichen Zeitpunkt).

    temperature_c und humidity_pct sind die Werte im Gewächshaus, soil_moisture_pct die
    Bodenfeuchte in Prozent und battery_v die Steuerspannung in Volt (nicht die 48-V-Akkubank).
    """
    temperature_c: float
    humidity_pct: float
    soil_moisture_pct: float
    battery_v: float


# --------------------------------------------------------------------------
# Aktoren (Lüfter, Pumpe)
# --------------------------------------------------------------------------
class Actuator(ABC):
    """Ein stufenlos regelbares Ausgabegerät (Lüfter, Pumpe, aber auch ein Relais).

    level liegt zwischen 0.0 (aus) und 1.0 (volle Leistung). max_level ist eine harte Obergrenze:
    set_level() kappt jeden Wunsch darauf. Der Controller setzt sie für die Pumpe, damit auch der
    Handregler in der Oberfläche eine 12-V-Pumpe nicht an 24 V überlasten kann.

    Unterklassen müssen nur _set() liefern ("jetzt wirklich schalten"). Die Basisklasse übernimmt
    Begrenzung, Zustand und Logging. Dadurch verhalten sich Simulation und echte Hardware gleich,
    und der Rest des Programms muss nicht wissen, womit er gerade spricht.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.level = 0.0
        self.max_level = 1.0   # Obergrenze, z.B. Schutz einer 12-V-Pumpe an 24 V

    @property
    def is_on(self) -> bool:
        """True, sobald das Gerät mit irgendeiner Leistung läuft (level größer 0)."""
        return self.level > 0

    def set_level(self, level: float) -> None:
        """Leistung setzen (0.0 bis 1.0). Der Wert wird auf 0..max_level begrenzt.

        Nur wenn sich der Pegel wirklich ändert, wird _set() aufgerufen und ein Log-Eintrag
        geschrieben. So wird die Hardware nicht jede Sekunde mit gleichen Werten beschäftigt.
        """
        level = max(0.0, min(self.max_level, level))
        if level != self.level:
            self._set(level)
            self.level = level
            if self.name == "Lüfter":
                self.log_fan_state(level)
            else:
                log.info("%s %d%%", self.name, round(level * 100))

    def log_fan_state(self, fallback_level: float | None = None) -> None:
        """Protokolliert Änderungen der Lüfterstufen L1-L3 im System-Log."""
        ff = getattr(self, "_fault_indices", set())
        lvls = getattr(self, "_single_levels", None)
        if not lvls or len(lvls) != 3:
            lv = self.level if fallback_level is None else fallback_level
            lvls = [0.0 if i in ff else lv for i in range(3)]
        running = [(i + 1, round(lvls[i] * 100)) for i in range(3) if i not in ff and lvls[i] > 0.01]
        flt = [str(i + 1) for i in range(3) if i in ff]
        extra = f" | gestört: Lüfter {', '.join(flt)}" if flt else ""
        if running:
            if len({p for _, p in running}) == 1:
                names = ", ".join(str(n) for n, _ in running)
                msg = f"Lüfter {names}: {running}%{extra}"
            else:
                parts = ", ".join(f"Lüfter {n} ({p}%)" for n, p in running)
                msg = f"{parts}{extra}"
        else:
            msg = f"Lüfter: 0%{extra}"
        if msg != getattr(self, "_last_fan_log", None):
            self._last_fan_log = msg
            log.info(msg)

    def on(self) -> None:
        """Volle Leistung (bzw. max_level, falls niedriger)."""
        self.set_level(1.0)

    def off(self) -> None:
        """Ausschalten (level 0.0)."""
        self.set_level(0.0)

    @abstractmethod
    def _set(self, level: float) -> None:
        """Gibt den Pegel an die Hardware aus. Wird von set_level() aufgerufen, nie direkt.

        level ist hier bereits begrenzt und garantiert eine Änderung gegenüber dem letzten Wert.
        """


class SimActuator(Actuator):
    """Ausgabegerät ohne Hardware für die Simulation: merkt sich nur den Zustand.

    Die simulierten Sensoren (SimSensors) und die simulierte Solaranlage (SimPower) lesen
    .level aus und rechnen damit, z.B. "Pumpe an, also steigt die Bodenfeuchte".
    """
    def _set(self, level: float) -> None:
        """Nichts zu tun: der Zustand steckt schon in level."""
        pass  # nur Zustand, keine Hardware


class GpioActuator(Actuator):
    """PWM-Ausgang an einem GPIO-Pin des Raspberry Pi (benötigt: pip install gpiozero).

    Gedacht für Lüfter und Pumpe über einen MOSFET-Treiber, NICHT über ein mechanisches Relais:
    Ein Relais verträgt kein schnelles Ein/Aus (PWM) und würde schnell verschleißen. Der Import
    von gpiozero passiert erst hier im Konstruktor, damit die Simulation auf jedem Rechner ohne
    diese Bibliothek läuft. Der Pin startet immer mit 0 (aus).
    """

    def __init__(self, name: str, pin: int) -> None:
        super().__init__(name)
        from gpiozero import PWMOutputDevice  # Import erst hier -> Sim läuft ohne

        self._dev = PWMOutputDevice(pin, frequency=25000, initial_value=0)

    def _set(self, level: float) -> None:
        """Pegel als PWM-Tastverhältnis (0.0 bis 1.0) an den Pin ausgeben."""
        self._dev.value = level


# --------------------------------------------------------------------------
# Sensoren
# --------------------------------------------------------------------------
class Sensors(ABC):
    """Schnittstelle für alles, was Messwerte liefert.

    Der Controller kennt nur diese Schnittstelle. Ob die Werte aus der Simulation (SimSensors)
    oder von echten Sensoren (PiSensors) kommen, ist ihm egal.
    """
    @abstractmethod
    def read(self) -> Reading:
        """Liefert den aktuellen Messzyklus als Reading. Darf blockieren, soll aber schnell sein."""


def _clamp(x: float, lo: float, hi: float) -> float:
    """Begrenzt x auf den Bereich lo..hi."""
    return max(lo, min(hi, x))

class SimSensors(Sensors):
    """Simulierte Sensoren: erzeugen glaubwürdige Messwerte und reagieren auf Lüfter und Pumpe.

    Das Modell ist absichtlich einfach, aber zeigt die richtigen Zusammenhänge:

        Temperatur      folgt einer Tageskurve (warm mittags, kühl nachts) mit Verzögerung; der
                        Lüfter kühlt zusätzlich
        Luftfeuchte     sinkt, wenn es wärmer wird (und umgekehrt)
        Bodenfeuchte    trocknet langsam aus; die Pumpe befeuchtet
        Steuerspannung  sinkt mit der Last von Lüfter und Pumpe (Spannungseinbruch)

    Der Tag wird gerafft: Ein Tag dauert DAY_S = 600 Sekunden (10 Minuten), damit man alle
    Tageszeiten in kurzer Zeit sieht. Mit env (siehe sim_panel.py, SimEnvironment) lassen sich
    Jahreszeit, Außentemperatur und Uhrzeit von außen vorgeben. Alle Veränderungen gelten pro
    AUFRUF von read(), nicht pro Sekunde; das Modell ist also auf den Standardtakt von etwa 1 Sekunde
    abgestimmt.
    """

    DAY_S = 1440.0

    def __init__(self, fan: Actuator, pump: Actuator, seed: int | None = None, env=None) -> None:
        self.fan, self.pump, self.env = fan, pump, env
        self.rng = random.Random(seed)
        self.t0 = time.monotonic()
        self.temp = float(env.outside_temp_base_c) if (env is not None and getattr(env, "live_siegen", False)) else 24.0
        self.soil = 60.0
        self.hum = 68.0
        self.nominal_v = 12.0
        self.fan_count = 3
        self.power_source = None

    def read(self) -> Reading:
        """Ein simulierter Messzyklus.

        Zuerst wird die Tageszeit bestimmt (feste Uhrzeit aus env, sonst aus der vergangenen Zeit),
        daraus die Zieltemperatur, dann nähern sich Temperatur, Luftfeuchte, Bodenfeuchte und
        Steuerspannung in kleinen Schritten an. Die Spannung sinkt mit der Last von Lüfter und Pumpe.
        """
        g = self.rng.gauss
        h_gh = self.env.manual_hour if (self.env is not None and self.env.manual_hour is not None) else (getattr(self.env, "sim_hour", None) if self.env is not None else None)
        if h_gh is not None:
            phase = ((h_gh - 6.0) / 24.0) * 2 * math.pi
        else:
            phase = (time.monotonic() - self.t0) / self.DAY_S * 2 * math.pi

        base_t = self.env.outside_temp_base_c if self.env is not None else 24.0
        swing_t = self.env.temp_swing_c if self.env is not None else 8.0
        target_temp = base_t + swing_t * math.sin(phase)

        # Die Innentemperatur nähert sich pro Aufruf zu 5 % der Zieltemperatur (träges Verhalten wie ein
        # echtes Gewächshaus), der Lüfter kühlt zusätzlich um bis zu 0,15 °C pro Aufruf, dazu etwas Rauschen.
        self.temp += (target_temp - self.temp) * 0.05 - (0.05 * self.fan_count) * self.fan.level + g(0, 0.1)
        out_hum = float(getattr(self.env, "outside_humidity_pct", 68.0)) if self.env is not None else 68.0
        w_sc = str(getattr(self.env, "weather_scenario", "auto")).lower() if self.env is not None else "auto"
        wet_out = (w_sc in ("regen", "sturm", "gewitter", "nebel")) or bool(self.env and getattr(self.env, "rain_sim", False))
        eff_out = max(out_hum, 88.0) if wet_out else out_hum
        evap_gain = max(0.0, (self.soil - 20.0) * 0.16 + max(0.0, self.temp - 10.0) * 0.40)
        natural_target = _clamp(eff_out * 0.78 + 16.0 + evap_gain + (6.0 if wet_out else 0.0), 45.0, 96.0)
        # 3 Lüfter mit je 450..2.600 U/min (aktiver Luftdurchsatz skaliert mit laufenden Lüftern)
        n_active = max(1, int(getattr(self, "fan_count", 3)))
        fan_power_3 = (n_active / 3.0) * (2.40 if not wet_out else 1.15)
        floor_h = 62.0 if wet_out else 44.0
        if self.env is not None and getattr(self.env, "override_hum_pct", None) is not None:
            cur_h = float(self.env.override_hum_pct)
            if self.fan.level > 0:
                cur_h = _clamp(cur_h - fan_power_3 * self.fan.level, floor_h, 99.0)
            else:
                cur_h += (natural_target - cur_h) * (0.08 if wet_out else 0.045)
            self.env.override_hum_pct = _clamp(cur_h, floor_h, 99.0)
            self.hum = _clamp(self.env.override_hum_pct + g(0, 0.15), floor_h, 99.0)
        else:
            if self.fan.level > 0:
                self.hum = _clamp(self.hum - fan_power_3 * self.fan.level + g(0, 0.15), floor_h, 99.0)
            else:
                self.hum += (natural_target - self.hum) * (0.08 if wet_out else 0.045) + g(0, 0.15)
                self.hum = _clamp(self.hum, floor_h, 99.0)
        hum = self.hum

        if self.env is not None and getattr(self.env, "override_soil_pct", None) is not None:
            if self.pump.level > 0:
                self.env.override_soil_pct = _clamp(float(self.env.override_soil_pct) + 0.45 * self.pump.level, 5.0, 95.0)
            self.soil = _clamp(float(self.env.override_soil_pct) + g(0, 0.05), 0, 100)
        else:
            # Plausible Bodenverdunstung abhängig von Innentemperatur & Luftfeuchte + Bewässerung durch Pumpe
            evap_rate = max(0.002, max(0.0, self.temp - 3.0) * 0.0016 * (1.0 - hum / 115.0))
            self.soil = _clamp(self.soil - evap_rate + 0.45 * self.pump.level + g(0, 0.03), 0, 100)
        load_drop = (0.04 * self.fan_count) * self.fan.level + 0.35 * self.pump.level
        sag = (25.0 - self.power_source.soc) * 0.08 if (self.power_source is not None and getattr(self.power_source, "soc", 100.0) < 25.0) else 0.0
        ctrl_v = self.nominal_v + 0.2 - load_drop - sag + g(0, 0.03)
        return Reading(round(self.temp, 1), round(hum, 1), round(self.soil, 1), round(ctrl_v, 2))



class PiSensors(Sensors):
    """Platzhalter für die echten Sensoren am Raspberry Pi.

    Muss noch gebaut werden: Temperatur und Luftfeuchte z.B. mit DHT22 oder BME280, Bodenfeuchte
    und Steuerspannung z.B. über einen ADS1115 (I2C-Analogwandler). Bis dahin wirft read() eine
    Fehlermeldung. Die Oberfläche fängt das ab (siehe App.tick) und schaltet Pumpe/Lüfter aus.
    """

    def read(self) -> Reading:
        # TODO: Temperatur/Luftfeuchte, z.B. DHT22 oder BME280
        # TODO: Bodenfeuchte + Akku-Spannung z.B. über ADS1115 (I2C)
        """Noch nicht umgesetzt: wirft NotImplementedError, siehe Klassenbeschreibung."""
        raise NotImplementedError("Hardware-Sensoren noch nicht implementiert")


# --------------------------------------------------------------------------
# Steuerlogik
# --------------------------------------------------------------------------
class Controller:
    """Die Regelung: entscheidet bei jedem Aufruf von step(), was Lüfter und Pumpe tun.

    Automatik (manual = False):
        Lüfter   an über temp_max_c, aus unter temp_max_c minus temp_hyst_c (Hysterese).
        Pumpe    startet, wenn der Boden trockener als soil_min_pct ist und die Einwirkpause
                 vorbei ist; läuft pump_run_s Sekunden oder bis soil_ok_pct erreicht ist;
                 danach pump_soak_s Sekunden Pause, damit das Wasser einsickert.

    Schutzmechanismen (siehe auch Modul-Beschreibung):
        - Unterspannung schaltet beide Geräte immer ab (auch im Handbetrieb).
        - Die Pumpen-Obergrenze steckt als max_level im Pumpen-Actuator (gilt auch von Hand).
        - Stundenlimit: höchstens pump_max_per_hour_s Sekunden Pumpenlaufzeit pro Stunde.
        - Wirkungskontrolle: Nach der Einwirkpause muss die Bodenfeuchte um mindestens
          pump_min_gain_pct gestiegen sein. Nach pump_fault_runs erfolglosen Zyklen in Folge wird
          die Pumpen-Automatik gesperrt (pump_fault gefüllt). Quittiert wird durch Umschalten auf
          Handbetrieb (set_manual).

    Zustand zwischen den Aufrufen: _pump_until (Ende des Laufs), _soak_until (Ende der Pause),
    _runs (Liste der Läufe der letzten Stunde), _eval_pending/_fail_runs (Wirkungskontrolle).
    """
    def __init__(self, cfg: Config, sensors: Sensors, fan: Actuator, pump: Actuator) -> None:
        """Setzt den Startzustand und überträgt das Pumpen-Limit aus der Konfiguration auf die Pumpe.

        Ist die Pumpe an der anliegenden Spannung überlastet (z.B. 12-V-Pumpe an 24 V), wird beim
        Start eine Warnung geloggt und die Pumpe automatisch begrenzt.
        """
        self.cfg, self.sensors, self.fan, self.pump = cfg, sensors, fan, pump
        self.manual = False
        self.pump.max_level = cfg.pump_limit    # gilt auch im Manuell-Modus (Regler)
        self.pump_fault = ""                    # nicht leer: Pumpen-Automatik gesperrt
        self._pump_until = 0.0                  # Pumpe läuft bis zu diesem Zeitpunkt
        self._soak_until = 0.0                  # Wasser sickert ein bis zu diesem Zeitpunkt
        self._runs: deque[tuple[float, float]] = deque()   # (Start, Dauer) der Automatik-Läufe
        self._run_start = 0.0
        self._run_start_soil = 0.0
        self._eval_pending = False
        self._fail_runs = 0
        self._limit_warned = False
        if cfg.pump_limit < cfg.pump_max_pwm:
            log.warning("Pumpe auf %d %% begrenzt (%.0f-V-Pumpe an %d V)",
                        round(cfg.pump_limit * 100), cfg.pump_rated_v, cfg.pump_voltage_v)

    def set_manual(self, manual: bool) -> None:
        """Automatik/Manuell umschalten. Der Wechsel auf Manuell quittiert eine Pumpen-Sperre."""
        self.manual = manual
        if manual:
            self.clear_pump_fault()

    def clear_pump_fault(self) -> None:
        """Hebt die Pumpen-Sperre auf und setzt die Wirkungskontrolle zurück."""
        self.pump_fault, self._fail_runs, self._eval_pending = "", 0, False

    def _budget_used(self, now: float) -> float:
        """Summe der Pumpen-Laufzeit (Sekunden) in der letzten Stunde.

        Alte Einträge (älter als 3600 s) werden dabei aus der Liste entfernt.
        """
        while self._runs and now - self._runs[0][0] > 3600.0:
            self._runs.popleft()
        return sum(d for _, d in self._runs)

    def _finish_run(self, now: float) -> None:
        """Aufräumen nach einem Gieß-Lauf: Einwirkpause starten und den Lauf für Stundenlimit und
        Wirkungskontrolle vormerken. Läufe, die nicht von der Automatik gestartet wurden (z.B. nach
        einem Wechsel von Hand auf Automatik), werden nicht mitgezählt.
        """
        self._soak_until = now + self.cfg.pump_soak_s
        log.info("Bewässerung beendet – Einwirkpause für %d s", int(self.cfg.pump_soak_s))
        if self._run_start > 0.0:  # nur echte Automatik-Läufe zählen
            self._runs.append((self._run_start, now - self._run_start))
            self._eval_pending = True
            self._run_start = 0.0

    def _evaluate_last_run(self, now: float, soil: float) -> None:
        """Nach der Einwirkpause prüfen, ob die Bodenfeuchte wirklich gestiegen ist."""
        if not self._eval_pending or now < self._soak_until:
            return
        self._eval_pending = False
        gain = soil - self._run_start_soil
        if gain >= self.cfg.pump_min_gain_pct:
            self._fail_runs = 0
            return
        self._fail_runs += 1
        log.warning("Bodenfeuchte nach Bewässerung nur %+.1f %% (Zyklus %d/%d ohne Anstieg)",
                    gain, self._fail_runs, self.cfg.pump_fault_runs)
        if self._fail_runs >= self.cfg.pump_fault_runs:
            self.pump_fault = "Pumpe gesperrt – Sensor/Pumpe prüfen"
            log.error("Pumpen-Automatik gesperrt (Umschalten auf Manuell quittiert)")

    def step(self) -> Reading:
        """Ein Regelzyklus: messen, entscheiden, schalten. Gibt die Messwerte zurück (für die Anzeige).

        Reihenfolge ist wichtig:
            1. Unterspannungsschutz (beendet den Zyklus, wenn ausgelöst)
            2. Handbetrieb (beendet den Zyklus, die Regler bestimmen)
            3. Lüfter mit Hysterese
            4. Pumpe: laufenden Lauf beenden oder, wenn aus, erst Wirkung des letzten Laufs prüfen
               und dann ggf. einen neuen starten (nicht bei Sperre, Einwirkpause oder Stundenlimit)
        """
        r = self.sensors.read()
        c = self.cfg
        now = time.monotonic()

        # 1. Schutz vor allem anderen: Bricht die Steuerspannung ein, gehen Lüfter und Pumpe aus –
        #    egal ob Automatik oder Handbetrieb. Ein Motor an zu niedriger Spannung wird sonst warm.
        if r.battery_v < c.undervoltage_v:
            self.fan.off()
            self.pump.off()
            self._pump_until = 0.0
            return r

        # Handbetrieb: Die Regler in der Oberfläche bestimmen Lüfter/Pumpe, die Automatik pausiert.
        # (Unterspannungsschutz oben und Pumpen-Limit im Actuator gelten trotzdem.)
        if self.manual:
            return r

        # Lüfter mit Hysterese
        if r.temperature_c > c.temp_max_c:
            self.fan.on()
        elif r.temperature_c < c.temp_max_c - c.temp_hyst_c and getattr(self, "_gui_curve_active", False) is False:
            self.fan.off()

        # Pumpe mit definierter Laufzeit + Einwirkpause
        if self.pump.is_on:
            if now >= self._pump_until or r.soil_moisture_pct >= c.soil_ok_pct:
                self.pump.off()
                self._finish_run(now)
        else:
            self._evaluate_last_run(now, r.soil_moisture_pct)
            if not self.pump_fault and r.soil_moisture_pct < c.soil_min_pct and now >= self._soak_until:
                if self._budget_used(now) >= c.pump_max_per_hour_s:
                    if not self._limit_warned:
                        log.warning("Pumpen-Limit pro Stunde erreicht – Automatik wartet")
                        self._limit_warned = True
                else:
                    self._limit_warned = False
                    self.pump.set_level(c.pump_max_pwm)   # begrenzt durch pump.max_level
                    self._pump_until = now + c.pump_run_s
                    self._run_start, self._run_start_soil = now, r.soil_moisture_pct
                    log.info("Bewässerung gestartet (%d V, %d %%, Laufzeit %d s)",
                             c.pump_voltage_v, round(self.pump.level * 100), int(c.pump_run_s))
        return r

    def shutdown(self) -> None:
        """Beide Geräte ausschalten (beim Beenden oder nach einem Sensorfehler)."""
        self.fan.off()
        self.pump.off()


# --------------------------------------------------------------------------
# Einstieg
# --------------------------------------------------------------------------
def main() -> None:
    """Kommandozeilen-Einstieg ohne Oberfläche: führt die Regelung im Takt aus und loggt die Werte.

    Nützlich zum Testen auf dem Pi (über SSH) oder zum Beobachten der Regelung im Log.
    Beenden mit Strg+C; die Geräte werden dabei sicher ausgeschaltet.
    """
    ap = argparse.ArgumentParser(description="Gewächshaus-Steuerung")
    ap.add_argument("--version", action="version", version=BUILD)
    ap.add_argument("--sim", action="store_true", help="Simulations-Modus (keine Hardware)")
    ap.add_argument("--interval", type=float, default=Config.interval_s, help="Sekunden zwischen Messungen")
    ap.add_argument("--cycles", type=int, default=0, help="Anzahl Durchläufe (0 = endlos)")
    ap.add_argument("--seed", type=int, default=None, help="Zufalls-Seed für reproduzierbare Simulation")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = Config(interval_s=args.interval)

    if args.sim:
        fan, pump = SimActuator("Lüfter"), SimActuator("Pumpe")
        sensors: Sensors = SimSensors(fan, pump, seed=args.seed)
        sensors.nominal_v = float(cfg.pump_voltage_v)
    else:
        fan, pump = GpioActuator("Lüfter", pin=17), GpioActuator("Pumpe", pin=27)
        sensors = PiSensors()

    ctrl = Controller(cfg, sensors, fan, pump)
    log.info("%s – Start (%s)", BUILD, "Simulation" if args.sim else "Hardware")

    n = 0
    try:
        while args.cycles == 0 or n < args.cycles:
            ctrl.step()
            n += 1
            time.sleep(cfg.interval_s)
    except KeyboardInterrupt:
        log.info("Abbruch durch Nutzer")
    finally:
        ctrl.shutdown()


if __name__ == "__main__":
    main()

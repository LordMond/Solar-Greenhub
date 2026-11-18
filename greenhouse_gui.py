#!/usr/bin/env python3
"""Touch-Oberfläche "Solar GreenHub" mit vier Menüs (tkinter, keine Zusatzpakete nötig).

Das ist das Hauptprogramm. Es steckt alles zusammen: die Regelung (greenhouse.py), die Energieseite
(power.py, power_hw.py) und, in der Simulation, das Simulations-Labor (sim_panel.py).

Start (Simulation):  python greenhouse_gui.py
Echte Hardware:      python greenhouse_gui.py --real --power vedirect   (oder modbus)
Touchscreen:         python greenhouse_gui.py --real --fullscreen       (Esc beendet)
Version anzeigen:    python greenhouse_gui.py --version
Benötigt im Projektordner: greenhouse.py, power.py, power_hw.py (und für die Simulation sim_panel.py
und siegen_wetter.py); für echte Hardware zusätzlich eine hardware.toml.

Aufbau der Oberfläche (Fenster 800 x 480, passt zu einem 7"-Touchscreen):

    Kopfzeile      Name, Versionsbezeichnung, Solarleistung und Akkustand, Uhrzeit, Hinweis "SIMULATION"
                   oder eine Warnung (Sensorfehler, Pumpe gesperrt, keine Solar-/Akkudaten)
    Seiten         vier Menüs, umschaltbar über die Knöpfe unten:
                     Gewächshaus  Messwert-Kacheln, Umschalter Automatik/Manuell, Regler Lüfter und Pumpe
                     Solar        Energiefluss-Animation, Solarleistung mit Verlauf, Balken je Panel
                     Akku         Ladezustand, Spannung/Strom/Leistung/Restlaufzeit, Einzel-Akkus
                     Steckdosen   zwei 230-V-Steckdosen mit Timer (nie dauerhaft an)
    Knopfleiste    die vier Menü-Knöpfe

Die Klassen im Überblick:

    Bedienelemente  Button, Card, TouchSlider, StringBar, BatteryGauge, BatteryPackBar, OutletInfo,
                    EnergyFlowCanvas, BatterySummaryCard. Das sind Zeichenflächen (Canvas), die sich bei
                    jeder Größenänderung selbst neu zeichnen und ihre Werte mit set_*/push-Methoden bekommen.
    Seiten          GreenhousePage, SolarPage, BatteryPage, OutletsPage (+ OutletPanel je Steckdose).
                    Jede Seite hat refresh(r, pr), das neue Messwerte entgegennimmt.
    App             baut das Fenster, hat den Takt (tick) und die Animation (_anim_loop).

So fließen die Daten (einmal pro Sekunde, App.tick):

    manager.step()   ->  PowerReading   (Solar/Akku; schaltet Steckdosen-Timer, wendet Akku-Schutz an)
    ctrl.step()      ->  Reading        (Gewächshaus-Sensoren; regelt Lüfter und Pumpe)
    page.refresh(r, pr) für jede Seite; Zeichenflächen zeichnen sich neu

Sicherheit im Takt (wichtig!): tick() ruft zuerst manager.step() auf, weil dort die Zeitabschaltung der
Steckdosen steckt. Jeder Teil ist einzeln abgesichert; selbst wenn ein Sensor ausfällt oder eine Seite
einen Fehler hat, wird der nächste Takt IMMER wieder geplant (try/finally). Sonst bliebe eine Steckdose
unbegrenzt an, wenn die Oberfläche einmal hängt.

Die Simulation ist Standard; --real wählt echte Hardware. Das Simulations-Labor-Fenster öffnet sich nur
in der Simulation (im Vollbild nur mit --simpanel).
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import time
import tkinter as tk
from collections import deque
from pathlib import Path

from diagram_view import SystemDiagramView
from settings_page import SettingsPage
from garden_view import GardenSceneView
from siegen_wetter import LOCATION_NAME, SiegenWeatherClient
from greenhouse import BUILD, Config, Controller, GpioActuator, PiSensors, SimActuator, SimSensors
from power import GpioRelay, Outlet, PowerConfig, PowerManager, PowerReading, SimPower
from power_hw import build_source, load_power_config

# Farben (Garten-Palette)
BG, HEADER, CARD = "#f4f1e4", "#2f6b3a", "#ffffff"
TEXT, MUTED, TRACK = "#2b3a2b", "#7a8a7a", "#e3dfcc"
GOOD, WARN, BAD = "#4f9d4f", "#e0a030", "#d2553f"
SUN, BLUE, BROWN, LEAF = "#e0a030", "#3b8fc4", "#8a5a36", "#6aa84f"
NAV_OFF, CHIP_OFF, DISABLED = "#9fb596", "#b7c4ad", "#b9c4b4"
FONT = "DejaVu Sans"

log = logging.getLogger("greenhouse.gui")


def rounded(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """Zeichnet ein Rechteck mit abgerundeten Ecken auf ein Tkinter-Canvas."""
    watermark = kw.pop("watermark", "")
    """Zeichnet ein Rechteck mit abgerundeten Ecken auf eine Zeichenfläche.

    tkinter kann keine runden Ecken; hier wird ein "weich" gezeichnetes Vieleck (smooth=True) genutzt.
    Hat die Fläche die Kartenfarbe (CARD, weiß), wird darunter ein hellgrauer, 3 Pixel versetzter
    Schatten gezeichnet. Das ergibt den "Karten"-Look, ohne dass jede Stelle den Schatten kennen muss.
    """
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    if kw.get("fill") == CARD:
        spts = [x1 + r, y1 + 3, x2 - r, y1 + 3, x2, y1 + 3, x2, y1 + r + 3, x2, y2 - r + 3, x2, y2 + 3,
                x2 - r, y2 + 3, x1 + r, y2 + 3, x1, y2 + 3, x1, y2 - r + 3, x1, y1 + r + 3, x1, y1 + 3]
        c.create_polygon(spts, smooth=True, fill="#e2decb", outline="")
        poly = c.create_polygon(pts, smooth=True, **kw)
        if watermark and (x2 - x1) > 70 and (y2 - y1) > 60:
            draw_watermark(c, watermark, x2, y2)
        return poly
    return c.create_polygon(pts, smooth=True, **kw)


def draw_garden_bg(c: tk.Canvas) -> None:
    """Zeichnet den dezenten Hintergrundverlauf der Hauptoberfläche."""
    c.delete("garden_bg")
    w, h = c.winfo_width(), c.winfo_height()
    if w < 100 or h < 80:
        return
    c.create_polygon(0, h, 0, h - 42, w * 0.35, h - 64, w * 0.72, h - 34, w, h - 55, w, h,
                     smooth=True, fill="#eae6d4", outline="", tags="garden_bg")
    for off in (0, 36, 72):
        c.create_line(off, 0, off + 95, 95, fill="#ebe7d6", width=2, tags="garden_bg")
        c.create_line(w - off, 0, w - off - 95, 95, fill="#ebe7d6", width=2, tags="garden_bg")
    for bx, bh, lean in ((24, 95, 18), (44, 120, 28), (68, 82, 12), (w - 30, 110, -24), (w - 58, 88, -14)):
        c.create_line(bx, h, bx + lean * 0.5, h - bh * 0.55, bx + lean, h - bh,
                      smooth=True, fill="#dfe5d0", width=4, capstyle="round", tags="garden_bg")
    mx, my = w * 0.5, h * 0.56
    c.create_oval(mx - 48, my - 18, mx + 6, my + 12, fill="#e6ebd8", outline="", tags="garden_bg")
    c.create_oval(mx - 6, my - 26, mx + 46, my + 6, fill="#e2e8d3", outline="", tags="garden_bg")
    c.tag_lower("garden_bg")


def _draw_bolt(c: tk.Canvas, cx: float, cy: float, sc: float, fill_col: str) -> None:
    pts = [cx + 1.5*sc, cy - 6*sc, cx - 4*sc, cy + 0.8*sc, cx - 0.5*sc, cy + 0.8*sc,
           cx - 1.5*sc, cy + 6*sc, cx + 4*sc, cy - 0.8*sc, cx + 0.5*sc, cy - 0.8*sc]
    c.create_polygon(pts, fill=fill_col, outline="#1f2a1f", width=1)


def draw_watermark(c: tk.Canvas, kind: str, x2: float, y2: float) -> None:
    """Zeichnet das halbtransparente Gewächshaus-Wasserzeichen im Hintergrund."""
    if not kind:
        return
    wx, wy = x2 - 30, y2 - 28
    if kind == "temp":
        c.create_line(wx, wy - 20, wx, wy + 8, fill="#f5b082", width=8, capstyle="round")
        c.create_oval(wx - 10, wy + 2, wx + 10, wy + 20, fill="#ee8b54", outline="")
        for sy in (-14, -8, -2):
            c.create_line(wx + 6, wy + sy, wx + 13, wy + sy, fill="#e67e22", width=2)
    elif kind == "hum":
        c.create_polygon(wx, wy - 22, wx - 14, wy + 2, wx, wy + 18, wx + 14, wy + 2, smooth=True, fill="#82ccdd", outline="")
        c.create_polygon(wx - 17, wy - 6, wx - 24, wy + 6, wx - 17, wy + 15, wx - 10, wy + 6, smooth=True, fill="#60a3bc", outline="")
    elif kind == "soil":
        c.create_arc(wx - 22, wy + 4, wx + 22, wy + 24, start=0, extent=180, style="arc", width=3, outline="#78b159")
        c.create_line(wx, wy + 8, wx, wy - 15, fill="#558b2f", width=3, capstyle="round")
        c.create_oval(wx - 17, wy - 17, wx - 1, wy - 4, fill="#8bc34a", outline="")
        c.create_oval(wx + 1, wy - 21, wx + 17, wy - 8, fill="#7cb342", outline="")
    elif kind in ("volt", "battery"):
        c.create_polygon(wx + 4, wy - 21, wx - 11, wy + 2, wx - 1, wy + 2, wx - 4, wy + 21,
                         wx + 11, wy - 2, wx + 1, wy - 2, fill="#f6b93b", outline="#e58e26", width=1)
    elif kind == "sun":
        c.create_oval(wx - 12, wy - 12, wx + 12, wy + 12, fill="#fad390", outline="#f6b93b", width=1.5)
        for deg in range(0, 360, 45):
            rad = math.radians(deg)
            c.create_line(wx + 15 * math.cos(rad), wy + 15 * math.sin(rad),
                          wx + 22 * math.cos(rad), wy + 22 * math.sin(rad), fill="#f6b93b", width=2.5)
    elif kind == "socket":
        c.create_oval(wx - 18, wy - 18, wx + 18, wy + 18, fill="#d1e8e2", outline="#58b19f", width=2)
        c.create_oval(wx - 8, wy - 3, wx - 3, wy + 3, fill="#3b6e6c", outline="")
        c.create_oval(wx + 3, wy - 3, wx + 8, wy + 3, fill="#3b6e6c", outline="")


def _soft_tint(hex_col: str, mix: float = 0.86) -> str:
    h = hex_col.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"#{int(r + (255 - r) * mix):02x}{int(g + (255 - g) * mix):02x}{int(b + (255 - b) * mix):02x}"


def dur_label(m: int) -> str:
    """Beschriftung für eine Timer-Dauer in Minuten: 15 -> '15 min', 60 -> '1 h', 120 -> '2 h'."""
    return f"{m // 60} h" if m % 60 == 0 else f"{m} min"


def soc_color(soc: float) -> str:
    """Stufenloser Farbverlauf von Rot (leer) über Orange (mittel) zu Grün (voll)."""
    s = max(0.0, min(100.0, soc))
    if s <= 20.0:
        r, g, b = 210, 85, 63
    elif s <= 50.0:
        t = (s - 20.0) / 30.0
        r = int(210 + (224 - 210) * t)
        g = int(85 + (160 - 85) * t)
        b = int(63 + (48 - 63) * t)
    else:
        t = (s - 50.0) / 50.0
        r = int(224 + (79 - 224) * t)
        g = int(160 + (157 - 160) * t)
        b = int(48 + (79 - 48) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# --- Statusbewertung -------------------------------------------------------
def temp_status(v, cfg):
    """Bewertung der Temperatur für die Kachel: über temp_max_c 'zu heiß', unter 15 °C 'zu kalt', sonst 'ideal'."""
    if v > cfg.temp_max_c:
        return "zu heiß", BAD
    return ("zu kalt", WARN) if v < 15 else ("ideal", GOOD)


def hum_status(v, cfg):
    """Bewertung der Luftfeuchte: unter 50 % 'trocken', über 85 % 'sehr feucht', sonst 'gut'."""
    if v < 50:
        return "trocken", WARN
    return ("sehr feucht", WARN) if v > 85 else ("gut", GOOD)


def soil_status(v, cfg):
    """Bewertung der Bodenfeuchte: unter soil_min_pct 'durstig', unter soil_ok_pct 'okay', sonst 'gut'."""
    if v < cfg.soil_min_pct:
        return "durstig", BAD
    return ("okay", WARN) if v < cfg.soil_ok_pct else ("gut", GOOD)


def batt_status(v, cfg):
    """Bewertung der Steuerspannung passend zum System (12 V oder 24 V, siehe Config.pump_voltage_v).

    Unter der Abschaltschwelle (Config.undervoltage_v) 'Unterspannung', bis 0,5 V darüber 'Lastabfall'
    (die Spannung bricht unter Last ein), sonst 'stabil'. Gleiche Schwelle wie im Unterspannungsschutz
    der Regelung, damit Anzeige und Abschaltung zusammenpassen.
    """
    low = cfg.undervoltage_v
    if v < low:
        return "Unterspannung", BAD
    return ("Lastabfall", WARN) if v < low + 0.5 else (f"stabil ({cfg.pump_voltage_v} V)", GOOD)

# --- Bedienelemente ----------------------------------------------------------
class Button(tk.Canvas):
    """Großer Touch-Knopf mit abgerundeten Ecken (Zeichenfläche).

    Zeigt text (optional eine kleine Zeile sub darunter) in der Farbe color. Ein Tipp ruft command()
    auf, aber nur wenn der Knopf aktiv (enabled) ist; deaktivierte Knöpfe sind grau. Mit look() ändert
    man Aussehen und Zustand später, z.B. "EINSCHALTEN" in Grün gegen "AUSSCHALTEN" in Rot.
    """

    def __init__(self, master, command, text="", sub="", color=GOOD, fsize=16, **kw):
        kw.setdefault("width", 10)
        kw.setdefault("height", 10)
        super().__init__(master, bg=BG, highlightthickness=0, **kw)
        self.command, self.text, self.sub = command, text, sub
        self.color, self.fsize, self.enabled = color, fsize, True
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", lambda e: self.command() if self.enabled else None)

    def look(self, text=None, sub=None, color=None, enabled=None) -> None:
        """Aussehen ändern (Text, Unterzeile, Farbe, aktiv/inaktiv). Nur angegebene Werte ändern sich."""
        if text is not None:
            self.text = text
        if sub is not None:
            self.sub = sub
        if color is not None:
            self.color = color
        if enabled is not None:
            self.enabled = enabled
        self.draw()

    def draw(self) -> None:
        """Zeichnet den Knopf neu (wird bei Größenänderung und nach look() aufgerufen)."""
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        r = max(4, min(22, (h - 8) // 2))
        rounded(self, 4, 4, w - 4, h - 4, r, fill=self.color if self.enabled else DISABLED, outline="")
        if self.sub:
            self.create_text(w / 2, h / 2 - 12, text=self.text, fill="white", font=(FONT, self.fsize, "bold"))
            self.create_text(w / 2, h / 2 + 16, text=self.sub, fill="white", font=(FONT, 9))
        else:
            self.create_text(w / 2, h / 2, text=self.text, fill="white", font=(FONT, self.fsize, "bold"))


class Card(tk.Canvas):
    """Kompakte Messwert-Karte mit Großanzeige, Status-Untertitel und kleinem Verlaufsdiagramm."""
    def __init__(self, master, title, fmt, color, status_fn, history_len, big=24, icon="", show_axes=False):
        super().__init__(master, bg=BG, highlightthickness=0, width=10, height=10)
        self.title_text, self.fmt, self.color, self.status_fn, self.big, self.icon, self.show_axes = title, fmt, color, status_fn, big, icon, show_axes
        self.hist: deque[float] = deque(maxlen=history_len)
        self.bind("<Configure>", lambda e: self.draw())

    def push(self, v: float) -> None:
        """Fügt einen neuen Messpunkt in die Verlaufsliste der Karte ein und zeichnet neu."""
        self.hist.append(v)
        self.draw()

    def _draw_icon(self, cx: float, cy: float) -> None:
        if not self.icon:
            return
        c = self.color
        self.create_oval(cx - 14, cy - 14, cx + 14, cy + 14, fill=_soft_tint(c, 0.85), outline="")
        if self.icon == "temp":
            self.create_line(cx, cy - 8, cx, cy + 4, fill=c, width=3, capstyle="round")
            self.create_oval(cx - 4, cy + 2, cx + 4, cy + 10, fill=c, outline="")
        elif self.icon == "hum":
            self.create_polygon(cx, cy - 9, cx - 6, cy + 2, cx, cy + 9, cx + 6, cy + 2, smooth=True, fill=c, outline="")
        elif self.icon == "soil":
            self.create_polygon(cx - 6, cy + 5, cx - 2, cy - 6, cx + 7, cy - 7, cx + 4, cy + 4, smooth=True, fill=c, outline="")
            self.create_line(cx - 6, cy + 7, cx + 4, cy - 4, fill="white", width=1)
        elif self.icon == "volt":
            self.create_polygon(cx + 2, cy - 9, cx - 5, cy + 1, cx, cy + 1, cx - 2, cy + 9, cx + 5, cy - 1, cx, cy - 1, fill=c, outline="")
        elif self.icon == "sun":
            self.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=c, outline="")
            for deg in range(0, 360, 45):
                rad = math.radians(deg)
                self.create_line(cx + 7 * math.cos(rad), cy + 7 * math.sin(rad),
                                 cx + 10 * math.cos(rad), cy + 10 * math.sin(rad), fill=c, width=2)

    def draw(self) -> None:
        """Zeichnet den aktuellen Inhalt des Canvas-Widgets neu."""
        self.delete("all")
        draw_garden_bg(self)
        w, h = self.winfo_width(), self.winfo_height()
        rounded(self, 4, 4, w - 4, h - 4, 18, watermark=self.icon, fill=CARD, outline="")
        rounded(self, 4, 4, w - 4, 16, 6, fill=self.color, outline="")
        self.create_text(16, 32, text=self.title_text, anchor="w", fill=MUTED, font=(FONT, 11, "bold" if self.show_axes else "normal"))
        self._draw_icon(w - 26, 32)
        if not self.hist:
            return
        v = self.hist[-1]
        text, col = self.status_fn(v)

        if self.show_axes:
            self.create_text(16, 62, text=self.fmt.format(v), anchor="w", fill=TEXT, font=(FONT, self.big, "bold"))
            self.create_text(16, 90, text=text, anchor="w", fill=col, font=(FONT, 11, "bold"))
            raw_hi = max(max(self.hist), 10.0)
            step_nice = 20 if raw_hi <= 100 else (50 if raw_hi <= 250 else (100 if raw_hi <= 600 else 250))
            hi = math.ceil(raw_hi / step_nice) * step_nice
            lo = 0.0
            span = max(hi - lo, 1.0)
            x0, x1, y0, y1 = 54, w - 18, 114, h - 28
            for frac, val_lbl in ((1.0, f"{int(hi)} W"), (0.5, f"{int(hi // 2)} W"), (0.0, "0 W")):
                gy = y1 - (y1 - y0) * frac
                self.create_line(x0, gy, x1, gy, fill="#e5e0ce", dash=(3, 3) if frac > 0 else ())
                self.create_text(x0 - 6, gy, text=val_lbl, anchor="e", fill=MUTED, font=(FONT, 8, "bold"))
            self.create_line(x0, y0, x0, y1, fill="#cfc9b4", width=1)
            span_s = max(10, self.hist.maxlen or 60)
            self.create_text(x0, h - 13, text=f"-{span_s}s", anchor="w", fill=MUTED, font=(FONT, 8))
            self.create_text((x0 + x1) / 2, h - 13, text=f"-{span_s // 2}s", anchor="center", fill=MUTED, font=(FONT, 8))
            self.create_text(x1, h - 13, text="jetzt", anchor="e", fill=MUTED, font=(FONT, 8, "bold"))
            if len(self.hist) > 1:
                n = len(self.hist) - 1
                pts = []
                for i, val in enumerate(self.hist):
                    pts += [x0 + (x1 - x0) * i / n, y1 - (y1 - y0) * (val - lo) / span]
                self.create_polygon([x0, y1] + pts + [x1, y1], fill=_soft_tint(self.color, 0.86), outline="")
                wm_k = self.icon or ("temp" if "Temp" in self.title_text else ("hum" if "Luft" in self.title_text else ("soil" if "Boden" in self.title_text else ("volt" if "Spannung" in self.title_text else ("sun" if "Solar" in self.title_text else "")))))
                draw_watermark(self, wm_k, w - 6, h - 6)
                self.create_line(pts, fill=self.color, width=3, smooth=True)
                self.create_oval(pts[-2] - 4.5, pts[-1] - 4.5, pts[-2] + 4.5, pts[-1] + 4.5, fill=self.color, outline="white", width=1.5)
            return

        self.create_text(16, 68, text=self.fmt.format(v), anchor="w", fill=TEXT, font=(FONT, self.big, "bold"))
        self.create_text(16, 102, text=text, anchor="w", fill=col, font=(FONT, 12, "bold"))
        if len(self.hist) > 1:
            lo, hi = min(self.hist), max(self.hist)
            span = max(hi - lo, 0.5)
            x0, x1, y0, y1 = 16, w - 16, 124, h - 16
            n = len(self.hist) - 1
            pts = []
            for i, val in enumerate(self.hist):
                pts += [x0 + (x1 - x0) * i / n, y1 - (y1 - y0) * (val - lo) / span]
            self.create_polygon([x0, y1] + pts + [x1, y1], fill=_soft_tint(self.color, 0.88), outline="")
            wm_k = self.icon or ("temp" if "Temp" in self.title_text else ("hum" if "Luft" in self.title_text else ("soil" if "Boden" in self.title_text else ("volt" if "Spannung" in self.title_text else ("sun" if "Solar" in self.title_text else "")))))
            draw_watermark(self, wm_k, w - 6, h - 6)
            self.create_line(pts, fill=self.color, width=3, smooth=True)
            self.create_oval(pts[-2] - 4.5, pts[-1] - 4.5, pts[-2] + 4.5, pts[-1] + 4.5, fill=self.color, outline="white", width=1.5)


class VerticalTouchSlider(tk.Canvas):
    """Schlanker vertikaler Mischpult-Regler (0..1 in 5%-Schritten)."""

    def __init__(self, master, label, color, command, max_value=1.0, **kw):
        kw.setdefault("width", 62)
        kw.setdefault("height", 10)
        super().__init__(master, bg=BG, highlightthickness=0, **kw)
        self.label, self.color, self.command, self.max_value = label, color, command, max_value
        self.value, self.enabled = 0.0, False
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._touch)
        self.bind("<B1-Motion>", self._touch)

    def _track_y(self):
        return 32, max(44, self.winfo_height() - 32)

    def _touch(self, e) -> None:
        if not self.enabled:
            return
        y_top, y_bot = self._track_y()
        frac = 1.0 - (e.y - y_top) / max(1, (y_bot - y_top))
        v = round(min(1.0, max(0.0, frac)) * 20) / 20
        v = min(v, self.max_value)
        if v != self.value:
            self.value = v
            self.draw()
            self.command(v)

    def set(self, v: float) -> None:
        """Setzt den Schaltzustand (ein/aus) des Touch-Schalters."""
        if abs(v - self.value) > 1e-9:
            self.value = v
            self.draw()

    def set_enabled(self, on: bool) -> None:
        """Aktiviert oder sperrt die Bedienbarkeit des Touch-Schalters."""
        self.enabled = on
        self.draw()

    def draw(self) -> None:
        """Zeichnet den aktuellen Inhalt des Canvas-Widgets neu."""
        self.delete("all")
        draw_garden_bg(self)
        w, h = self.winfo_width(), self.winfo_height()
        cx = w // 2
        y_top, y_bot = self._track_y()
        col = self.color if self.enabled else DISABLED
        self.create_text(cx, 11, text=f"{round(self.value * 100)}%", fill=TEXT, font=(FONT, 9, "bold"))
        rounded(self, cx - 10, y_top, cx + 10, y_bot, 10, fill=TRACK, outline="")
        if self.max_value < 1.0:
            my = y_bot - (y_bot - y_top) * self.max_value
            self.create_rectangle(cx - 10, y_top, cx + 10, my, fill="#d6d0b8", outline="")
            self.create_line(cx - 16, my, cx + 16, my, fill=BAD, width=2)
        ky = y_bot - (y_bot - y_top) * self.value
        if self.value > 0:
            rounded(self, cx - 10, min(ky, y_bot - 20), cx + 10, y_bot, 10, fill=col, outline="")
        self.create_oval(cx - 14, ky - 12, cx + 14, ky + 12, fill="#ffffff", outline=col, width=3)
        self.create_text(cx, h - 9, text=self.label, fill=TEXT, font=(FONT, 8, "bold"))


class TouchSlider(tk.Canvas):
    """Großer Wischregler (0 bis 1 in 5-%-Schritten), per Finger oder Maus bedienbar.

    Nur im aktiven Zustand (enabled) reagiert er auf Berührung. Das Verhalten ist so gedacht:
    Im Automatik-Betrieb ist er gesperrt und zeigt nur an, was die Regelung gerade tut (set());
    im Handbetrieb wird er aktiv und ruft bei jeder Änderung command(wert) auf.

    max_value ist eine Obergrenze (z.B. das Pumpen-Limit): Der Regler lässt sich nicht darüber hinaus
    ziehen. Der gesperrte Bereich wird grau mit roter Markierung dargestellt. Diese Grenze ist zusätzlich
    im Actuator selbst verankert (Actuator.max_level), doppelt hält besser.
    """

    def __init__(self, master, label, color, command, max_value=1.0):
        super().__init__(master, bg=BG, highlightthickness=0, width=10, height=10)
        self.label, self.color, self.command, self.max_value = label, color, command, max_value
        self.value, self.enabled = 0.0, False
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._touch)
        self.bind("<B1-Motion>", self._touch)

    def _track(self):
        """Waagerechter Bereich (von, bis) der Schiene in Pixeln; links ist Platz für die Beschriftung, rechts für den Prozentwert."""
        return 158, self.winfo_width() - 80

    def _touch(self, e) -> None:
        """Berührung oder Ziehen: Position in einen Wert umrechnen, auf 5 % runden, begrenzen und melden, wenn er sich geändert hat."""
        if not self.enabled:
            return
        x0, x1 = self._track()
        v = round(min(1.0, max(0.0, (e.x - x0) / (x1 - x0))) * 20) / 20
        v = min(v, self.max_value)
        if v != self.value:
            self.value = v
            self.draw()
            self.command(v)

    def set(self, v: float) -> None:
        """Wert von außen setzen (z.B. Ist-Wert der Automatik anzeigen). Ruft command NICHT auf."""
        if abs(v - self.value) > 1e-9:
            self.value = v
            self.draw()

    def set_enabled(self, on: bool) -> None:
        """Regler aktivieren (Handbetrieb) oder sperren (Automatik, dann grau)."""
        self.enabled = on
        self.draw()

    def draw(self) -> None:
        """Zeichnet Beschriftung, Schiene, Füllung, Griff, ggf. die Begrenzung und den Prozentwert."""
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        x0, x1 = self._track()
        cy = h // 2
        col = self.color if self.enabled else DISABLED
        self.create_text(8, cy, text=self.label, anchor="w", fill=TEXT, font=(FONT, 13, "bold"))
        rounded(self, x0, cy - 14, x1, cy + 14, 14, fill=TRACK, outline="")
        if self.max_value < 1.0:  # gesperrter Bereich + Markierung
            mx = x0 + (x1 - x0) * self.max_value
            self.create_rectangle(mx, cy - 14, x1, cy + 14, fill="#d6d0b8", outline="")
            self.create_line(mx, cy - 20, mx, cy + 20, fill=BAD, width=3)
        kx = x0 + (x1 - x0) * self.value
        if self.value > 0:
            rounded(self, x0, cy - 14, max(kx, x0 + 28), cy + 14, 14, fill=col, outline="")
        self.create_oval(kx - 20, cy - 20, kx + 20, cy + 20, fill="#ffffff", outline=col, width=4)
        self.create_text(w - 8, cy, text=f"{round(self.value * 100)}%", anchor="e", fill=TEXT, font=(FONT, 16, "bold"))


class StringBar(tk.Canvas):
    """Kachel für ein Solar-Panel: Leistung, Spannung, Strom und ein Balken (Anteil der Spitzenleistung).

    Bei vielen Panels (Kachel niedriger als 80 Pixel) wird eine platzsparende einzeilige Darstellung
    genutzt, damit die Beschriftung nicht überlappt.
    """

    def __init__(self, master, title, peak_w):
        super().__init__(master, bg=BG, highlightthickness=0, width=10, height=10)
        self.title_text, self.peak_w, self.reading = title, peak_w, None
        self.bind("<Configure>", lambda e: self.draw())

    def set_reading(self, sr) -> None:
        """Neuen Messwert (StringReading) setzen und neu zeichnen."""
        self.reading = sr
        self.draw()

    def draw(self) -> None:
        """Zeichnet die normale oder, bei wenig Platz, die kompakte Darstellung."""
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        compact = h < 80
        rounded(self, 4, 4, w - 4, h - 4, 10 if compact else 18, fill=CARD, outline="")
        sr = self.reading
        if compact:
            self.create_text(12, h / 2 - 6, text=self.title_text, anchor="w", fill=MUTED, font=(FONT, 9, "bold"))
            if sr is None:
                return
            self.create_text(w - 12, h / 2 - 6, text=f"{sr.power_w:.0f} W · {sr.voltage_v:.0f} V · {sr.current_a:.1f} A",
                             anchor="e", fill=TEXT, font=(FONT, 10, "bold"))
            x0, x1, y = 12, w - 12, h - 12
            rounded(self, x0, y - 3, x1, y + 3, 3, fill=TRACK, outline="")
            frac = min(1.0, sr.power_w / self.peak_w)
            if frac > 0.03:
                rounded(self, x0, y - 3, x0 + max(8, (x1 - x0) * frac), y + 3, 3, fill=SUN, outline="")
            return
        self.create_text(16, 28, text=self.title_text, anchor="w", fill=MUTED, font=(FONT, 11, "bold"))
        # Kleines Solarmodul-Vektor-Icon oben rechts
        mx, my = w - 28, 28
        self.create_rectangle(mx - 11, my - 8, mx + 11, my + 8, fill="#244b7a", outline=SUN, width=1.5)
        self.create_line(mx - 4, my - 8, mx - 4, my + 8, fill="#7fb3e8", width=1)
        self.create_line(mx + 4, my - 8, mx + 4, my + 8, fill="#7fb3e8", width=1)
        self.create_line(mx - 11, my, mx + 11, my, fill="#7fb3e8", width=1)
        if sr is None:
            return
        self.create_text(16, 62, text=f"{sr.power_w:.0f} W", anchor="w", fill=TEXT, font=(FONT, 22, "bold"))
        self.create_text(w - 16, 62, text=f"{sr.voltage_v:.0f} V · {sr.current_a:.1f} A",
                         anchor="e", fill=MUTED, font=(FONT, 12))
        x0, x1, y = 16, w - 16, h - 30
        rounded(self, x0, y - 9, x1, y + 9, 9, fill=TRACK, outline="")
        frac = min(1.0, sr.power_w / self.peak_w)
        if frac > 0.03:
            rounded(self, x0, y - 9, x0 + max(20, (x1 - x0) * frac), y + 9, 9, fill=SUN, outline="")


class BatteryGauge(tk.Canvas):
    """Kreisförmige Ladezustands- und Leistungsanzeige (SOC %) für die 48-V-Speicherbank."""
    def __init__(self, master):
        super().__init__(master, bg=BG, highlightthickness=0, width=10, height=10)
        self.soc, self.status, self.color, self.status_color = 0.0, "", GOOD, MUTED
        self.current_a = 0.0
        self.phase = 0.0
        self.bind("<Configure>", lambda e: self.draw())

    def set_state(self, soc, status, color, status_color, current_a: float = 0.0) -> None:
        """Aktualisiert Zustand, Messwerte und Statusfarben des Widgets."""
        self.soc, self.status, self.color, self.status_color = soc, status, color, status_color
        self.current_a = current_a
        self.draw()

    def step_anim(self) -> None:
        """Führt einen Animationsschritt des Widgets aus."""
        if abs(self.current_a) <= 0.2 or self.soc <= 1.0:
            return
        speed = min(0.08, max(0.02, abs(self.current_a) / 180.0))
        self.phase = (self.phase + speed) % 1.0 if self.current_a > 0.2 else (self.phase - speed) % 1.0
        if self.winfo_ismapped():
            self.draw()

    def draw(self) -> None:
        """Zeichnet den aktuellen Inhalt des Canvas-Widgets neu."""
        self.delete("all")
        draw_garden_bg(self)
        w, h = self.winfo_width(), self.winfo_height()
        rounded(self, 4, 4, w - 4, h - 4, 18, watermark="battery", fill=CARD, outline="")
        self.create_text(16, 28, text="Ladezustand", anchor="w", fill=MUTED, font=(FONT, 11))
        cx, cy = w / 2, h / 2 + 10
        r = max(20, min(w / 2, h / 2) - 38)
        box = (cx - r, cy - r, cx + r, cy + r)
        ring_col = soc_color(self.soc)
        self.create_arc(*box, start=225, extent=-270, style="arc", width=20, outline=TRACK)
        if self.soc > 0:
            arc_span = 270.0 * self.soc / 100.0
            self.create_arc(*box, start=225, extent=-arc_span, style="arc", width=20, outline=ring_col)
            if abs(self.current_a) > 0.2 and arc_span > 12:
                dot_step = 26.0
                n_dots = int(arc_span // dot_step) + 1
                blink = int(time.monotonic() * 4.5) % 2 == 0
                bolt_col = ("#ffffff" if blink else "#2ecc71") if self.current_a > 0.2 else ("#ffffff" if blink else "#ff4d4d")
                for k in range(n_dots):
                    deg_offset = (k + self.phase) * dot_step
                    if deg_offset <= arc_span - 4:
                        ang_rad = math.radians(225.0 - deg_offset)
                        px = cx + r * math.cos(ang_rad)
                        py = cy - r * math.sin(ang_rad)
                        _draw_bolt(self, px, py, 1.15, bolt_col)
        self.create_text(cx, cy - 6, text=f"{self.soc:.0f} %", fill=TEXT, font=(FONT, 32, "bold"))
        self.create_text(cx, cy + 32, text=self.status, fill=self.status_color, font=(FONT, 13, "bold"))


class InfoList(tk.Canvas):
    """Kachel mit Zeilen (Bezeichnung links, Wert rechts). Wird aktuell nicht mehr benutzt, seit die
    Akku-Seite die BatterySummaryCard verwendet; bleibt als Reserve erhalten.
    """

    def __init__(self, master, title):
        super().__init__(master, bg=BG, highlightthickness=0, width=10, height=10)
        self.title_text, self.rows = title, []
        self.bind("<Configure>", lambda e: self.draw())

    def set_rows(self, rows) -> None:
        """Zeilen setzen: Liste aus (Bezeichnung, Wert, Farbe) und neu zeichnen."""
        self.rows = rows
        self.draw()

    def draw(self) -> None:
        """Zeichnet die Karte mit gleichmäßig verteilten Zeilen und feinen Trennlinien."""
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        rounded(self, 4, 4, w - 4, h - 4, 18, watermark="sun", fill=CARD, outline="")
        self.create_text(16, 28, text=self.title_text, anchor="w", fill=MUTED, font=(FONT, 11))
        n = len(self.rows)
        if not n:
            return
        top = 46
        rh = (h - top - 12) / n
        for i, (label, value, col) in enumerate(self.rows):
            y = top + rh * (i + 0.5)
            self.create_text(16, y, text=label, anchor="w", fill=MUTED, font=(FONT, 12))
            self.create_text(w - 16, y, text=value, anchor="e", fill=col, font=(FONT, 15, "bold"))
            if i < n - 1:
                self.create_line(16, y + rh / 2, w - 16, y + rh / 2, fill="#eee8d6")


class OutletInfo(tk.Canvas):
    """Anzeige einer 230-V-Steckdose mit Schuko-Vektorgrafik, Schloss-Symbol und Timer-Balken."""

    def __init__(self, master, title):
        super().__init__(master, bg=BG, highlightthickness=0, width=10, height=10)
        self.title_text = title
        self.st = dict(on=False, load=None, remaining=None, minutes=30, locked=False, reason="")
        self.bind("<Configure>", lambda e: self.draw())

    def set_state(self, **kw) -> None:
        """Aktualisiert Zustand, Messwerte und Statusfarben des Widgets."""
        self.st.update(kw)
        self.draw()

    def _draw_socket_icon(self, cx: float, cy: float, on: bool, locked: bool) -> None:
        ring = GOOD if on else (BAD if locked else "#b8c2b6")
        bg_in = _soft_tint(GOOD, 0.85) if on else (_soft_tint(BAD, 0.88) if locked else "#f2efe4")
        self.create_oval(cx - 26, cy - 26, cx + 26, cy + 26, fill=bg_in, outline=ring, width=3)
        if locked:
            # Rotes Vorhängeschloss
            self.create_arc(cx - 8, cy - 15, cx + 8, cy + 1, start=0, extent=180, style="arc", width=3, outline=BAD)
            rounded(self, cx - 11, cy - 5, cx + 11, cy + 13, 4, fill=BAD, outline="")
            self.create_oval(cx - 2.5, cy + 1, cx + 2.5, cy + 6, fill="white", outline="")
        else:
            # Schuko-Steckdose (2 Buchsen + Schutzkontakte)
            self.create_line(cx, cy - 24, cx, cy - 18, fill=ring, width=3)
            self.create_line(cx, cy + 18, cx, cy + 24, fill=ring, width=3)
            pin_col = "#1f2a1f" if on else "#7a8a7a"
            self.create_oval(cx - 12, cy - 4, cx - 5, cy + 4, fill=pin_col, outline="")
            self.create_oval(cx + 5, cy - 4, cx + 12, cy + 4, fill=pin_col, outline="")
            if on:
                _draw_bolt(self, cx, cy - 13, 0.75, SUN)

    def draw(self) -> None:
        """Zeichnet den aktuellen Inhalt des Canvas-Widgets neu."""
        self.delete("all")
        draw_garden_bg(self)
        w, h = self.winfo_width(), self.winfo_height()
        s = self.st
        rounded(self, 4, 4, w - 4, h - 4, 18, watermark="socket", fill=CARD, outline="")
        top_bar = GOOD if s["on"] else (BAD if s["locked"] else TRACK)
        rounded(self, 4, 4, w - 4, 14, 5, fill=top_bar, outline="")
        self.create_text(16, 30, text=f"{self.title_text} · 230 V AC", anchor="w", fill=MUTED, font=(FONT, 11, "bold"))

        self._draw_socket_icon(w - 46, 56, s["on"], s["locked"])

        if s["on"]:
            txt, col = "AN", GOOD
        elif s["locked"]:
            txt, col = "GESPERRT", BAD
        else:
            txt, col = "AUS", MUTED
        self.create_text(16, 68, text=txt, anchor="w", fill=col, font=(FONT, 26, "bold"))

        if s["on"]:
            if s["load"] is not None:
                self.create_text(16, 104, text=f"Last: {s['load']:.0f} W", anchor="w", fill=TEXT, font=(FONT, 14, "bold"))
            if s["remaining"] is not None:
                m, sec = divmod(int(s["remaining"]), 60)
                self.create_text(w - 16, 104, text=f"noch {m} min {sec:02d} s", anchor="e", fill=MUTED, font=(FONT, 11, "bold"))
                # Countdown-Balken unten
                total_s = max(60.0, float(s["minutes"]) * 60.0)
                frac = min(1.0, max(0.0, float(s["remaining"]) / total_s))
                x0, x1, y = 16, w - 16, h - 18
                rounded(self, x0, y - 4, x1, y + 4, 4, fill=TRACK, outline="")
                if frac > 0.02:
                    rounded(self, x0, y - 4, x0 + max(10, (x1 - x0) * frac), y + 4, 4, fill=GOOD, outline="")
        elif s["locked"]:
            self.create_text(16, 108, text=s["reason"], anchor="w", fill=BAD, font=(FONT, 11, "bold"))
        else:
            self.create_text(16, 108, text=f"Gewählter Timer: {dur_label(s['minutes'])}", anchor="w", fill=MUTED, font=(FONT, 11))


# --- Seiten (Menüs) ----------------------------------------------------------
class GreenhousePage(tk.Frame):
    """Hauptseite für Gewächshaus-Klima, 3 Lüfter (L1-L3), 12-V-Pumpe und Live-Wetterkarte."""
    def __init__(self, master, ctrl: Controller, cfg: Config, history: int, env=None):
        super().__init__(master, bg=BG)
        self.ctrl = ctrl
        self.env = env
        self.env = env
        self.out_temp = 20.0
        self.out_clouds = 20.0
        self.out_rad = 0.0
        self.out_wind = 10.0
        self.is_live_weather = False
        self.loc_name = LOCATION_NAME
        self._bg_weather = SiegenWeatherClient() if env is None else None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=3, uniform="gp_r")
        self.rowconfigure(1, weight=2, uniform="gp_r")

        frame = tk.Frame(self, bg=BG)
        frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        frame.rowconfigure(0, weight=1)
        specs = [
            ("Temperatur", "{:.1f} °C", "#e2762f", lambda v: temp_status(v, cfg)),
            ("Luftfeuchte", "{:.0f} %", BLUE, lambda v: hum_status(v, cfg)),
            ("Bodenfeuchte", "{:.0f} %", BROWN, lambda v: soil_status(v, cfg)),
            ("Steuerspannung", "{:.2f} V", LEAF, lambda v: batt_status(v, cfg)),
        ]
        self.cards = []
        for i, (title, fmt, color, fn) in enumerate(specs):
            frame.columnconfigure(i, weight=1, uniform="c")
            card = Card(frame, title, fmt, color, fn, history)
            card.grid(row=0, column=i, sticky="nsew", padx=4)
            self.cards.append(card)

        ctl = tk.Frame(self, bg=BG)
        ctl.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 6))
        ctl.rowconfigure(0, weight=1)

        self.mode = Button(ctl, lambda: self.set_manual(not ctrl.manual), text="AUTO",
                           sub="", color=GOOD, fsize=16, width=102)
        self.mode.grid(row=0, column=0, sticky="nsew", padx=(4, 6), pady=4)

        self.s_fans = []
        for idx in range(3):
            ctl.columnconfigure(idx + 1, weight=0, minsize=62)
            sf = VerticalTouchSlider(ctl, f"Lüfter {idx + 1}", BLUE, lambda v, i=idx: self._set_single_fan(i, v), width=62)
            sf.grid(row=0, column=idx + 1, sticky="nsew", padx=1)
            self.s_fans.append(sf)

        ctl.columnconfigure(4, weight=0, minsize=62)
        def _on_pump_slider(v: float) -> None:
            if getattr(ctrl, "pump_fault", None) or getattr(tk._default_root, "_app_ctrl_error", False):
                ctrl.pump.off()
                self.after_idle(lambda: self.s_pump.set(0.0))
                return
            ctrl.pump.set_level(v)
        self.s_pump = VerticalTouchSlider(ctl, "Pumpe", BROWN, _on_pump_slider, max_value=cfg.pump_limit, width=62)
        self.s_pump.grid(row=0, column=4, sticky="nsew", padx=(4, 6))

        ctl.columnconfigure(5, weight=1)
        self.weather_slot = tk.Canvas(ctl, bg=BG, highlightthickness=0)
        self.weather_slot.grid(row=0, column=5, sticky="nsew", padx=(4, 4), pady=4)
        self.weather_slot.bind("<Configure>", lambda e: self._draw_weather_card())

    def _weather_scene(self) -> str:
        if self.out_wind >= 45.0:
            return "storm"
        if self.out_temp <= 1.5 and self.out_clouds >= 65.0:
            return "snow"
        if self.out_clouds >= 80.0:
            return "rain"
        if self.out_clouds >= 35.0:
            return "cloudy"
        if self.out_rad < 5.0 and self.is_live_weather:
            return "night"
        return "sunny"

    def _draw_weather_card(self) -> None:
        c = self.weather_slot
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 80 or h < 60:
            return
        scene = self._weather_scene()
        themes = {
            "sunny":  ("#fff2c6", "#2b3a2b", "#b87a14"),
            "cloudy": ("#dce8f2", "#233240", "#4b6580"),
            "rain":   ("#384e63", "#ffffff", "#b8d4ee"),
            "storm":  ("#273240", "#ffffff", "#ffd166"),
            "snow":   ("#465d75", "#ffffff", "#d8ecff"),
            "night":  ("#1e2d44", "#ffffff", "#9ec5f0"),
        }
        bg_col, fg_main, fg_sub = themes[scene]
        rounded(c, 3, 3, w - 3, h - 3, 16, fill=bg_col, outline="")

        rx, ry = w - 58, h * 0.43
        if scene == "sunny":
            c.create_oval(rx - 21, ry - 21, rx + 21, ry + 21, fill="#ffe082", outline="")
            c.create_oval(rx - 13, ry - 13, rx + 13, ry + 13, fill=SUN, outline="")
            for deg in range(0, 360, 30):
                rad = math.radians(deg)
                c.create_line(rx + 16 * math.cos(rad), ry + 16 * math.sin(rad),
                                 rx + 23 * math.cos(rad), ry + 23 * math.sin(rad), fill=SUN, width=2)
        elif scene == "cloudy":
            c.create_oval(rx - 19, ry - 17, rx - 1, ry + 1, fill=SUN, outline="")
            c.create_oval(rx - 22, ry - 7, rx + 22, ry + 12, fill="#ffffff", outline="")
            c.create_oval(rx - 11, ry - 16, rx + 12, ry + 5, fill="#ffffff", outline="")
        elif scene == "rain":
            c.create_oval(rx - 22, ry - 16, rx + 22, ry + 3, fill="#6c849c", outline="")
            c.create_oval(rx - 11, ry - 23, rx + 12, ry - 4, fill="#7c94ac", outline="")
            for dx in (-12, -3, 6, 15):
                c.create_line(rx + dx, ry + 7, rx + dx - 5, ry + 20, fill="#64b5f6", width=2, capstyle="round")
        elif scene == "storm":
            c.create_oval(rx - 22, ry - 17, rx + 22, ry + 2, fill="#4c5c6e", outline="")
            c.create_polygon(rx - 2, ry + 2, rx - 9, ry + 13, rx - 2, ry + 13, rx - 6, ry + 22,
                             rx + 6, ry + 10, rx - 1, ry + 10, fill="#ffd166", outline="")
            c.create_line(rx - 24, ry + 19, rx + 18, ry + 19, fill="#90a4ae", width=2, capstyle="round")
        elif scene == "snow":
            c.create_oval(rx - 22, ry - 16, rx + 22, ry + 3, fill="#8fa8c2", outline="")
            for sx, sy in ((rx - 12, ry + 10), (rx, ry + 18), (rx + 12, ry + 11), (rx - 5, ry + 22)):
                c.create_oval(sx - 2.5, sy - 2.5, sx + 2.5, sy + 2.5, fill="#ffffff", outline="")
        elif scene == "night":
            c.create_oval(rx - 13, ry - 13, rx + 13, ry + 13, fill="#ffd54f", outline="")
            c.create_oval(rx - 5, ry - 16, rx + 17, ry + 8, fill=bg_col, outline="")
            for sx, sy in ((rx - 22, ry - 10), (rx - 18, ry + 12), (rx + 16, ry - 15)):
                c.create_oval(sx - 2, sy - 2, sx + 2, sy + 2, fill="#ffffff", outline="")

        scene_labels = {"sunny": "SONNIG", "cloudy": "BEWÖLKT", "rain": "REGEN",
                        "storm": "STURM", "snow": "SCHNEE", "night": "KLARE NACHT"}
        src_tag = f"LIVE · {self.loc_name.upper()}" if self.is_live_weather else "SIMULATION"
        c.create_text(14, 19, text=f"{src_tag} · {scene_labels[scene]}", anchor="w", fill=fg_sub, font=(FONT, 8, "bold"))
        c.create_text(14, h * 0.49, text=f"{self.out_temp:.1f} °C", anchor="w", fill=fg_main, font=(FONT, 22, "bold"))
        c.create_text(14, h - 18, text=f"Wolken {self.out_clouds:.0f}% · {self.out_rad:.0f} W/m²",
                      anchor="w", fill=fg_sub, font=(FONT, 9, "bold"))

    def _set_single_fan(self, idx: int, v: float) -> None:
        if getattr(tk._default_root, "_app_ctrl_error", False):
            self.ctrl.fan._single_levels = [0.0, 0.0, 0.0]
            if tk._default_root is not None:
                tk._default_root._fan_levels = [0.0, 0.0, 0.0]
            self.ctrl.fan.off()
            for sf in self.s_fans:
                sf.set(0.0)
            return
        ffaults = getattr(tk._default_root, "_fan_faults", set())
        if idx in ffaults:
            self.after_idle(lambda: self.s_fans[idx].set(0.0))
            return
        vals = [0.0 if i in ffaults else (v if i == idx else sf.value) for i, sf in enumerate(self.s_fans)]
        self.ctrl.fan._single_levels = list(vals)
        if tk._default_root is not None:
            tk._default_root._fan_levels = list(vals)
        self.ctrl.fan.set_level(sum(vals) / len(vals))
        if hasattr(self.ctrl.fan, "log_fan_state"):
            self.ctrl.fan.log_fan_state()

    def set_manual(self, manual: bool) -> None:
        """Schaltet zwischen Automatik- und Handbetrieb um und aktualisiert die Schalter."""
        self.ctrl.set_manual(manual)
        self.mode.look(text="HAND" if manual else "AUTO", color=WARN if manual else GOOD)
        for sf in self.s_fans:
            sf.set_enabled(manual)
        self.s_pump.set_enabled(manual)

    def refresh(self, r, pr: PowerReading) -> None:
        """Aktualisiert alle Anzeigen und Steuerelemente der Seite mit den neuesten Daten."""
        for card, v in zip(self.cards, (r.temperature_c, r.humidity_pct, r.soil_moisture_pct, r.battery_v)):
            card.push(v)
        self._sensor_blocked = bool(getattr(tk._default_root, "_app_ctrl_error", False))
        if getattr(self.ctrl, "pump_fault", None) or self._sensor_blocked:
            self.ctrl.pump.off()
            self.s_pump.set(0.0)
        if self._sensor_blocked:
            self.ctrl.fan.off()
            self.ctrl.pump.off()
            self.s_pump.set(0.0)
            for sf in self.s_fans:
                sf.set(0.0)
        ffaults = getattr(tk._default_root, "_fan_faults", set())
        for fi in ffaults:
            if 0 <= fi < len(self.s_fans):
                self.s_fans[fi].set(0.0)
        if self._sensor_blocked:
            cur_lvls = [0.0, 0.0, 0.0]
        elif not self.ctrl.manual:
            cur_lvls = [0.0 if i in ffaults else self.ctrl.fan.level for i in range(3)]
            for i, sf in enumerate(self.s_fans):
                sf.set(cur_lvls[i])
            self.s_pump.set(self.ctrl.pump.level)
        else:
            cur_lvls = [0.0 if i in ffaults else sf.value for i, sf in enumerate(self.s_fans)]
        self.ctrl.fan._single_levels = list(cur_lvls)
        if tk._default_root is not None:
            tk._default_root._fan_levels = list(cur_lvls)
        if self.env is not None:
            self.is_live_weather = bool(getattr(self.env, "live_siegen", False))
            if self.is_live_weather:
                self.out_temp = float(self.env.outside_temp_base_c)
                self.out_clouds = float(self.env.cloud_cover_pct)
                self.out_rad = float(self.env.live_radiation_wm2 or 0.0)
            else:
                hr = self.env.manual_hour if self.env.manual_hour is not None else ((6.0 + (time.monotonic() % 600.0) / 600.0 * 24.0) % 24.0)
                phase = ((hr - 6.0) / 24.0) * 2 * math.pi
                self.out_temp = self.env.outside_temp_base_c + self.env.temp_swing_c * math.sin(phase)
                self.out_clouds = float(self.env.cloud_cover_pct)
                self.out_rad = max(0.0, math.sin(phase)) * (1.0 - self.out_clouds / 100.0) * 850.0
                self.out_wind = 32.0 if self.out_clouds > 75 else 12.0
            self._draw_weather_card()
        elif self._bg_weather is not None:
            wd, _ = self._bg_weather.get_latest()
            if wd is not None:
                self.is_live_weather = True
                self.out_temp, self.out_clouds, self.out_rad, self.out_wind = wd.temperature_c, wd.cloud_cover_pct, wd.radiation_wm2, wd.wind_kmh
                self._draw_weather_card()


class EnergyFlowCanvas(tk.Canvas):
    """Animierter Energiefluss: SOLAR, AKKU und LAST als drei Knoten, verbunden durch Leitungen mit
    wandernden Punkten.

    Bedeutung der Punkte: gelb = Solarstrom fließt zum Akku/Verbrauch, grün = der Akku wird geladen,
    orange = der Akku wird entladen (versorgt die Verbraucher), blau = Verbraucher werden direkt aus
    Solar versorgt. Je mehr Leistung, desto schneller wandern die Punkte (begrenzt auf 0,4 bis 2,5).

    Die Punkte bewegen sich über gespeicherte Positionen (offsets) je Leitung, die stetig weiterlaufen.
    Dadurch ruckeln sie nicht, wenn ein Durchlauf endet. step_anim() wird von der Oberfläche etwa 12-mal
    pro Sekunde aufgerufen, solange die Solar-Seite sichtbar ist.
    """

    def __init__(self, master):
        super().__init__(master, bg=BG, highlightthickness=0, height=78)
        self.solar_w = 0.0
        self.batt_w = 0.0
        self.batt_soc = 70.0
        self.load_w = 0.0
        self.offsets: dict[str, float] = {}   # Punkt-Position je Leitung (0..1), läuft stetig weiter
        self.speeds: dict[str, float] = {}
        self.bind("<Configure>", lambda e: self.draw())

    def set_values(self, solar_w: float, batt_w: float, batt_soc: float, load_w: float) -> None:
        """Aktuelle Leistungswerte setzen (Solar, Akku, Ladezustand, Verbrauch). Gezeichnet wird beim nächsten Animationsschritt."""
        self.solar_w, self.batt_w, self.batt_soc, self.load_w = solar_w, batt_w, batt_soc, load_w

    def step_anim(self) -> None:
        """Ein Animationsschritt: Punkt-Positionen weiterschieben und neu zeichnen."""
        for key, sp in self.speeds.items():
            self.offsets[key] = (self.offsets.get(key, 0.0) + 0.06 * sp) % 1.0
        self.draw()

    def _draw_dots(self, key: str, x0: float, x1: float, y: float, power_w: float, color: str) -> None:
        """Zeichnet die Punkte einer Leitung (key); Geschwindigkeit hängt von der Leistung ab. Bei weniger als 5 W gibt es keine Punkte."""
        if abs(power_w) < 5.0:
            return
        speed = min(2.5, max(0.4, abs(power_w) / 350.0))
        self.speeds[key] = speed
        shift = self.offsets.get(key, 0.0)
        step = 26.0
        dist = abs(x1 - x0)
        direction = 1.0 if x1 > x0 else -1.0
        n = int(dist // step) + 1
        for i in range(n):
            pos = i * step + shift * step
            if pos <= dist:
                px = x0 + direction * pos
                _draw_bolt(self, px, y, 0.9, color)

    def draw(self) -> None:
        """Zeichnet Leitungen, Punkte und die drei Knoten (Solar, Akku, Last) mit ihren Werten."""
        self.delete("all")
        self.speeds.clear()
        w, h = self.winfo_width(), self.winfo_height()
        if w < 100:
            return
        rounded(self, 4, 4, w - 4, h - 4, 16, fill=CARD, outline="")
        cy = h / 2 + 2
        x_sun, x_bat, x_load = 78, w / 2, w - 78

        # Leitungen (Hintergrund)
        self.create_line(x_sun + 46, cy, x_bat - 52, cy, fill=TRACK, width=6, capstyle="round")
        self.create_line(x_bat + 52, cy, x_load - 46, cy, fill=TRACK, width=6, capstyle="round")

        # Animierte Energie-Punkte
        if self.solar_w > 5:
            self._draw_dots("solar", x_sun + 48, x_bat - 54, cy, self.solar_w, SUN)
        if self.batt_w > 10:
            # Akku lädt (grüne Punkte fließen zum Akku)
            self._draw_dots("charge", x_bat - 54, x_bat - 18, cy - 8, self.batt_w, GOOD)
        elif self.batt_w < -10:
            # Akku entlädt (orangene Punkte fließen vom Akku zu den Verbrauchern)
            self._draw_dots("discharge", x_bat + 54, x_load - 48, cy, abs(self.batt_w), WARN)
        if self.load_w > 5 and self.batt_w >= -10:
            # Direktverbrauch aus Solar (blaue Punkte zum Haus)
            self._draw_dots("direct", x_bat + 54, x_load - 48, cy, self.load_w, BLUE)

        # Knoten 1: Solar
        rounded(self, x_sun - 46, cy - 22, x_sun + 46, cy + 22, 12, fill="#fff6e0", outline=SUN, width=2)
        self.create_text(x_sun, cy - 8, text="SOLAR", fill=MUTED, font=(FONT, 8, "bold"))
        self.create_text(x_sun, cy + 8, text=f"{self.solar_w:.0f} W", fill=TEXT, font=(FONT, 11, "bold"))

        # Knoten 2: Akku
        bcol = BAD if self.batt_soc < 20 else WARN if self.batt_soc < 40 else GOOD
        rounded(self, x_bat - 52, cy - 24, x_bat + 52, cy + 24, 12, fill="#eef7ee", outline=bcol, width=2)
        self.create_text(x_bat, cy - 9, text=f"AKKU {self.batt_soc:.0f}%", fill=MUTED, font=(FONT, 8, "bold"))
        sign_txt = f"{self.batt_w:+.0f} W" if abs(self.batt_w) >= 5 else "0 W"
        self.create_text(x_bat, cy + 8, text=sign_txt, fill=bcol, font=(FONT, 11, "bold"))

        # Knoten 3: Verbrauch
        rounded(self, x_load - 46, cy - 22, x_load + 46, cy + 22, 12, fill="#eef5fa", outline=BLUE, width=2)
        self.create_text(x_load, cy - 8, text="LAST", fill=MUTED, font=(FONT, 8, "bold"))
        self.create_text(x_load, cy + 8, text=f"{self.load_w:.0f} W", fill=TEXT, font=(FONT, 11, "bold"))


class SolarPage(tk.Frame):
    """Menü "Solar": Energiefluss oben, links die Gesamtleistung mit Verlauf, rechts je ein Balken pro Panel.

    In der Simulation lässt sich die Anzahl der Panels (1 bis 6) mit den Knöpfen +/- ändern, um
    "was wäre wenn" auszuprobieren (die Quelle muss dafür num_strings anbieten, was SimPower tut). Bei
    echten Quellen (VE.Direct, Modbus) entscheidet die Konfiguration, die Knöpfe haben dort keine Wirkung.
    """
    def __init__(self, master, pcfg: PowerConfig, history: int, source=None):
        super().__init__(master, bg=BG)
        self.pcfg = pcfg
        self.source = source
        self.last: PowerReading | None = None
        self.columnconfigure(0, weight=2, uniform="s")
        self.columnconfigure(1, weight=1, uniform="s")
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=0)
        self.rowconfigure(2, weight=1)

        self.flow = EnergyFlowCanvas(self)
        self.flow.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 2))

        self.total = Card(self, "Solarleistung gesamt", "{:.0f} W", SUN, self._status, history, big=28)
        self.total.show_axes = True
        self.total.grid(row=1, column=0, rowspan=2, sticky="nsew", padx=(8, 4), pady=(2, 8))

        # Steuerleiste rechts oben: Panels hinzufügen / entfernen (1..6)
        ctrl = tk.Frame(self, bg=BG)
        ctrl.grid(row=1, column=1, sticky="ew", padx=(4, 8), pady=(2, 2))
        ctrl.columnconfigure(1, weight=1)
        Button(ctrl, lambda: self._change_panels(-1), text="−", color=HEADER, fsize=14, width=56, height=44).grid(row=0, column=0)
        self.lbl_panels = tk.Label(ctrl, text=f"{pcfg.strings} Panels", bg=BG, fg=TEXT, font=(FONT, 11, "bold"))
        self.lbl_panels.grid(row=0, column=1)
        Button(ctrl, lambda: self._change_panels(1), text="+", color=HEADER, fsize=14, width=56, height=44).grid(row=0, column=2)

        self.bars_host = tk.Frame(self, bg=BG)
        self.bars_host.grid(row=2, column=1, sticky="nsew", padx=(4, 8), pady=(2, 8))
        self.bars_host.columnconfigure(0, weight=1)
        self.bars: list[StringBar] = []
        self._rebuild_bars(pcfg.strings)

    def _change_panels(self, delta: int) -> None:
        """Anzahl der Panels in der Simulation um delta ändern (1 bis 6); bei echten Quellen ohne Wirkung."""
        if self.source is None or not hasattr(self.source, "num_strings"):
            return
        new_n = max(1, min(6, self.source.num_strings + delta))
        self.source.num_strings = new_n
        self._rebuild_bars(new_n)

    def _rebuild_bars(self, count: int) -> None:
        """Balken für count Panels neu aufbauen (nur wenn sich die Anzahl geändert hat)."""
        if len(self.bars) == count:
            return
        for b in self.bars:
            b.destroy()
        self.bars.clear()
        for r in range(6):
            self.bars_host.rowconfigure(r, weight=0)
        kwp = count * self.pcfg.string_peak_w / 1000.0
        self.lbl_panels.config(text=f"{count} Panel" + ("s" if count > 1 else "") + f" · {kwp:.1f} kWp")
        for i in range(count):
            self.bars_host.rowconfigure(i, weight=1)
            bar = StringBar(self.bars_host, f"Panel {i + 1}", self.pcfg.string_peak_w)
            bar.grid(row=i, column=0, sticky="nsew", pady=(0 if i == 0 else 4, 0))
            self.bars.append(bar)

    def _status(self, v):
        """Statuszeile der Gesamtleistung: 'erzeugt · heute x kWh' bei Sonne, sonst 'Nacht · heute x kWh'."""
        kwh = self.last.solar_energy_today_kwh if self.last else 0.0
        return (f"erzeugt · heute {kwh:.1f} kWh", GOOD) if v > 5 else (f"Nacht · heute {kwh:.1f} kWh", MUTED)

    def refresh(self, r, pr: PowerReading) -> None:
        """Neue Messwerte übernehmen: Energiefluss, Gesamtleistung und die Balken je Panel; baut Balken bei geänderter Panel-Zahl um."""
        self.last = pr
        if len(pr.strings) != len(self.bars):
            self._rebuild_bars(len(pr.strings))
        self.flow.set_values(pr.solar_power_w, pr.battery_power_w, pr.battery_soc_pct, pr.load_w)
        self.total.push(pr.solar_power_w)
        for bar, sr in zip(self.bars, pr.strings):
            bar.set_reading(sr)


class BatteryPackBar(tk.Canvas):
    """Einzelpack-Balkenanzeige mit Spannung, Ladezustand und Temperaturwarnung."""
    def __init__(self, master, title: str):
        super().__init__(master, bg=BG, highlightthickness=0, width=10, height=10)
        self.title_text = title
        self.pack_data = None
        self.phase = 0.0
        self.bind("<Configure>", lambda e: self.draw())

    def set_reading(self, bp) -> None:
        """Übernimmt neue Batterie- und Pack-Messwerte für die Balkenanzeige."""
        self.pack_data = bp
        self.draw()

    def step_anim(self) -> None:
        """Führt einen Animationsschritt des Widgets aus."""
        bp = self.pack_data
        cur = getattr(bp, "current_a", 0.0) if bp is not None else 0.0
        if cur is None or abs(cur) <= 0.2 or bp.soc_pct <= 1.0:
            return
        sp = min(0.09, max(0.025, abs(cur) / 140.0))
        self.phase = (self.phase + sp) % 1.0 if cur > 0.2 else (self.phase - sp) % 1.0
        if self.winfo_ismapped():
            self.draw()

    def draw(self) -> None:
        """Zeichnet den aktuellen Inhalt des Canvas-Widgets neu."""
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        rounded(self, 3, 3, w - 3, h - 3, 14, fill=CARD, outline="")
        self.create_text(10, 18, text=self.title_text, anchor="w", fill=MUTED, font=(FONT, 10, "bold"))
        bp = self.pack_data
        if bp is None:
            self.create_text(w / 2, h / 2 + 6, text="keine Einzeldaten", fill=MUTED, font=(FONT, 9))
            return
        col = soc_color(bp.soc_pct)
        self.create_text(w - 10, 18, text=f"{bp.soc_pct:.0f}%", anchor="e", fill=col, font=(FONT, 11, "bold"))
        self.create_text(10, 42, text=f"{bp.voltage_v:.2f} V", anchor="w", fill=TEXT, font=(FONT, 14, "bold"))
        t_txt = "–" if math.isnan(bp.temp_c) else f"{bp.temp_c:.1f}°"
        self.create_text(w - 10, 42, text=t_txt, anchor="e", fill=MUTED, font=(FONT, 10))
        x0, x1, y = 10, w - 10, h - 14
        rounded(self, x0, y - 6, x1, y + 6, 6, fill=TRACK, outline="")
        frac = min(1.0, max(0.0, bp.soc_pct / 100.0))
        if frac > 0.03:
            fx1 = x0 + max(14, (x1 - x0) * frac)
            rounded(self, x0, y - 6, fx1, y + 6, 6, fill=col, outline="")
            cur = getattr(bp, "current_a", 0.0) or 0.0
            if abs(cur) > 0.2 and (fx1 - x0) > 24 and not (cur > 0 and bp.temp_c < 2.0):
                blink = int(time.monotonic() * 4.5) % 2 == 0
                bcol = ("#ffffff" if blink else "#2ecc71") if cur > 0.2 else ("#ffffff" if blink else "#ff4d4d")
                for k in range(int((fx1 - x0) // 28.0) + 1):
                    bx = x0 + 10 + (k + self.phase) * 28.0
                    if bx <= fx1 - 8:
                        _draw_bolt(self, bx, y, 0.85, bcol)


class BatterySummaryCard(tk.Canvas):
    """Überlappungsfreie 3-Spalten-Anzeige für Spannung, Strom, Leistung & Restzeit."""

    def __init__(self, master):
        super().__init__(master, bg=BG, highlightthickness=0, width=10, height=10)
        self.v_txt = "–"
        self.i_txt = "–"
        self.p_txt = "–"
        self.p_col = TEXT
        self.load_txt = "–"
        self.time_txt = "–"
        self.bind("<Configure>", lambda e: self.draw())

    def set_data(self, v_txt: str, i_txt: str, p_txt: str, p_col: str, load_txt: str, time_txt: str) -> None:
        """Anzeige-Texte setzen (Spannung, Strom, Leistung samt Farbe, Verbrauch, Restlaufzeit) und neu zeichnen."""
        self.v_txt, self.i_txt, self.p_txt, self.p_col = v_txt, i_txt, p_txt, p_col
        self.load_txt, self.time_txt = load_txt, time_txt
        self.draw()

    def draw(self) -> None:
        """Zeichnet oben drei Kennzahlen-Blöcke (Spannung, Strom, Leistung), darunter eine Zeile mit Verbrauch und Restlaufzeit."""
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 60 or h < 40:
            return
        rounded(self, 4, 4, w - 4, h - 4, 18, fill=CARD, outline="")

        # Obere Hälfte: 3 saubere Kennzahlen-Blöcke (Spannung | Strom | Leistung)
        cols = [
            ("SPANNUNG", self.v_txt, TEXT),
            ("STROM", self.i_txt, self.p_col),
            ("LEISTUNG", self.p_txt, self.p_col),
        ]
        pad = 12
        gap = 6
        box_w = (w - 2 * pad - 2 * gap) / 3.0
        top_y, mid_y = 12, h * 0.54
        for idx, (lbl, val, col) in enumerate(cols):
            x0 = pad + idx * (box_w + gap)
            x1 = x0 + box_w
            rounded(self, x0, top_y, x1, mid_y, 10, fill="#f7f5eb", outline="")
            cx = (x0 + x1) / 2
            self.create_text(cx, top_y + 13, text=lbl, fill=MUTED, font=(FONT, 7, "bold"))
            self.create_text(cx, (top_y + mid_y) / 2 + 8, text=val, fill=col, font=(FONT, 12, "bold"))

        # Trennlinie & untere Zeile: Verbrauch links, Restlaufzeit rechts
        line_y = mid_y + 7
        self.create_line(pad, line_y, w - pad, line_y, fill="#eee8d6")
        bot_y = (line_y + h - 6) / 2
        self.create_text(pad + 2, bot_y, text=f"Last: {self.load_txt}", anchor="w", fill=MUTED, font=(FONT, 10, "bold"))
        self.create_text(w - pad - 2, bot_y, text=f"Rest: {self.time_txt}", anchor="e", fill=TEXT, font=(FONT, 10, "bold"))


class BatteryPage(tk.Frame):
    """Menü "Akku": links die Bank, rechts die Einzel-Akkus.

    Links oben die Ladezustands-Anzeige der ganzen Bank (BatteryGauge), darunter Spannung, Strom,
    Leistung, Verbrauch und Restlaufzeit (BatterySummaryCard). Rechts je eine Kachel pro Akku
    (BatteryPackBar), zweispaltig. Bei Reihenschaltung zählt der schwächste Akku: Er bestimmt den
    Ladezustand der Bank, und an ihm erkennt man, ob die Akkus gleichmäßig geladen sind.

    In der Simulation lässt sich die Zahl der Akkus (1 bis 8) mit +/- ändern. Die Restlaufzeit rechnet
    mit der Kapazität aller Akkus.
    """
    def __init__(self, master, pcfg: PowerConfig, source=None):
        super().__init__(master, bg=BG)
        self.pcfg = pcfg
        self.source = source
        self.n_packs = pcfg.batteries
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=5, uniform="b")
        self.columnconfigure(1, weight=6, uniform="b")

        left = tk.Frame(self, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=3)
        left.rowconfigure(1, weight=2)
        self.gauge = BatteryGauge(left)
        self.gauge.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        self.info = BatterySummaryCard(left)
        self.info.grid(row=1, column=0, sticky="nsew", pady=(4, 0))

        right_outer = tk.Frame(self, bg=BG)
        right_outer.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
        right_outer.columnconfigure(0, weight=1)
        right_outer.rowconfigure(1, weight=1)

        # Akku-Anzahl ändern (nur in der Simulation sinnvoll)
        ctrl = tk.Frame(right_outer, bg=BG)
        ctrl.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ctrl.columnconfigure(1, weight=1)
        can_edit = source is not None and hasattr(source, "num_packs")
        if can_edit:
            Button(ctrl, lambda: self._change_packs(-1), text="−", color=HEADER, fsize=14, width=56, height=44).grid(row=0, column=0)
            Button(ctrl, lambda: self._change_packs(1), text="+", color=HEADER, fsize=14, width=56, height=44).grid(row=0, column=2)
        self.lbl_packs = tk.Label(ctrl, text="", bg=BG, fg=TEXT, font=(FONT, 11, "bold"))
        self.lbl_packs.grid(row=0, column=1)

        self.packs_host = tk.Frame(right_outer, bg=BG)
        self.packs_host.grid(row=1, column=0, sticky="nsew")
        self.pack_cards: list[BatteryPackBar] = []
        self._rebuild_packs(pcfg.batteries)

    def _change_packs(self, delta: int) -> None:
        """Anzahl der Akkus in der Simulation um delta ändern (1 bis 8) und die Kacheln neu aufbauen."""
        new_n = max(1, min(8, self.source.num_packs + delta))
        self.source.num_packs = new_n
        self._rebuild_packs(new_n)

    def _rebuild_packs(self, count: int) -> None:
        """Kacheln für count Akkus neu aufbauen und die Gesamtkapazität beschriften (nur wenn sich die Anzahl geändert hat)."""
        if len(self.pack_cards) == count:
            return
        self.n_packs = count
        for c in self.pack_cards:
            c.destroy()
        self.pack_cards.clear()
        for r in range(4):
            self.packs_host.rowconfigure(r, weight=0)
        for col in range(2):
            self.packs_host.columnconfigure(col, weight=1, uniform="bp_c")
        kwh = self.pcfg.battery_capacity_wh * count / 1000.0
        par = " (2×4 · 48 V)" if count > 4 else ""
        self.lbl_packs.config(text=f"{count} Akku" + ("s" if count > 1 else "") + f" · {kwh:.2f} kWh{par}")
        for r in range((count + 1) // 2):
            self.packs_host.rowconfigure(r, weight=1, uniform="bp_r")
        for i in range(count):
            card = BatteryPackBar(self.packs_host, f"Akku {i + 1}")
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=2, pady=2)
            self.pack_cards.append(card)

    def _runtime(self, pr: PowerReading) -> str:
        """Geschätzte Restlaufzeit bei aktueller Entladeleistung, als Text.

        Gerechnet wird mit der nutzbaren Energie bis zur Abschaltgrenze (soc_cutoff_pct) geteilt durch die
        momentane Entladeleistung. Beim Laden steht '–', ab 48 Stunden '> 2 Tage'. Es ist eine Momentaufnahme:
        Ändert sich der Verbrauch, ändert sich auch die Schätzung.
        """
        if pr.battery_power_w >= -5:
            return "–"
        cap_wh = self.pcfg.battery_capacity_wh * self.n_packs
        usable_wh = max(0.0, pr.battery_soc_pct - self.pcfg.soc_cutoff_pct) / 100 * cap_wh
        hours = usable_wh / -pr.battery_power_w
        if hours > 48:
            return "> 2 Tage"
        return f"ca. {int(hours)} h {int(hours % 1 * 60):02d} min"

    def refresh(self, r, pr: PowerReading) -> None:
        """Neue Messwerte übernehmen: Gauge, Kennzahlen und Einzel-Akkus. Baut die Kacheln um, wenn die Quelle eine andere Akku-Zahl meldet."""
        if len(pr.battery_packs) != len(self.pack_cards) and len(pr.battery_packs) > 0:
            self._rebuild_packs(len(pr.battery_packs))
        soc, i = pr.battery_soc_pct, pr.battery_current_a
        if pr.battery_temp_c < 0.0:
            status, scol, icol = "FROST-SCHLAF", "#3b8fc4", "#3b8fc4"
        elif i > 0.5:
            status, scol, icol = "wird geladen", GOOD, GOOD
        elif i < -0.5:
            status, scol, icol = "entlädt", WARN, WARN
        else:
            status, scol, icol = "ruhig", MUTED, TEXT
        gcol = BAD if soc < self.pcfg.soc_cutoff_pct else WARN if soc < 40 else GOOD
        self.gauge.set_state(soc, status, gcol, scol, i)
        self.info.set_data(
            v_txt=f"{pr.battery_voltage_v:.1f} V",
            i_txt=f"{i:+.1f} A",
            p_txt=f"{pr.battery_power_w:+.0f} W",
            p_col=icol,
            load_txt=f"{pr.load_w:.0f} W",
            time_txt=self._runtime(pr),
        )
        for card, bp in zip(self.pack_cards, pr.battery_packs):
            card.set_reading(bp)


class OutletsPage(tk.Frame):
    """Menü "Steckdosen": ein OutletPanel je Steckdose, nebeneinander."""
    def __init__(self, master, manager: PowerManager, pcfg: PowerConfig):
        super().__init__(master, bg=BG)
        self.rowconfigure(0, weight=1)
        self.panels = []
        for i, outlet in enumerate(manager.outlets):
            self.columnconfigure(i, weight=1, uniform="o")
            p = OutletPanel(self, manager, outlet, pcfg)
            p.grid(row=0, column=i, sticky="nsew", padx=(8 if i == 0 else 4, 8 if i == len(manager.outlets) - 1 else 4), pady=8)
            self.panels.append(p)

    def refresh(self, r, pr: PowerReading) -> None:
        """Leistungswerte je Steckdose an die Panels weiterreichen."""
        for i, p in enumerate(self.panels):
            p.refresh(pr.outlet_loads_w[i] if i < len(pr.outlet_loads_w) else None)
class OutletPanel(tk.Frame):
    """Bedienung EINER Steckdose: Anzeige, Timer-Auswahl und Ein/Aus-Knopf.

    So ist es gedacht: Zuerst wählt man die Dauer (15 min, 30 min, 1 h, 2 h), dann EINSCHALTEN. Nach
    Ablauf schaltet die Steckdose von selbst ab (Outlet.tick im PowerManager). Tippt man bei laufender
    Steckdose auf eine andere Dauer, beginnt der Timer von vorn (so verlängert man die Laufzeit).
    Der Ein-Knopf ist grau, solange der PowerManager das Einschalten verbietet (Akku zu leer oder keine
    Daten). AUSSCHALTEN geht immer.
    """

    def __init__(self, master, manager: PowerManager, outlet: Outlet, pcfg: PowerConfig):
        super().__init__(master, bg=BG)
        self.manager, self.outlet = manager, outlet
        self.minutes = 30 if 30 in pcfg.durations_min else pcfg.durations_min[0]
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=3)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)
        self.info = OutletInfo(self, outlet.name)
        self.info.grid(row=0, column=0, sticky="nsew")
        chips = tk.Frame(self, bg=BG)
        chips.grid(row=1, column=0, sticky="nsew", pady=6)
        chips.rowconfigure(0, weight=1)
        self.chips = {}
        for i, m in enumerate(pcfg.durations_min):
            chips.columnconfigure(i, weight=1, uniform="ch")
            b = Button(chips, lambda m=m: self.pick(m), text=dur_label(m), fsize=13)
            b.grid(row=0, column=i, sticky="nsew", padx=3)
            self.chips[m] = b
        self.toggle = Button(self, self.on_toggle, text="EINSCHALTEN", fsize=16)
        self.toggle.grid(row=2, column=0, sticky="nsew")

    def pick(self, m: int) -> None:
        """Dauer gewählt: merken; läuft die Steckdose schon, den Timer mit dieser Dauer neu starten."""
        self.minutes = m
        if self.outlet.is_on:
            self.manager.switch_on(self.outlet, m)
        self.refresh()

    def on_toggle(self) -> None:
        """Ein/Aus-Knopf: läuft die Steckdose, ausschalten; sonst mit der gewählten Dauer einschalten (falls erlaubt)."""
        if self.outlet.is_on:
            self.manager.switch_off(self.outlet)
        else:
            self.manager.switch_on(self.outlet, self.minutes)
        self.refresh()

    def refresh(self, load_w: float | None = None) -> None:
        """Anzeige, Dauer-Knöpfe und Ein/Aus-Knopf an den aktuellen Zustand anpassen (load_w = gemessene Leistung, falls bekannt)."""
        on, blocked = self.outlet.is_on, not self.manager.can_switch_on
        self.info.set_state(on=on, load=load_w, remaining=self.outlet.remaining_s(), minutes=self.minutes,
                            locked=blocked, reason=self.manager.lock_reason or "Keine Akkudaten")
        for m, b in self.chips.items():
            b.look(color=HEADER if m == self.minutes else CHIP_OFF)
        self.toggle.look(text="AUSSCHALTEN" if on else "EINSCHALTEN", color=BAD if on else GOOD,
                         enabled=on or not blocked)


# --- Anwendung ---------------------------------------------------------------
class EventsPage(tk.Frame):
    """5. Menü: Störungs- & Ereignis-Speicher mit Quittier-Button zum Zurücksetzen."""

    def __init__(self, master, app: "App"):
        super().__init__(master, bg=BG)
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Obere Status- & Aktionskarte
        top = tk.Frame(self, bg=BG)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.grid_columnconfigure(0, weight=1)

        self.status_lbl = tk.Label(top, text="Systemstatus: Alles in Ordnung", bg=CARD, fg=GOOD,
                                   font=(FONT, 12, "bold"), anchor="w", padx=14, pady=10)
        self.status_lbl.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.reset_btn = tk.Button(top, text="Störung quittieren", command=self._ack_faults,
                                   bg=GOOD, fg="white", activebackground="#23532c", activeforeground="white",
                                   bd=0, relief="flat", padx=16, pady=10, font=(FONT, 11, "bold"), cursor="hand2")
        self.reset_btn.grid(row=0, column=1, padx=(0, 8))

        clr_btn = tk.Button(top, text="Log leeren", command=self._clear_log,
                            bg="#e6e1cf", fg=TEXT, activebackground="#d4ceb8",
                            bd=0, relief="flat", padx=12, pady=10, font=(FONT, 10, "bold"), cursor="hand2")
        clr_btn.grid(row=0, column=2, padx=(0, 8))

        close_btn = tk.Button(top, text="✕ Schließen", command=self.app.close_events,
                              bg=HEADER, fg="white", activebackground="#23532c", activeforeground="white",
                              bd=0, relief="flat", padx=14, pady=10, font=(FONT, 11, "bold"), cursor="hand2")
        close_btn.grid(row=0, column=3)

        # Zwei getrennte Bereiche: Oben Störungen (ALARM), Unten Systemmeldungen (INFO)
        split = tk.Frame(self, bg=BG)
        split.grid(row=1, column=0, sticky="nsew")
        split.grid_columnconfigure(0, weight=1)
        split.grid_rowconfigure(0, weight=1, uniform="ev_s")
        split.grid_rowconfigure(1, weight=1, uniform="ev_s")

        box_al = tk.Frame(split, bg=CARD, padx=12, pady=8)
        box_al.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        box_al.grid_columnconfigure(0, weight=1)
        box_al.grid_rowconfigure(1, weight=1)
        tk.Label(box_al, text="1. STÖRUNGEN & SCHUTZSCHALTUNGEN (ALARM)",
                 bg=CARD, fg=BAD, font=(FONT, 10, "bold"), anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.alarm_listbox = tk.Listbox(box_al, bg="#fdf4f4", fg="#7a1c1c", font=(FONT, 10, "bold"),
                                        bd=0, highlightthickness=1, highlightbackground="#ecc8c8",
                                        selectbackground="#f5d6d6", activestyle="none")
        self.alarm_listbox.grid(row=1, column=0, sticky="nsew")

        box_in = tk.Frame(split, bg=CARD, padx=12, pady=8)
        box_in.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        box_in.grid_columnconfigure(0, weight=1)
        box_in.grid_rowconfigure(1, weight=1)
        tk.Label(box_in, text="2. NORMALE SYSTEM- & WETTERSCHUTZ-MELDUNGEN (INFO)",
                 bg=CARD, fg=GOOD, font=(FONT, 10, "bold"), anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.info_listbox = tk.Listbox(box_in, bg="#faf8f0", fg=TEXT, font=(FONT, 10),
                                       bd=0, highlightthickness=1, highlightbackground="#e0dbc8",
                                       selectbackground="#dce8d8", activestyle="none")
        self.info_listbox.grid(row=1, column=0, sticky="nsew")

    def _ack_faults(self) -> None:
        self.app.acknowledge_faults()

    def _toggle_filter(self) -> None:
        self.only_alarms = not self.only_alarms
        self.flt_btn.config(text="Ansicht: Nur Störungen (ALARM)" if self.only_alarms else "Ansicht: Alle (INFO + ALARM)",
                            bg=BAD if self.only_alarms else "#e6e1cf",
                            fg="white" if self.only_alarms else TEXT)

    def _clear_log(self) -> None:
        self.app.clear_event_log()

    def refresh(self, r: Reading, pr: PowerReading | None) -> None:
        """Aktualisiert alle Anzeigen und Steuerelemente der Seite mit den neuesten Daten."""
        bms_al = ""
        if pr and getattr(pr, "battery_packs", None):
            mc = min(bp.voltage_v for bp in pr.battery_packs)
            mt = min(bp.temp_c for bp in pr.battery_packs)
            if mc < 11.5:
                bms_al = f"BMS Zell-Unterspannung ({mc:.2f} V)"
            elif mt < 0.0:
                bms_al = f"BMS Akku-Frost ({mt:.1f} °C)"
        alarm = self.app.ctrl_error or self.app.ctrl.pump_fault or bms_al
        if alarm:
            self.status_lbl.config(text=f"AKTIVE STÖRUNG: {alarm}", fg=BAD, bg="#fdecec")
            self.reset_btn.config(state="normal", bg=BAD)
        else:
            self.status_lbl.config(text="Systemstatus: Keine aktiven Störungen", fg=GOOD, bg=CARD)
            self.reset_btn.config(state="normal", bg=GOOD)
        self.app.sync_event_lists(self.alarm_listbox, self.info_listbox)


class App:
    """Das Hauptfenster: baut Kopfzeile, die vier Seiten und die Knopfleiste und hat die beiden Takte.

        tick()        alle interval_s Sekunden: messen, regeln, Seiten und Kopfzeile aktualisieren
        _anim_loop()  alle 80 ms: nur Bewegung der Animationen auf der sichtbaren Seite

    Zustand: pr = letzte Solar-/Akku-Messung (None, solange noch keine kam), current = sichtbare Seite,
    ctrl_error = Hinweis bei Sensorfehler (leer, wenn alles in Ordnung).

    Die Seiten werden mit der Reihenfolge TABS verknüpft (Seite k gehört zum k-ten Knopf).
    """
    TABS = ["Gewächshaus", "Solar", "Akku", "Steckdosen"]

    def __init__(self, root: tk.Tk, ctrl: Controller, manager: PowerManager, cfg: Config,
                 pcfg: PowerConfig, sim: bool, history: int, env=None):
        self.root, self.ctrl, self.manager, self.sim = root, ctrl, manager, sim
        self.interval_ms = int(cfg.interval_s * 1000)
        self.pr: PowerReading | None = None
        self.current = 0
        self.ctrl_error = ""
        self._seen: set[str] = set()

        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self.header = tk.Canvas(root, height=56, bg=HEADER, highlightthickness=0)
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.bind("<Configure>", lambda e: self.draw_header())
        self.header.bind("<Button-1>", self._on_header_click)

        host = tk.Frame(root, bg=BG)
        host.grid(row=1, column=0, sticky="nsew")
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        self.env = env
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.pages = [
            GreenhousePage(host, ctrl, cfg, history, env=env),
            SolarPage(host, pcfg, history, manager.source),
            BatteryPage(host, pcfg, manager.source),
            OutletsPage(host, manager, pcfg),
            EventsPage(host, self),
            SettingsPage(host, self, cfg, base_dir),
        ]
        # Alle Seiten liegen im SELBEN Gitterfeld übereinander. Welche zu sehen ist, bestimmt show():
        # tkraise() holt eine Seite nach vorn. Die anderen bleiben im Hintergrund und werden weiter
        # aktualisiert (siehe tick), nur animiert wird allein die sichtbare (siehe _anim_loop).
        for page in self.pages:
            bg_c = tk.Canvas(page, bg=BG, highlightthickness=0)
            bg_c.place(x=0, y=0, relwidth=1.0, relheight=1.0)
            bg_c.bind("<Configure>", lambda e, cv=bg_c: draw_garden_bg(cv))
            tk.Misc.lower(bg_c)
            page.grid(row=0, column=0, sticky="nsew")
        self.diagram_view = SystemDiagramView(host, self.close_diagram)
        self.diagram_view.grid(row=0, column=0, sticky="nsew")
        self.diagram_open = False
        self.garden_view = GardenSceneView(host, self.close_garden)
        self.garden_view.grid(row=0, column=0, sticky="nsew")
        self.garden_open = False
        self._screensaver_active = False
        self._last_user_activity = time.monotonic()
        self.root.bind_all("<Button-1>", self._on_any_user_touch, add="+")
        self.root.bind_all("<B1-Motion>", self._on_any_user_touch, add="+")
        self.root.bind_all("<Key>", self._on_any_user_touch, add="+")

        self.nav = nav = tk.Frame(root, bg=BG)
        nav.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.nav_btns = []
        for i, title in enumerate(self.TABS):
            nav.columnconfigure(i, weight=1, uniform="n")
            b = Button(nav, lambda i=i: self.show(i), text=title, fsize=14, height=52)
            b.grid(row=0, column=i, sticky="nsew", padx=3)
            self.nav_btns.append(b)
        self.show(0)
        # Zwei unabhängige Takte: tick() = Messen/Regeln/Anzeigen (alle interval_s Sekunden),
        # _anim_loop() = nur Bewegung der Animationen (alle 80 ms, ca. 12 Bilder pro Sekunde).
        root.after(200, self.tick)
        root.after(80, self._anim_loop)

    def _anim_loop(self) -> None:
        """Animationstakt (alle 80 ms): schiebt Energiefluss- bzw. Akku-Animation der SICHTBAREN Seite weiter.

        Nur die sichtbare Seite wird animiert, denn alle Seiten liegen im selben Gitterfeld übereinander und
        gelten für Tk alle als "sichtbar"; sonst würden auf dem Raspberry Pi unnötig alle gezeichnet.
        """
        try:
            pf_txt = str(getattr(self.ctrl, "pump_fault", "") or "")
            crit_pump = ("Trockenlauf" in pf_txt) or ("Regentonne leer" in pf_txt)
            min_c = min((getattr(bp, "voltage_v", 13.0) for bp in self.pr.battery_packs), default=13.0) if getattr(self, "pr", None) else 13.0
            crit_cell = (min_c < 11.5) or ("Unterspannung" in str(getattr(self.manager, "lock_reason", "") or ""))
            blink_fast = (int(time.monotonic() * 2.2) % 2 == 0)
            crit_phase = (1 if blink_fast else 2) if (crit_pump or crit_cell) else 0
            if getattr(self, "_last_crit_phase", 0) != crit_phase:
                self._last_crit_phase = crit_phase
                if crit_phase > 0 and getattr(self, "_screensaver_active", False):
                    self._screensaver_active = False
                    self.close_garden()
                self._tint_widget_tree(self.root, crit_phase)
                self.draw_header()
            if self.diagram_open:
                self.diagram_view.step_anim()
                return
            if getattr(self, "garden_open", False):
                self.garden_view.step_anim()
                return
            page = self.pages[self.current]   # nur die sichtbare Seite animieren (spart CPU)
            if hasattr(page, "flow"):
                page.flow.step_anim()
            if hasattr(page, "gauge"):
                page.gauge.step_anim()
                for pc in getattr(page, "pack_cards", ()): pc.step_anim()
        except Exception:
            self._log_once("anim", "Fehler in der Animation")
        finally:
            self.root.after(80, self._anim_loop)

    def _on_header_click(self, e) -> None:
        w = self.header.winfo_width()
        x1 = w - 96
        if x1 - 46 <= e.x <= x1 - 4 and 10 <= e.y <= 46:
            if getattr(self, "events_open", False):
                self.events_open = False
                self.nav.grid()
            self.close_diagram() if self.diagram_open else self.open_diagram()
        elif x1 - 190 <= e.x <= x1 - 146 and 10 <= e.y <= 46:
            self.close_garden() if getattr(self, "garden_open", False) else self.open_garden()
        elif x1 - 142 <= e.x <= x1 - 98 and 10 <= e.y <= 46:
            self.close_settings() if getattr(self, "settings_open", False) else self.open_settings()
        elif x1 - 94 <= e.x <= x1 - 50 and 10 <= e.y <= 46:
            self.close_events() if getattr(self, "events_open", False) else self.open_events()

    def _on_any_user_touch(self, _event=None) -> None:
        self._last_user_activity = time.monotonic()
        if getattr(self, "_screensaver_active", False):
            self._screensaver_active = False
            self.close_garden()

    def open_garden(self, as_screensaver: bool = False) -> None:
        """Öffnet die animierte 4-Jahreszeiten-Gartenwelt (manuell oder als Bildschirmschoner)."""
        self._screensaver_active = bool(as_screensaver)
        self.garden_open = True
        self.diagram_open = False
        self.events_open = False
        self.settings_open = False
        self.garden_view.update_data(getattr(self, "last_r", None), self.pr, self.ctrl, self.manager, self.env)
        self.nav.grid_remove()
        tk.Misc.tkraise(self.garden_view)
        self.draw_header()

    def close_garden(self) -> None:
        """Schließt die Garten-Ansicht und kehrt zur vorherigen Seite zurück."""
        self.garden_open = False
        self._screensaver_active = False
        self._last_user_activity = time.monotonic()
        self.nav.grid()
        tk.Misc.tkraise(self.pages[getattr(self, "current", 0)])
        self.draw_header()

    def open_diagram(self) -> None:
        """Öffnet das animierte Vollbild-Anlagenschema."""
        self.diagram_open = True
        self.garden_open = False
        self.diagram_view.update_data(getattr(self, "last_r", None), self.pr, self.ctrl, self.manager, self.env)
        self.nav.grid_remove()
        tk.Misc.tkraise(self.diagram_view)
        self.draw_header()

    def close_diagram(self) -> None:
        """Schließt das Vollbild-Anlagenschema und kehrt zur Hauptansicht zurück."""
        self.diagram_open = False
        self.nav.grid()
        self.pages[self.current].tkraise()
        self.draw_header()

    def show(self, i: int) -> None:
        """Seite i nach vorn holen und den passenden Menü-Knopf hervorheben."""
        self.diagram_open = False
        self.nav.grid()
        self.current = i
        self.pages[i].tkraise()
        for k, b in enumerate(self.nav_btns):
            b.look(color=HEADER if k == i else NAV_OFF)

    def _ensure_events(self) -> None:
        if not hasattr(self, "_events"):
            self._last_logged_alarm = ""
            self._event_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.log")
            self._events = self._load_events()

    def _load_events(self) -> list[str]:
        if os.path.exists(self._event_log_path):
            try:
                lines = [ln.strip() for ln in open(self._event_log_path, encoding="utf-8") if ln.strip()]
                return lines[-100:]
            except OSError:
                pass
        return [f"{time.strftime('%d.%m.%Y %H:%M:%S')} · [INFO] System gestartet – Überwachung aktiv"]

    def log_event(self, msg: str) -> None:
        """Schreibt ein Ereignis ([ALARM] oder [INFO]) in den Speicher und in events.log."""
        self._ensure_events()
        entry = f"{time.strftime('%d.%m.%Y %H:%M:%S')} · {msg}"
        self._events.append(entry)
        self._events = self._events[-100:]
        try:
            with open(self._event_log_path, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except OSError:
            pass

    def acknowledge_faults(self) -> None:
        """Quittiert aktive Störungen nach Prüfung und gibt gesperrte Aktoren wieder frei."""
        self._ensure_events()
        had_fault = bool(self.ctrl_error or self.ctrl.pump_fault or getattr(self, "sim_fault_fan_msg", None))
        self.sim_fault_pump = None
        self.ctrl.clear_pump_fault()
        self.ctrl_error = None
        self.sim_fault_sensor = None
        self.sim_fault_fans = set()
        self.sim_fault_fan_msg = None
        tk._default_root._fan_faults = set()
        self.ctrl.fan._fault_indices = set()
        self.manager.locked = False
        self.manager.lock_reason = ""
        if hasattr(self, "sim_reset_cb") and callable(self.sim_reset_cb):
            self.sim_reset_cb()
        self._last_logged_alarm = ""
        if had_fault:
            self.log_event("[INFO] Störung vom Bediener quittiert & Automatik freigegeben")
        self.draw_header()

    def clear_event_log(self) -> None:
        """Löscht die Historie der protokollierten Ereignisse nach Bestätigung."""
        self._ensure_events()
        self._events = [f"{time.strftime('%d.%m.%Y %H:%M:%S')} · [INFO] Ereignis-Speicher geleert"]
        try:
            with open(self._event_log_path, "w", encoding="utf-8") as f:
                f.write(self._events[0] + "\n")
        except OSError:
            pass

    def open_settings(self) -> None:
        """Öffnet das Touch-Einstellungsmenü (settings_page.py)."""
        self.settings_open = True
        self.events_open = False
        if getattr(self, "diagram_open", False):
            self.diagram_open = False
        self.nav.grid_remove()
        tk.Misc.tkraise(self.pages[5])
        self.draw_header()

    def close_settings(self) -> None:
        """Schließt das Touch-Einstellungsmenü und kehrt zur Hauptansicht zurück."""
        self.settings_open = False
        self.nav.grid()
        tk.Misc.tkraise(self.pages[getattr(self, "current", 0)])
        self.draw_header()

    def open_events(self) -> None:
        """Öffnet das zweigeteilte Störungs- und Ereignismenü."""
        self.events_open = True
        self.settings_open = False
        if getattr(self, "diagram_open", False):
            self.diagram_open = False
        self.nav.grid_remove()
        tk.Misc.tkraise(self.pages[4])
        self.draw_header()

    def close_events(self) -> None:
        """Schließt das Störungs- und Ereignismenü."""
        self.events_open = False
        self.nav.grid()
        tk.Misc.tkraise(self.pages[getattr(self, "current", 0)])
        self.draw_header()

    def sync_event_lists(self, lb_alarm: tk.Listbox, lb_info: tk.Listbox) -> None:
        """Aktualisiert die Listen für aktive Störungen und Ereignisverlauf."""
        self._ensure_events()
        alarms = list(reversed([e for e in self._events if "ALARM" in e]))
        infos = list(reversed([e for e in self._events if "ALARM" not in e]))
        if list(lb_alarm.get(0, "end")) != alarms:
            lb_alarm.delete(0, "end")
            for it in alarms:
                lb_alarm.insert("end", it)
        if list(lb_info.get(0, "end")) != infos:
            lb_info.delete(0, "end")
            for it in infos:
                lb_info.insert("end", it)

    def _play_alarm_tone(self) -> None:
        """Spielt asynchron einen sanften Zweiklang-Hinweiston (880 Hz -> 660 Hz) ab."""
        import shutil, struct, subprocess, wave
        wav_path = "/tmp/solar_greenhub_alarm.wav"
        try:
            if not os.path.exists(wav_path):
                sr = 22050
                frames = bytearray()
                for freq, dur in ((880.0, 0.11), (0.0, 0.06), (660.0, 0.14)):
                    n_s = int(sr * dur)
                    for i in range(n_s):
                        if freq <= 0.0:
                            val = 0
                        else:
                            env_a = min(1.0, i / 250.0, (n_s - i) / 250.0)
                            val = int(14000 * env_a * math.sin(2.0 * math.pi * freq * (i / sr)))
                        frames.extend(struct.pack("<h", val))
                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(bytes(frames))
            for player in ("paplay", "aplay"):
                if shutil.which(player):
                    subprocess.Popen([player, wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
        except Exception:
            pass
        try:
            self.root.bell()
        except Exception:
            pass

    def _tint_widget_tree(self, widget, phase: int) -> None:
        """Pulsiert den gesamten Hintergrund (#f4f1e6) alarmrot und rahmt weiße Karten rot ein."""
        if widget in (getattr(self, "garden_view", None), getattr(self, "diagram_view", None),
                      getattr(self, "settings_page", None), getattr(self, "events_page", None),
                      getattr(self, "header", None)):
            return
        try:
            cur_bg = str(widget.cget("bg")).lower()
            if not hasattr(widget, "_orig_bg"):
                widget._orig_bg = cur_bg
            if widget._orig_bg == "#f4f1e6":
                new_bg = "#e57368" if phase == 1 else ("#f5b7b1" if phase == 2 else "#f4f1e6")
                if cur_bg != new_bg:
                    widget.config(bg=new_bg)
        except Exception:
            pass
        if isinstance(widget, tk.Canvas):
            if not hasattr(widget, "_orig_items"):
                widget._orig_items = {}
            for iid in widget.find_all():
                try:
                    fl = str(widget.itemcget(iid, "fill")).lower()
                    if iid not in widget._orig_items and fl in ("#eae6d4", "#ebe7d6", "#dfe5d0", "#e6ebd8", "#ffffff"):
                        widget._orig_items.__setitem__(iid, fl)
                    ofl = widget._orig_items.get(iid)
                    if ofl == "#ffffff":
                        widget.itemconfig(iid, outline="#c0392b" if phase in (1, 2) else "", width=2 if phase in (1, 2) else 0)
                    elif ofl is not None:
                        nfl = "#d9534f" if phase == 1 else ("#eb9891" if phase == 2 else ofl)
                        if fl != nfl:
                            widget.itemconfig(iid, fill=nfl)
                except Exception:
                    pass
        for ch in widget.winfo_children():
            self._tint_widget_tree(ch, phase)

    def draw_header(self) -> None:
        """Kopfzeile zeichnen: Blatt-Symbol, Name, Versionsbezeichnung, Uhrzeit, ggf. 'SIMULATION' und in der Mitte
        eine Zeile mit dem wichtigsten Zustand: ein Alarm (Sensorfehler oder Pumpen-Sperre), 'Keine
        Solar-/Akkudaten' oder die aktuelle Solarleistung und der Akkustand.
        """
        c = self.header
        c.delete("all")
        w = c.winfo_width()
        bx, by = 32, 28
        c.create_polygon(bx, by+1, bx-14, by+4, bx-11, by+15, bx-1, by+12, smooth=True, fill="#ebd028", outline="#c49d14")
        c.create_polygon(bx, by+1, bx+14, by+4, bx+11, by+15, bx+1, by+12, smooth=True, fill="#ebd028", outline="#c49d14")
        c.create_polygon(bx, by, bx-18, by-13, bx-16, by+3, bx-1, by+3, smooth=True, fill="#fff05a", outline="#c49d14")
        c.create_polygon(bx, by, bx+18, by-13, bx+16, by+3, bx+1, by+3, smooth=True, fill="#fff05a", outline="#c49d14")
        for dx, dy in ((-8, -3), (8, -3), (-6, 7), (6, 7)):
            c.create_oval(bx+dx-1.6, by+dy-1.6, bx+dx+1.6, by+dy+1.6, fill="#e67e22", outline="")
        c.create_line(bx, by-7, bx, by+11, fill="#3b3624", width=2, capstyle="round")
        c.create_line(bx-1, by-7, bx-5, by-13, fill="#3b3624", width=1)
        c.create_line(bx+1, by-7, bx+5, by-13, fill="#3b3624", width=1)
        c.create_text(62, 22, text="Solar GreenHub", anchor="w", fill="white", font=(FONT, 18, "bold"))
        c.create_text(63, 44, text=BUILD, anchor="w", fill="#bcd9b4", font=(FONT, 8))
        bms_alarm = ""
        if self.pr and getattr(self.pr, "battery_packs", None):
            min_c = min(bp.voltage_v for bp in self.pr.battery_packs)
            min_t = min(bp.temp_c for bp in self.pr.battery_packs)
            if min_c < 11.5:
                bms_alarm = f"BMS Zell-Unterspannung ({min_c:.2f} V) – Lastabwurf aktiv"
            elif min_t < 0.0:
                bms_alarm = f"BMS Akku-Frost ({min_t:.1f} °C) – Ladesperre aktiv"
        ff_set = getattr(tk._default_root, "_fan_faults", set()) if tk._default_root is not None else set()
        fan_alarm = (f"Lüfter-Störung ({', '.join(f'L{i+1}' for i in sorted(ff_set))}) aktiv" if ff_set else "")
        alarm = self.ctrl_error or self.ctrl.pump_fault or bms_alarm or fan_alarm
        if alarm and alarm != self._last_logged_alarm:
            self._last_logged_alarm = alarm
            self.log_event(f"ALARM: {alarm}")
            self._last_alarm_beep = 0.0
        elif not alarm:
            self._last_logged_alarm = ""
            self._last_alarm_beep = 0.0

        # Dezenter Doppel-Alarmton bei aktiver Störung (alle 5 Live-Minuten bzw. 5 Simulations-Minuten)
        if alarm:
            now_b = time.monotonic()
            is_live_b = bool(self.env and getattr(self.env, "live_siegen", False))
            if is_live_b:
                beep_interval = 300.0
            else:
                sp_step = float(self.sim_win.s_speed.get()) if (hasattr(self, "sim_win") and hasattr(self.sim_win, "s_speed")) else 1.0
                sp_mult = 1.0 + (max(1.0, min(14.0, sp_step)) - 1.0) * (2.0 / 13.0)
                beep_interval = max(6.0, 300.0 / (60.0 * sp_mult))
            if (now_b - getattr(self, "_last_alarm_beep", 0.0)) >= beep_interval:
                self._last_alarm_beep = now_b
                self._play_alarm_tone()
        ev_open = getattr(self, "events_open", False)
        blink_on = int(time.monotonic() * 2.2) % 2 == 0

        c.create_text(w - 16, 28, text=time.strftime("%H:%M"), anchor="e", fill="white", font=(FONT, 18, "bold"))

        ix1 = w - 96
        ix0 = ix1 - 42
        wx1 = ix0 - 6
        wx0 = wx1 - 42
        sx1 = wx0 - 6
        sx0 = sx1 - 42
        gx1 = sx0 - 6
        gx0 = gx1 - 42
        st_open = getattr(self, "settings_open", False)
        gd_open = getattr(self, "garden_open", False)

        # Gewächshaus-Livebild-Icon links neben dem Zahnrad
        rounded(c, gx0, 14, gx1, 42, 10, fill="#3b8fc4" if gd_open else "#23532c", outline="#a8d98a", width=1)
        gmx = (gx0 + gx1) / 2
        c.create_arc(gmx - 11, 18, gmx + 11, 36, start=0, extent=180, outline="white", width=1.6, style="arc")
        c.create_rectangle(gmx - 11, 27, gmx + 11, 37, outline="white", width=1.4)
        c.create_line(gmx, 20, gmx, 37, fill="#a8d98a", width=1.2)

        # Zahnrad-Button (Einstellungen) links neben dem Warndreieck
        rounded(c, sx0, 14, sx1, 42, 10, fill="#3b8fc4" if st_open else "#23532c", outline="#a8d98a", width=1)
        smx, smy = (sx0 + sx1) / 2, 28
        for gi in range(8):
            ang = gi * (math.pi / 4)
            c.create_line(smx + 5 * math.cos(ang), smy + 5 * math.sin(ang),
                          smx + 9 * math.cos(ang), smy + 9 * math.sin(ang), fill="white", width=2)
        c.create_oval(smx - 5, smy - 5, smx + 5, smy + 5, fill="#3b8fc4" if st_open else "#23532c", outline="white", width=1.8)

        # Blinkendes Warndreieck-Symbol links neben dem Schema-Button
        w_bg = ("#c0392b" if blink_on else "#e67e22") if alarm else ("#3b8fc4" if ev_open else "#23532c")
        w_out = "#ffd27a" if alarm else "#a8d98a"
        rounded(c, wx0, 14, wx1, 42, 10, fill=w_bg, outline=w_out, width=1.5 if alarm else 1)
        wmx = (wx0 + wx1) / 2
        c.create_polygon(wmx, 19, wmx - 11, 37, wmx + 11, 37, fill="#ffd27a" if (alarm or ev_open) else "#a8d98a", outline="")
        c.create_text(wmx, 30, text="!", fill="#2b2618", font=(FONT, 10, "bold"))

        # Schema-Button
        rounded(c, ix0, 14, ix1, 42, 10, fill="#3b8fc4" if self.diagram_open else "#23532c", outline="#a8d98a", width=1)
        mx = (ix0 + ix1) / 2
        c.create_rectangle(mx - 11, 20, mx - 3, 27, outline="white", width=1)
        c.create_rectangle(mx + 3, 20, mx + 11, 27, outline=SUN, width=1)
        c.create_rectangle(mx - 5, 31, mx + 5, 37, outline="#a8d98a", width=1)
        c.create_line(mx - 3, 23, mx + 3, 23, fill="white", width=1)
        c.create_line(mx, 27, mx, 31, fill="white", width=1)

    def _log_once(self, key: str, msg: str) -> None:
        """Fehler nur beim ersten Mal ins Log (sonst läuft es jede Sekunde voll)."""
        if key not in self._seen:
            self._seen.add(key)
            log.exception(msg)

    def tick(self) -> None:
        """Der Takt der Oberfläche: einmal pro Zeitintervall messen, regeln und alles aktualisieren.

        Reihenfolge und Absicherung (siehe Modul-Beschreibung):
            1. manager.step() zuerst: Steckdosen-Timer und Akku-Schutz laufen immer.
            2. ctrl.step(): Gewächshaus-Regelung. Bei einem Sensorfehler gehen Pumpe und Lüfter aus, und die
               Kopfzeile zeigt 'Sensorfehler'; sobald die Sensoren wieder Werte liefern, verschwindet der Hinweis.
            3. Jede Seite einzeln aktualisieren: Ein Fehler in einer Seite stoppt die anderen nicht.
            4. Im finally-Block wird der nächste Takt IMMER geplant, was auch passiert ist.
        Fehler werden nur beim ersten Mal ins Log geschrieben (_log_once).
        """
        try:
            # Zuerst: Steckdosen-Timer und Akku-Schutz laufen immer, egal was danach schiefgeht
            try:
                self.pr = self.manager.step()
            except Exception:
                self._log_once("manager", "Fehler in PowerManager.step()")
            r = None
            try:
                if getattr(self, "sim_fault_sensor", None):
                    self.ctrl_error = self.sim_fault_sensor
                    tk._default_root._app_ctrl_error = True
                    self.ctrl.pump.off()
                    self.ctrl.fan.off()
                    if "sim_sensor" not in self._seen:
                        self._seen.add("sim_sensor")
                        log.warning("Steuerzyklus gestoppt: %s – Pumpe/Lüfter gesperrt", self.ctrl_error)
                else:
                    self._seen.discard("sim_sensor")
                    rt = getattr(tk._default_root, "_runtime_settings", {}) if tk._default_root is not None else {}
                    w_lim = float(rt.get("winter_temp_c", 10.0))
                    s_lim = float(rt.get("soil_hi_pct", 88.0))
                    d_lim = float(rt.get("dehum_start_pct", 78.0))
                    if getattr(self, "last_r", None) is not None and self.last_r.temperature_c < w_lim and not self.ctrl.manual:
                        self.ctrl._soak_until = max(getattr(self.ctrl, "_soak_until", 0.0), time.monotonic() + 3.0)
                    self.ctrl._gui_curve_active = True
                    r = self.ctrl.step()
                    # 220-L-Regentonne: Pumpenverbrauch & Regen-Nachfüllung (Live & Simulation)
                    if self.env is not None:
                        dt_s = max(0.2, self.interval_ms / 1000.0)
                        t_l = float(getattr(self.env, "tank_liters", 180.0))
                        if self.ctrl.pump.level > 0.01:
                            t_l = max(0.0, t_l - 0.25 * self.ctrl.pump.level * dt_s)
                        w_obj_t = getattr(self.pages[0], "_last_weather_obj", None) if self.pages else None
                        if getattr(self.env, "live_siegen", False) and w_obj_t is not None:
                            mm_h = float(getattr(w_obj_t, "precipitation_mm", 0.0))
                            if mm_h > 0.0:
                                t_l = min(220.0, t_l + (mm_h * 6.0 / 3600.0) * dt_s)
                        elif getattr(self.env, "rain_sim", False) or getattr(self.env, "cloud_cover_pct", 0.0) >= 85.0:
                            t_l = min(220.0, t_l + 0.35 * dt_s)
                        self.env.tank_liters = round(t_l, 2)
                        if t_l < 10.0 and not getattr(self.ctrl, "pump_fault", None):
                            self.ctrl.pump_fault = f"Regentonne leer ({t_l:.1f} L < 10 L) – Trockenlaufschutz"
                    if getattr(self, "sim_fault_pump", None):
                        self.ctrl.pump_fault = self.sim_fault_pump
                    if getattr(self.ctrl, "pump_fault", None):
                        self.ctrl.pump.off()
                    self.last_r = r
                    self.ctrl_error = None
                    tk._default_root._app_ctrl_error = False
                    winter_active = getattr(self, "_winter_lock", False)
                    if r is not None and (r.temperature_c < w_lim or (winter_active and r.temperature_c < w_lim + 0.8)):
                        self._winter_lock = True
                        self._seen.discard("soil_hi")
                        self._seen.discard("hum_hi")
                        if not self.ctrl.manual:
                            self.ctrl.fan.off()
                            self.ctrl.pump.off()
                        if "cold_auto" not in self._seen:
                            self._seen.add("cold_auto")
                            msg_c = f"Winterschutz (< {w_lim:.1f} °C): Lüfter- & Bewässerungs-Automatik pausiert"
                            log.info(msg_c)
                            self.log_event(f"[INFO] {msg_c}")
                    elif r is not None and r.soil_moisture_pct > s_lim:
                        self._seen.discard("cold_auto")
                        f_msg = f"Staunässe-Schutz (Bodenfeuchte {r.soil_moisture_pct:.0f} % > {s_lim:.0f} %) → Pumpe AUS & gesperrt"
                        self.ctrl.pump_fault = f_msg
                        self.ctrl.pump.off()
                        if "soil_hi" not in self._seen:
                            self._seen.add("soil_hi")
                            log.warning(f_msg)
                    elif r is not None and r.temperature_c >= 12.0 and r.humidity_pct > 92.0:
                        self._seen.discard("cold_auto")
                        h_msg = f"Luftfeuchte kritisch ({r.humidity_pct:.0f} % > 92 %) → Pumpe AUS & Entfeuchtung aktiv"
                        self.ctrl.pump_fault = h_msg
                        self.ctrl.pump.off()
                        if not self.ctrl.manual:
                            ff = getattr(tk._default_root, "_fan_faults", set())
                            self.ctrl.fan.set_level(1.0 if len(ff) < 3 else 0.0)
                        if "hum_hi" not in self._seen:
                            self._seen.add("hum_hi")
                            log.warning(h_msg)
                    else:
                        self._winter_lock = False
                        self._seen.discard("cold_auto")
                        self._seen.discard("soil_hi")
                        self._seen.discard("hum_hi")
                        if r is not None and not self.ctrl.manual:
                            now_d = time.monotonic()
                            ff = getattr(tk._default_root, "_fan_faults", set())
                            is_live_mode = bool(self.env and getattr(self.env, "live_siegen", False))
                            t_up, t_dn = (45.0, 45.0) if is_live_mode else (6.0, 6.0)
                            max_hold = 600.0 if is_live_mode else 45.0
                            start_hum = float(rt.get("dehum_start_pct", 76.0))
                            stop_hum = float(rt.get("dehum_stop_pct", 65.0))
                            fan_cap = max(0.30, min(1.0, float(rt.get("fan_max_pct", 85.0)) / 100.0))
                            want_fans = max(0, min(3, int(round(float(rt.get("fan_active_cnt", 3.0))))))
                            state = getattr(self, "_fan_state", "idle")
                            st_t0 = getattr(self, "_fan_state_t0", now_d)
                            heat_need = (r.temperature_c > self.ctrl.cfg.temp_max_c)

                            if len(ff) >= 3 or want_fans == 0 or r.temperature_c < w_lim:
                                state, lvl = "idle", 0.0
                            elif heat_need:
                                if state not in ("ramp_up", "hold_100"):
                                    state, st_t0 = "ramp_up", now_d
                                if state == "ramp_up":
                                    el = now_d - st_t0
                                    if el >= t_up:
                                        state, st_t0, lvl = "hold_100", now_d, fan_cap
                                    else:
                                        lvl = 0.15 + (fan_cap - 0.15) * (math.sin((el / t_up) * (math.pi / 2)) ** 2)
                                else:
                                    lvl = fan_cap
                            elif state == "idle":
                                lvl = 0.0
                                if r.temperature_c >= 12.0 and r.humidity_pct >= start_hum:
                                    state, st_t0, lvl = "ramp_up", now_d, 0.15
                            elif state == "ramp_up":
                                el = now_d - st_t0
                                if el >= t_up:
                                    state, st_t0, lvl = "hold_100", now_d, fan_cap
                                else:
                                    lvl = 0.15 + (fan_cap - 0.15) * (math.sin((el / t_up) * (math.pi / 2)) ** 2)
                            elif state == "hold_100":
                                lvl = fan_cap
                                if r.humidity_pct <= stop_hum or r.temperature_c < 12.0 or (now_d - st_t0) >= max_hold:
                                    state, st_t0 = "ramp_down", now_d
                            else:  # ramp_down
                                el = now_d - st_t0
                                if el >= t_dn:
                                    state, st_t0, lvl = "idle", now_d, 0.0
                                else:
                                    rem = max(0.0, 1.0 - el / t_dn)
                                    lvl = 0.15 + (fan_cap - 0.15) * (math.sin(rem * (math.pi / 2)) ** 2)

                            self._fan_state = state
                            self._fan_state_t0 = st_t0
                            self._dehum_until = (now_d + t_dn) if state != "idle" else 0.0
                            if tk._default_root is not None:
                                tk._default_root._dehum_until = self._dehum_until
                            if lvl <= 0.01:
                                if self.ctrl.fan.level > 0:
                                    self.ctrl.fan.off()
                            elif abs(self.ctrl.fan.level - lvl) > 0.02:
                                self.ctrl.fan.set_level(round(lvl, 3))
                            healthy = tuple(i for i in (2, 0, 1) if i not in ff)
                            active_ids = set(healthy.__getitem__(slice(0, want_fans)))
                            if hasattr(self.ctrl.sensors, "fan_count"):
                                self.ctrl.sensors.fan_count = max(1, len(active_ids))
                            if tk._default_root is not None:
                                tk._default_root._fan_levels = tuple((self.ctrl.fan.level if i in active_ids else 0.0) for i in range(3))
            except Exception as e:
                self.ctrl_error = "Sensorfehler – Pumpe/Lüfter aus"
                tk._default_root._app_ctrl_error = True
                self.ctrl.pump.off()
                self.ctrl.fan.off()
                if f"ctrl:{type(e).__name__}" not in self._seen:
                    self._seen.add(f"ctrl:{type(e).__name__}")
                    log.warning("Sensorfehler (%s) – Pumpe/Lüfter abgeschaltet und gesperrt", type(e).__name__)
                self.ctrl.shutdown()
            for k, page in enumerate(self.pages):
                if (k == 0 and r is None) or (k > 0 and self.pr is None):
                    continue
                try:
                    page.refresh(r, self.pr)
                except Exception:
                    self._log_once(f"page{k}", f"Fehler beim Aktualisieren von Seite {k}")
            if self.diagram_open:
                self.diagram_view.update_data(r, self.pr, self.ctrl, self.manager, self.env)
            w_obj = getattr(self.pages[0], "_last_weather_obj", None) if self.pages else None
            # Wichtige Laufmeldungen (Live-Wetter, Lüfter & Pumpe) auch für Doppelklick-Nutzer in den Ereignisspeicher schreiben
            if w_obj is not None:
                w_sig = f"{getattr(w_obj, 'location_name', 'Altenkirchen')}:{getattr(w_obj, 'temperature_c', 0):.1f}:{getattr(w_obj, 'weather_text', '')}"
                if w_sig != getattr(self, "_last_logged_weather", ""):
                    self._last_logged_weather = w_sig
                    self.log_event(
                        f"[INFO] Wetter {getattr(w_obj, 'location_name', 'Altenkirchen')}: "
                        f"{getattr(w_obj, 'temperature_c', 0):.1f} °C, {getattr(w_obj, 'weather_text', '')} "
                        f"({getattr(w_obj, 'cloud_cover_pct', 0):.0f} % Wolken, Wind {getattr(w_obj, 'wind_kmh', 0):.0f} km/h)"
                    )
            if r is not None:
                fl = getattr(tk._default_root, "_fan_levels", None) if tk._default_root is not None else None
                fp = getattr(tk._default_root, "_fan_parked", set()) if tk._default_root is not None else set()
                ff = getattr(tk._default_root, "_fan_faults", set()) if tk._default_root is not None else set()
                if fl and len(fl) == 3:
                    parts = []
                    for i in range(3):
                        if i in ff:
                            parts.append(f"L{i+1} FEHLER")
                        elif i in fp:
                            parts.append(f"L{i+1} PARK")
                        else:
                            parts.append(f"L{i+1} {round(fl.__getitem__(i) * 100)}%")
                    f_sig = " · ".join(parts)
                else:
                    f_sig = f"Lüfter {round(self.ctrl.fan.level * 100)}%"
                f_active = self.ctrl.fan.level > 0.01
                prev_f_active = getattr(self, "_last_logged_fan_active", None)
                prev_f_sig = getattr(self, "_last_logged_fan_sig", "")
                if (f_active != prev_f_active) or (f_active and round(self.ctrl.fan.level * 10) in (2, 5, 8, 10) and f_sig != prev_f_sig):
                    self._last_logged_fan_active = f_active
                    self._last_logged_fan_sig = f_sig
                    mode_lbl = "Hand" if self.ctrl.manual else "Auto"
                    self.log_event(f"[INFO] Lüfter ({mode_lbl}): {f_sig} (Zelt {r.temperature_c:.1f} °C · {r.humidity_pct:.0f} % Feuchte)")

                p_active = self.ctrl.pump.level > 0.01
                prev_p_active = getattr(self, "_last_logged_pump_active", None)
                if p_active != prev_p_active:
                    self._last_logged_pump_active = p_active
                    t_rem = float(getattr(self.env, "tank_liters", 0.0)) if self.env is not None else 0.0
                    if p_active:
                        self.log_event(f"[INFO] Bewässerung START: Pumpe {round(self.ctrl.pump.level * 100)} % (Boden {r.soil_moisture_pct:.0f} % · Tonne {t_rem:.1f} L)")
                    else:
                        self.log_event(f"[INFO] Bewässerung STOP: Pumpe AUS (Boden {r.soil_moisture_pct:.0f} % · Tonne {t_rem:.1f} L)")
            self.garden_view.update_data(r, self.pr, self.ctrl, self.manager, self.env, weather=w_obj)
            has_alarm = bool(self.ctrl_error or self.ctrl.pump_fault or (getattr(tk._default_root, "_fan_faults", set()) if tk._default_root is not None else set()))
            if (not self.garden_open and not has_alarm
                    and (time.monotonic() - getattr(self, "_last_user_activity", time.monotonic())) >= 300.0):
                self.open_garden(as_screensaver=True)
            self.draw_header()
        finally:
            self.root.after(self.interval_ms, self.tick)   # der Takt darf nie abreißen

    def close(self) -> None:
        """Beenden (Fenster schließen oder Esc): erst die Steckdosen, dann Lüfter/Pumpe ausschalten, dann das Fenster schließen."""
        self.manager.shutdown()
        self.ctrl.shutdown()
        self.root.destroy()


def main() -> None:
    """Programmstart: Argumente lesen, Hardware oder Simulation aufbauen, alles verbinden, Fenster starten.

    Wichtige Argumente: --real (echte Hardware statt Simulation), --power sim|vedirect|modbus (Quelle für
    Solar/Akku), --hw (Pfad zur hardware.toml), --fullscreen (Touchscreen), --simpanel (Simulations-Labor
    auch im Vollbild), --interval (Sekunden pro Takt), --version.

    Zusammengesteckt wird so: Aktoren (Lüfter, Pumpe, Steckdosen-Relais) und Sensoren (simuliert oder
    echt), die Solar-/Akku-Quelle (SimPower oder aus power_hw), daraus Controller und PowerManager,
    beide gehen an die App. In der Simulation teilen sich Sensoren, SimPower und das Simulations-Labor
    das SimEnvironment.
    """
    ap = argparse.ArgumentParser(description=BUILD)
    ap.add_argument("--version", action="version", version=BUILD)
    ap.add_argument("--sim", action="store_true", help="Simulation (Standard, nur der Klarheit halber)")
    ap.add_argument("--real", action="store_true", help="Echte Hardware statt Simulation")
    ap.add_argument("--fullscreen", action="store_true", help="Vollbild (für den Touchscreen)")
    ap.add_argument("--simpanel", action="store_true", help="Simulations-Labor auch im Vollbild öffnen")
    ap.add_argument("--interval", type=float, default=1.0, help="Sekunden zwischen Messungen")
    ap.add_argument("--history", type=int, default=60, help="Messpunkte in den Verlaufslinien")
    ap.add_argument("--power", choices=["sim", "vedirect", "modbus"], default=None,
                    help="Quelle für Solar/Akku (Standard: sim in der Simulation, sonst vedirect)")
    ap.add_argument("--hw", default=str(Path(__file__).with_name("hardware.toml")), help="Pfad zur hardware.toml")
    args = ap.parse_args()
    sim = not args.real
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    log.info("%s – %s", BUILD, "Simulation" if sim else "echte Hardware")

    cfg, pcfg = Config(interval_s=args.interval), load_power_config(args.hw)
    env = None
    if sim:
        from sim_panel import SimControlWindow, SimEnvironment
        env = SimEnvironment()
        fan, pump = SimActuator("Lüfter"), SimActuator("Pumpe")
        sensors = SimSensors(fan, pump, env=env)
        sensors.nominal_v = float(cfg.pump_voltage_v)
        sensors.fan_count = cfg.fan_count
        outlets = [Outlet(f"Steckdose {i}", SimActuator(f"Steckdose {i}")) for i in (1, 2)]
    else:
        fan, pump = GpioActuator("Lüfter", 17), GpioActuator("Pumpe", 27)
        sensors = PiSensors()
        outlets = [Outlet("Steckdose 1", GpioRelay("Steckdose 1", 22)),
                   Outlet("Steckdose 2", GpioRelay("Steckdose 2", 23))]
    kind = args.power or ("sim" if sim else "vedirect")
    if kind == "sim":
        source = SimPower(pcfg, fan, pump, outlets, t0=getattr(sensors, "t0", None), env=env)
        source.fan_total_w = cfg.fan_count * cfg.fan_power_per_unit_w
        if hasattr(sensors, "power_source"):
            sensors.power_source = source
    else:
        source = build_source(kind, args.hw, pcfg)
    ctrl = Controller(cfg, sensors, fan, pump)
    manager = PowerManager(pcfg, source, outlets)

    root = tk.Tk()
    root.title(BUILD)
    root.geometry("800x480")
    root.configure(bg=BG)
    if args.fullscreen:
        root.attributes("-fullscreen", True)
        root.config(cursor="none")
    app = App(root, ctrl, manager, cfg, pcfg, sim, args.history, env=env)
    if env is not None and (args.simpanel or not args.fullscreen):
        sim_win = SimControlWindow(root, env, source if kind == "sim" else None, app=app)
        app.sim_reset_cb = sim_win.reset_faults
    root.bind("<Escape>", lambda e: app.close())
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Simulations-Labor: ein zweites Fenster mit Reglern, um die Simulation gezielt zu steuern.

Wozu? Ohne echte Anlage will man trotzdem sehen, wie die Steuerung auf unterschiedliche
Bedingungen reagiert: heißer Sommertag, trüber Herbst, Frost, Nacht, hoher Verbrauch, leerer Akku.
Dieses Fenster stellt dafür die Regler bereit. Es erscheint nur in der Simulation (nicht mit --real)
und im Vollbild nur auf Wunsch (--simpanel).

Wie hängt das mit dem Rest zusammen? Über ein gemeinsames Objekt, SimEnvironment ("Umgebung").
Das Fenster SCHREIBT in dieses Objekt, die simulierten Sensoren (greenhouse.SimSensors) und die
simulierte Solaranlage (power.SimPower) LESEN daraus. Es gibt keine weitere Verbindung. Dadurch
bleiben Simulation und Fenster unabhängig voneinander.

Zwei Betriebsarten (oben im Fenster umschaltbar):

    Simulation    Jahreszeit-Knöpfe und Regler bestimmen Temperatur, Bewölkung, Tageszeit. Die
                  Tageszeit läuft im Zeitraffer (ein Tag = 10 Minuten) oder lässt sich festhalten.
    Live-Wetter   Die Werte kommen vom echten Wetter (siegen_wetter.py). Die Tageszeit ist die echte
                  Uhrzeit, und die Solarleistung folgt der gemessenen Sonnenstrahlung. Die Regler
                  für Temperatur und Bewölkung werden dann nur zur Anzeige nachgeführt.

Zusätzlich gibt es einen Regler für eine Zusatzlast (Stresstest für die Akkus) und einen für den
Ladezustand der Akkus, mit dem man direkt einen leeren Akku herstellen kann, um den Steckdosen-Schutz
zu testen.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from dataclasses import dataclass

from siegen_wetter import LOCATION_NAME, SiegenWeatherClient


@dataclass
class SimEnvironment:
    """Die gemeinsame "Umgebung" der Simulation: Hier stehen die Bedingungen, unter denen simuliert wird.

        live_siegen          True = Live-Wetter ist aktiv (Name historisch; gilt für den eingestellten Ort)
        season               Name der gewählten Jahreszeit (Schlüssel in SEASONS)
        outside_temp_base_c  mittlere Außentemperatur in Grad Celsius
        temp_swing_c         Tagesschwankung der Temperatur (+/-); im Live-Modus 0, weil dann die echte
                             Temperatur gilt
        sun_peak_factor      Stärke der Sonne (1.0 = Hochsommer)
        cloud_cover_pct      Bewölkung in Prozent
        live_radiation_wm2   gemessene Sonnenstrahlung (nur im Live-Modus, sonst None)
        extra_load_w         zusätzlicher Verbraucher in Watt (Stresstest für die Akkus)
        manual_hour          festgehaltene Uhrzeit (0-24) oder None für den automatischen Tagesverlauf
    """
    live_siegen: bool = False
    season: str = "Frühling"
    outside_temp_base_c: float = 21.0
    temp_swing_c: float = 7.0
    sun_peak_factor: float = 0.85
    cloud_cover_pct: float = 20.0
    live_radiation_wm2: float | None = None
    extra_load_w: float = 0.0
    manual_hour: float | None = None
    outside_humidity_pct: float = 68.0
    tank_liters: float = 180.0
    wind_kmh: float = 10.0
    precipitation_mm: float = 0.0
    rain_sim: bool = False
    sim_hour: float | None = None
    tank_capacity_l: float = 220.0
    weather_scenario: str = "auto" 


# Voreinstellungen der Jahreszeiten für die Simulation:
#   temp   mittlere Außentemperatur in Grad Celsius
#   swing  Schwankung der Temperatur im Tagesverlauf (+/-), Mittag warm, Nacht kühl
#   sun    Faktor für die Sonnenstärke (1.0 = Hochsommer, kleiner im Winter)
#   clouds Bewölkung in Prozent
SEASONS = {
    "Frühling": {"temp": 21.0, "swing": 7.0, "sun": 0.85, "clouds": 20.0},
    "Hochsommer": {"temp": 31.0, "swing": 9.0, "sun": 1.00, "clouds": 5.0},
    "Herbst (trüb)": {"temp": 12.0, "swing": 4.0, "sun": 0.50, "clouds": 65.0},
    "Winter (frostig)": {"temp": 2.0, "swing": 5.0, "sun": 0.30, "clouds": 50.0},
}


def current_calendar_season() -> str:
    """Jahreszeit passend zum aktuellen Datum (zur Anzeige im Live-Modus)."""
    m = time.localtime().tm_mon
    if m in (3, 4, 5):
        return "Frühling"
    if m in (6, 7, 8):
        return "Hochsommer"
    if m in (9, 10, 11):
        return "Herbst (trüb)"
    return "Winter (frostig)"


class SimControlWindow(tk.Toplevel):
    """Das Simulations-Labor-Fenster (Toplevel neben der Hauptoberfläche).

    Aufbau von oben nach unten: Umschalter Simulation/Live-Wetter, Jahreszeit-Knöpfe, Regler für
    Außentemperatur, Bewölkung und Zusatzlast, Tageszeit (automatisch oder fest), Akku-Ladezustand.
    Alle 0,5 Sekunden (_tick_ui) werden Anzeigen aktualisiert und im Live-Modus die Wetterwerte
    übernommen. Jede Änderung eines Reglers landet über _on_change im SimEnvironment.
    """
    DAY_S = 1440.0

    def __init__(self, master: tk.Tk, env: SimEnvironment, power_source=None, app=None):
        super().__init__(master)
        self.env = env
        self.power_source = power_source
        self.app = app
        self.t0 = getattr(power_source, "t0", time.monotonic())
        self.weather_client = SiegenWeatherClient()

        self.title(f"Simulations-Labor & Live-Wetter {LOCATION_NAME}")
        self.configure(bg="#eef2eb")
        self._geom_file = __import__("pathlib").Path.home() / ".greenhouse_sim_geom"
        if self._geom_file.exists():
            self.geometry(self._geom_file.read_text(encoding="utf-8").strip())
        else:
            self.geometry("460x680+820+10")
        self.bind("<Configure>", self._save_geom)

        # Scrollbarer Innenbereich (Seitenleiste rechts + Mausrad)
        outer = tk.Frame(self, bg="#eef2eb")
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg="#eef2eb", highlightthickness=0)
        vbar = tk.Scrollbar(outer, orient="vertical", command=canvas.yview, width=16)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._body = body = tk.Frame(canvas, bg="#eef2eb")
        win_id = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-2, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(2, "units"))

        mode_box = tk.Frame(body, bg="#dde6d5", bd=1, relief="solid")
        mode_box.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(mode_box, text="Datenquelle für Automatik-Test:", font=("DejaVu Sans", 10, "bold"),
                 bg="#dde6d5", fg="#2b3a2b").pack(anchor="w", padx=8, pady=(6, 2))

        btn_row = tk.Frame(mode_box, bg="#dde6d5")
        btn_row.pack(fill="x", padx=8, pady=(0, 6))
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)

        self.btn_mode_sim = tk.Button(btn_row, text="Simulation (Regler)", command=lambda: self.set_live_mode(False),
                                      relief="flat", pady=6)
        self.btn_mode_sim.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.btn_mode_live = tk.Button(btn_row, text=f"Live-Wetter {LOCATION_NAME}", command=lambda: self.set_live_mode(True),
                                       relief="flat", pady=6)
        self.btn_mode_live.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        self.lbl_siegen_status = tk.Label(mode_box, text=f"{LOCATION_NAME}-Wetter: bereit im Hintergrund",
                                          bg="#dde6d5", fg="#2f6b3a", font=("DejaVu Sans", 8, "bold"))
        self.lbl_siegen_status.pack(anchor="w", padx=8, pady=(0, 6))

        tk.Label(body, text="1. Jahreszeit / Szenario (nur im Simulations-Modus)",
                 font=("DejaVu Sans", 10, "bold"), bg="#eef2eb", fg="#2b3a2b").pack(anchor="w", padx=12, pady=(6, 4))
        btn_frame = tk.Frame(body, bg="#eef2eb")
        btn_frame.pack(fill="x", padx=12)
        self.season_btns: dict[str, tk.Button] = {}
        for i, name in enumerate(SEASONS):
            b = tk.Button(btn_frame, text=name, command=lambda n=name: self.apply_season(n),
                          bg="#2f6b3a", fg="white", relief="flat", padx=6, pady=6)
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3)
            self.season_btns[name] = b
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        self.rain_sim = False
        self.btn_rain = tk.Button(
            body, text="Regen-Simulation: AUS", command=self.toggle_rain_sim,
            bg="#4a6572", fg="white", relief="flat", padx=8, pady=7,
            font=("DejaVu Sans", 9, "bold"), cursor="hand2"
        )
        self.btn_rain.pack(fill="x", padx=15, pady=(6, 3))

        scen_row = tk.Frame(body, bg="#eef2eb")
        scen_row.pack(fill="x", padx=12, pady=(2, 4))
        self.scen_btns: dict[str, tk.Button] = {}
        for idx_s, (s_key, s_lbl) in enumerate((
            ("auto", "Tages-Wetter"), ("klar", "Klar"), ("nebel", "Nebel"), ("sturm", "Sturm"), ("gewitter", "Gewitter")
        )):
            sb = tk.Button(scen_row, text=s_lbl, command=lambda k=s_key: self.set_weather_scenario(k),
                           bg="#5b6e5d", fg="white", relief="flat", padx=3, pady=4, font=("DejaVu Sans", 8, "bold"))
            sb.grid(row=0, column=idx_s, sticky="ew", padx=2)
            scen_row.columnconfigure(idx_s, weight=1)
            self.scen_btns[s_key] = sb

        self.lbl_day_weather = tk.Label(body, text="Tagesablauf: Saison-Zufallsgenerator bereit",
                                        bg="#eef2eb", fg="#2b4c32", font=("DejaVu Sans", 8, "bold"))
        self.lbl_day_weather.pack(anchor="w", padx=14, pady=(0, 4))

        # Tageszeit-Einheit (Auto-Tag-Button + Geschwindigkeit + Uhrzeit überlappungsfrei in einer Box)
        time_box = tk.Frame(body, bg="#e2ebd9", bd=1, relief="solid")
        time_box.pack(fill="x", padx=12, pady=(4, 6))
        t_hdr = tk.Frame(time_box, bg="#e2ebd9")
        t_hdr.pack(fill="x", padx=8, pady=(6, 2))
        t_hdr.columnconfigure(0, weight=1)
        tk.Label(t_hdr, text="Tageszeit-Steuerung",
                 font=("DejaVu Sans", 9, "bold"), bg="#e2ebd9", fg="#2b3a2b").grid(row=0, column=0, sticky="w")
        self.auto_time = tk.BooleanVar(value=True)
        self.btn_auto_tag = tk.Button(
            t_hdr, text="Auto-Tag: AN", command=self.toggle_auto_tag,
            bg="#2b7ca8", fg="white", activebackground="#1f618d", activeforeground="white",
            bd=0, relief="flat", padx=10, pady=3, font=("DejaVu Sans", 8, "bold"), cursor="hand2"
        )
        self.btn_auto_tag.grid(row=0, column=1, sticky="e")
        self.lbl_tageszeit = tk.Label(
            t_hdr, text="1 Tag = 24.0 min (1.00×) · 24h Simulation",
            font=("DejaVu Sans", 8, "bold"), bg="#e2ebd9", fg="#1f4e78", anchor="w"
        )
        self.lbl_tageszeit.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 0))

        orig_body = self._body
        self._body = time_box
        self.s_speed = self._add_slider("Geschwindigkeit (Stufe 1–14 · bis 3×)", 1, 14, 1, self._on_speed_change, res=1.0)
        self.s_hour = self._add_slider("Simulierte Uhrzeit (0 – 24 Uhr)", 0, 24, 6, None, res=0.1)
        self.s_hour.bind("<Button-1>", self._on_user_hour)
        self.s_hour.bind("<B1-Motion>", self._on_user_hour)
        for child in time_box.winfo_children():
            if isinstance(child, (tk.Label, tk.Scale)):
                child.config(bg="#e2ebd9")
        tk.Frame(time_box, bg="#e2ebd9", height=4).pack(fill="x")
        self._body = orig_body

        self.s_temp = self._add_slider("Außentemperatur (°C)", -20, 40, env.outside_temp_base_c, self._on_change, res=0.5)
        self.lbl_live_temp = tk.Label(body, text="Aktuell draußen: --.- °C",
                                      bg="#eef2eb", fg="#2f6b3a", font=("DejaVu Sans", 9, "bold"))
        self.lbl_live_temp.pack(anchor="w", padx=14, pady=(0, 4))

        self.s_clouds = self._add_slider("Bewölkung (%)", 0, 100, env.cloud_cover_pct, self._on_change)
        self.s_soil = self._add_slider("Bodenfeuchte (%)", 5, 95, 55, self._on_soil_hum_change)
        self.s_hum = self._add_slider("Luftfeuchte (%)", 15, 99, 65, self._on_soil_hum_change)
        self.s_tank = self._add_slider("Regentonne Füllstand (0–220 L)", 0, 220, 180, self._on_tank_change, res=1.0)
        self.s_out1 = self._add_slider("Steckdose 1 Last (W, z.B. 65 W Scooter)", 0, 1500, 65, self._on_outlet_load, res=5.0)
        self.s_out2 = self._add_slider("Steckdose 2 Last (W)", 0, 1500, 150, self._on_outlet_load, res=5.0)
        if self.power_source is not None and hasattr(self.power_source, "soc"):
            tk.Label(body, text="3. Akku-Ladezustand direkt setzen (%)",
                     font=("DejaVu Sans", 10, "bold"), bg="#eef2eb", fg="#2b3a2b").pack(anchor="w", padx=12, pady=(8, 2))
            self.s_soc = self._add_slider("Akku SoC (%)", 5, 100, 70, None)
            self.s_soc.bind("<Button-1>", self._on_user_soc)
            self.s_soc.bind("<B1-Motion>", self._on_user_soc)

        tk.Label(body, text="4. Störfälle provozieren (Schutzlogik-Test)",
                 font=("DejaVu Sans", 10, "bold"), bg="#eef2eb", fg="#8b1e1e").pack(anchor="w", padx=12, pady=(10, 4))
        f_grid = tk.Frame(body, bg="#eef2eb")
        f_grid.pack(fill="x", padx=12, pady=(0, 4))
        f_grid.columnconfigure((0, 1), weight=1)

        def _fbtn(r, c, label, bg, cmd):
            tk.Button(f_grid, text=label, command=cmd, bg=bg, fg="white", bd=0, relief="flat",
                      font=("DejaVu Sans", 9, "bold"), pady=6, cursor="hand2").grid(row=r, column=c, sticky="ew", padx=3, pady=3)

        _fbtn(0, 0, "Pumpen-Trockenlauf", "#b83232", lambda: self._trigger_fault("pump"))
        _fbtn(0, 1, "Kaputte Zelle (10,8 V)", "#b83232", lambda: self._trigger_fault("cell"))
        _fbtn(1, 0, "Akku-Frost (-1,5 °C)", "#2d6a9f", lambda: self._trigger_fault("frost"))
        _fbtn(1, 1, "Sensor-Ausfall", "#c96a1b", lambda: self._trigger_fault("sensor"))

        fan_row = tk.Frame(body, bg="#eef2eb")
        fan_row.pack(fill="x", padx=12, pady=(0, 4))
        fan_row.columnconfigure((0, 1, 2), weight=1)
        for fi in range(3):
            tk.Button(fan_row, text=f"Lüfter {fi+1} Ausfall", command=lambda idx=fi: self._trigger_fault(f"fan_{idx}"),
                      bg="#8b3a62", fg="white", bd=0, relief="flat", font=("DejaVu Sans", 8, "bold"),
                      pady=5, cursor="hand2").grid(row=0, column=fi, sticky="ew", padx=2)


        tk.Button(body, text="Alle Labor-Störungen zurücksetzen", command=self.reset_faults,
                  bg="#2f6b3a", fg="white", bd=0, relief="flat", font=("DejaVu Sans", 9, "bold"),
                  pady=6, cursor="hand2").pack(fill="x", padx=15, pady=(2, 10))

        self._t0 = time.monotonic()
        self._highlight_season(env.season)
        self.set_live_mode(env.live_siegen)
        self.after(500, self._tick_ui)

    def set_live_mode(self, live: bool) -> None:
        """Zwischen Simulation und Live-Wetter umschalten.

        Beim Wechsel auf Live wird sofort eine Wetterabfrage angestoßen. Beim Wechsel zurück auf Simulation
        wird der Tagesgang der Temperatur der gewählten Jahreszeit wiederhergestellt (im Live-Modus war er
        auf 0 gesetzt) und die Live-Strahlung gelöscht, damit wieder nach Sonnenstand und Bewölkung
        gerechnet wird.
        """
        self.env.live_siegen = live
        auto_lock = bool(live or (hasattr(self, "auto_time") and self.auto_time.get()))
        for w in (self.s_temp, self.s_clouds, self.s_hum, self.s_soil, self.s_tank, self.s_hour):
            self._set_slider_auto(w, None, auto_lock)
        if live:
            self.rain_sim = False
            self.env.rain_sim = False
            self.btn_rain.config(text="Regen-Simulation: AUS", bg="#4a6572")
            self.btn_mode_live.config(bg="#3b8fc4", fg="white", font=("DejaVu Sans", 10, "bold"))
            self.btn_mode_sim.config(bg="#b7c4ad", fg="#2b3a2b", font=("DejaVu Sans", 10, "normal"))
            self.weather_client.refresh_now()
        else:
            self.btn_mode_sim.config(bg="#2f6b3a", fg="white", font=("DejaVu Sans", 10, "bold"))
            self.btn_mode_live.config(bg="#b7c4ad", fg="#2b3a2b", font=("DejaVu Sans", 10, "normal"))
            self.env.live_radiation_wm2 = None
            self.env.temp_swing_c = SEASONS[self.env.season]["swing"]   # Tagesgang wiederherstellen
            self._on_change()
        self._highlight_season(self.env.season)

    def _highlight_season(self, active_name: str) -> None:
        """Hebt den Knopf der aktuellen Jahreszeit hervor (im Live-Modus in Blau, sonst in Gelb)."""
        target = current_calendar_season() if self.env.live_siegen else active_name
        active_bg = "#3b8fc4" if self.env.live_siegen else "#e0a030"
        active_fg = "white" if self.env.live_siegen else "#1f2a1f"
        for name, btn in self.season_btns.items():
            if name == target:
                btn.config(bg=active_bg, fg=active_fg, font=("DejaVu Sans", 10, "bold"))
            else:
                btn.config(bg="#2f6b3a", fg="white", font=("DejaVu Sans", 10, "normal"))

    def _save_geom(self, e=None) -> None:
        if e is None or e.widget is self:
            try:
                self._geom_file.write_text(self.geometry(), encoding="utf-8")
            except Exception:
                pass

    def _add_slider(self, label: str, frm: float, to: float, init: float, cmd, res: float = 1.0) -> tk.Scale:
        """Baut Beschriftung und Schieberegler und gibt den Regler zurück."""
        tk.Label(self._body, text=label, bg="#eef2eb", fg="#2b3a2b", font=("DejaVu Sans", 9, "bold")).pack(
            anchor="w", padx=12, pady=(4, 0))
        s = tk.Scale(self._body, from_=frm, to=to, resolution=res, orient="horizontal",
                     command=cmd, bg="#eef2eb", highlightthickness=0)
        s.set(init)
        s.pack(fill="x", padx=12)
        return s

    def _set_slider_auto(self, sc: tk.Scale, val: float | None, locked: bool) -> None:
        """Setzt einen Reglerwert und graut den Regler im Live-/Auto-Modus sichtbar aus."""
        if locked:
            sc.config(state="normal")
            if val is not None:
                sc.set(val)
            sc.config(state="disabled", troughcolor="#c5ccc3", fg="#667066")
        else:
            sc.config(state="normal", troughcolor="#d4ddd0", fg="#1f2b1f")
            if val is not None:
                sc.set(val)

    def _calc_speed_mult(self) -> float:
        step = float(self.s_speed.get()) if hasattr(self, "s_speed") else 1.0
        return 1.0 + (max(1.0, min(14.0, step)) - 1.0) * (2.0 / 13.0)

    def _on_speed_change(self, _=None) -> None:
        mult = self._calc_speed_mult()
        day_min = 24.0 / mult
        if hasattr(self, "lbl_tageszeit"):
            self.lbl_tageszeit.config(text=f"1 Tag = {day_min:.1f} min ({mult:.2f}×) · 24h Simulation")

    def toggle_auto_tag(self) -> None:
        """Schaltet zwischen automatischem 24-Minuten-Tag und manueller Uhrzeit um."""
        if getattr(self.env, "live_siegen", False):
            self.set_live_mode(False)
        new_val = not self.auto_time.get()
        self.auto_time.set(new_val)
        if new_val:
            cur_h = float(self.s_hour.get())
            self._sim_total_hours = float(getattr(self, "_last_rolled_day", 0)) * 24.0 + cur_h
            self._last_tick_m = time.monotonic()
            self.env.manual_hour = None
            self._auto_base_clouds = float(self.s_clouds.get())
            self.btn_auto_tag.config(text="Auto-Tag: AN", bg="#2b7ca8", fg="white")
        else:
            self.env.manual_hour = float(self.s_hour.get())
            self._sim_total_hours = float(getattr(self, "_last_rolled_day", 0)) * 24.0 + float(self.s_hour.get())
            self._last_tick_m = time.monotonic()
            self.btn_auto_tag.config(text="Auto-Tag: AUS", bg="#5b6e5d", fg="white")
        self._on_change()

    def _tick_ui(self) -> None:
        """Läuft alle 500 ms: Anzeigen aktualisieren und die Umgebung nachführen.

        Live-Modus: Wetterwerte (Temperatur, Bewölkung, Strahlung) und die echte Uhrzeit ins SimEnvironment
        übernehmen. Simulations-Modus: im Automatik-Betrieb die simulierte Uhrzeit aus der vergangenen Zeit
        berechnen (1 Tag = 10 Minuten) und die zu erwartende Außentemperatur anzeigen. Außerdem wird der
        Akku-Regler auf den tatsächlichen Ladezustand nachgeführt.
        """
        w_data, w_err = self.weather_client.get_latest()
        if w_data is not None:
            self.lbl_siegen_status.config(
                fg="#2f6b3a",
                text=f"{LOCATION_NAME} aktuell: {w_data.temperature_c:.1f} °C · {w_data.cloud_cover_pct:.0f} % Wolken · {w_data.radiation_wm2:.0f} W/m²"
            )
        elif w_err:
            self.lbl_siegen_status.config(fg="#d2553f", text=f"{LOCATION_NAME} offline (Fallback aktiv): {w_err[:38]}")

        if self.env.live_siegen and w_data is not None:
            lt = time.localtime()
            now_hour = lt.tm_hour + lt.tm_min / 60.0
            self.env.outside_temp_base_c = w_data.temperature_c
            self.env.temp_swing_c = 0.0
            self.env.cloud_cover_pct = w_data.cloud_cover_pct
            self.env.live_radiation_wm2 = w_data.radiation_wm2
            self.env.manual_hour = now_hour

            w_spd = float(getattr(w_data, "wind_kmh", 10.0))
            w_cd = int(getattr(w_data, "weather_code", 1))
            self.env.wind_kmh = w_spd
            if w_cd in (95, 96, 99):
                self.env.weather_scenario = "gewitter"
            elif w_cd in (45, 48):
                self.env.weather_scenario = "nebel"
            elif w_spd >= 34.0:
                self.env.weather_scenario = "sturm"
            elif w_cd in (51, 53, 55, 61, 63, 65, 71, 73, 75, 77, 80, 81, 82, 85, 86):
                self.env.weather_scenario = "regen"
            else:
                self.env.weather_scenario = "klar"
            self.lbl_day_weather.config(text=f"Live-Szene: {getattr(w_data, "weather_text", "Aktiv")} ({w_spd:.0f} km/h)")
            self.env.precipitation_mm = float(getattr(w_data, "precipitation_mm", 0.0))
            self.env.outside_humidity_pct = float(w_data.humidity_pct)
            self.env.override_hum_pct = None
            self.env.override_soil_pct = None
            last_r = getattr(self.app, "last_r", None) if self.app is not None else None
            live_in_hum = round(last_r.humidity_pct) if last_r is not None else round(w_data.humidity_pct)
            live_in_soil = round(last_r.soil_moisture_pct) if last_r is not None else round(self.s_soil.get())
            t_cur = round(float(getattr(self.env, "tank_liters", 180.0)), 1)
            for sc, val in (
                (self.s_temp, round(w_data.temperature_c, 1)),
                (self.s_clouds, round(w_data.cloud_cover_pct)),
                (self.s_hum, live_in_hum),
                (self.s_soil, live_in_soil),
                (self.s_tank, t_cur),
                (self.s_hour, round(now_hour, 1)),
                (self.s_speed, None),
            ):
                self._set_slider_auto(sc, val, True)
            self.lbl_live_temp.config(
                text=f"LIVE {LOCATION_NAME} ({lt.tm_hour:02d}:{lt.tm_min:02d} Uhr): {w_data.temperature_c:.1f} °C · Außenluft {w_data.humidity_pct:.0f} % · {w_data.radiation_wm2:.0f} W/m²"
            )
        else:
            auto_on = bool(self.auto_time.get())
            if hasattr(self, "s_speed"):
                self._set_slider_auto(self.s_speed, None, False)
            if auto_on:
                now_m = time.monotonic()
                if not hasattr(self, "_sim_total_hours"):
                    self._sim_total_hours = float(self.s_hour.get())
                    self._last_tick_m = now_m
                dt_m = max(0.0, now_m - getattr(self, "_last_tick_m", now_m))
                self._last_tick_m = now_m
                self._sim_total_hours += (dt_m * self._calc_speed_mult() / self.DAY_S) * 24.0
                day_num = int(self._sim_total_hours // 24.0)
                if getattr(self.env, "weather_scenario", "auto") == "auto" or hasattr(self, "_last_rolled_day"):
                    if getattr(self, "_last_rolled_day", -1) != day_num:
                        self._roll_daily_weather(day_idx=day_num)
                sim_hour = self._sim_total_hours % 24.0
                self.env.sim_hour = sim_hour
                phase = ((sim_hour - 6.0) / 24.0) * 2 * math.pi
                cur_outside = self.env.outside_temp_base_c + self.env.temp_swing_c * math.sin(phase)
                last_r = getattr(self.app, "last_r", None) if self.app is not None else None
                in_h = round(last_r.humidity_pct) if last_r is not None else None
                in_s = round(last_r.soil_moisture_pct) if last_r is not None else None
                t_cur = round(float(getattr(self.env, "tank_liters", 180.0)), 1)
                self._set_slider_auto(self.s_hour, round(sim_hour, 1), True)
                self._set_slider_auto(self.s_temp, round(cur_outside, 1), True)
                self._set_slider_auto(self.s_hum, in_h, True)
                self._set_slider_auto(self.s_soil, in_s, True)
                self._set_slider_auto(self.s_tank, t_cur, True)
                if getattr(self, "_auto_weather_mode", True) and hasattr(self, "_day_profile"):
                    d_desc, d_cl, d_wnd, d_hum, d_sc = self._day_profile
                    if d_sc == "nebel":
                        active_front = (sim_hour < 9.5 or sim_hour >= 20.5)
                        cur_sc = "nebel" if active_front else "klar"
                        cur_rain = False
                        cur_cl = round(84.0 if active_front else 26.0 + 10.0 * math.sin(phase))
                        cur_wnd = 4.0 if active_front else 11.0
                        self.env.outside_humidity_pct = 93.0 if active_front else 62.0
                        sub_txt = "Morgennebel/Abendnebel" if active_front else "Nebel gelichtet (heiter)"
                    elif d_sc == "regen":
                        active_front = (7.0 <= sim_hour <= 10.5) or (15.5 <= sim_hour <= 19.0)
                        cur_sc = "regen" if active_front else "klar"
                        cur_rain = active_front
                        cur_cl = round(91.0 if active_front else 46.0 + 12.0 * math.sin(phase))
                        cur_wnd = d_wnd if active_front else max(8.0, d_wnd * 0.6)
                        self.env.outside_humidity_pct = 90.0 if active_front else 68.0
                        sub_txt = "Regen-/Schneefront aktiv" if active_front else "Niederschlagspause"
                    elif d_sc == "gewitter":
                        active_front = (14.5 <= sim_hour <= 18.0)
                        cur_sc = "gewitter" if active_front else "klar"
                        cur_rain = active_front
                        cur_cl = round(98.0 if active_front else (38.0 if sim_hour < 14.5 else 24.0))
                        cur_wnd = 38.0 if active_front else 12.0
                        self.env.outside_humidity_pct = 91.0 if active_front else 60.0
                        sub_txt = "Gewitterfront zieht durch" if active_front else ("Schwül vor Gewitter" if sim_hour < 14.5 else "Abendklar nach Gewitter")
                    elif d_sc == "sturm":
                        active_front = (8.0 <= sim_hour <= 12.0) or (16.0 <= sim_hour <= 19.5)
                        cur_sc = "sturm" if active_front else "klar"
                        cur_rain = active_front
                        cur_cl = round(92.0 if active_front else 52.0)
                        cur_wnd = d_wnd if active_front else 21.0
                        self.env.outside_humidity_pct = 86.0 if active_front else 65.0
                        sub_txt = "Sturmböen & Schauer" if active_front else "Windige Zwischenaufhellung"
                    else:
                        cur_sc, cur_rain = "klar", False
                        cur_cl = round(max(0.0, min(100.0, d_cl + 12.0 * math.sin(phase * 1.5))))
                        cur_wnd = d_wnd
                        self.env.outside_humidity_pct = d_hum
                        sub_txt = "Freundlicher Tagesverlauf"
                    self.env.weather_scenario = cur_sc
                    self.rain_sim = cur_rain
                    self.env.rain_sim = cur_rain
                    self.env.wind_kmh = cur_wnd
                    self.btn_rain.config(text=f"Regen-Simulation: AKTIV ({cur_cl} % Wolken)" if cur_rain else "Regen-Simulation: AUS", bg="#2b7ca8" if cur_rain else "#4a6572")
                    self.lbl_day_weather.config(text=f"Tag {day_num + 1}: {d_desc} · {sub_txt}")
                else:
                    base_cl = float(getattr(self, "_auto_base_clouds", self.env.cloud_cover_pct))
                    cur_cl = round(max(0.0, min(100.0, base_cl + 12.0 * math.sin(phase * 1.5))))
                self.env.cloud_cover_pct = float(cur_cl)
                self._set_slider_auto(self.s_clouds, cur_cl, True)
                self.env.override_hum_pct = None
                self.env.override_soil_pct = None
            else:
                for sc in (self.s_hour, self.s_temp, self.s_clouds, self.s_hum, self.s_soil, self.s_tank):
                    self._set_slider_auto(sc, None, False)
                sim_hour = float(self.s_hour.get())
                phase = ((sim_hour - 6.0) / 24.0) * 2 * math.pi
                cur_outside = self.env.outside_temp_base_c + self.env.temp_swing_c * math.sin(phase)

            self.lbl_live_temp.config(
                text=f"Simulation ({sim_hour:04.1f} Uhr): {cur_outside:.1f} °C  "
                     f"(Spanne: {self.env.outside_temp_base_c - self.env.temp_swing_c:.0f} bis "
                     f"{self.env.outside_temp_base_c + self.env.temp_swing_c:.0f} °C)"
            )

        if hasattr(self, "s_soc") and self.power_source is not None:
            self.s_soc.set(round(self.power_source.soc))

        self.after(500, self._tick_ui)

    def set_weather_scenario(self, scen: str) -> None:
        """Aktiviert ein Wetterszenario (klar, regen, nebel, sturm, gewitter) im Simulations-Labor."""
        """Setzt ein gezieltes Wetterszenario (klar, nebel, sturm, gewitter) oder den Auto-Tagesgenerator."""
        if getattr(self.env, "live_siegen", False):
            self.set_live_mode(False)
        self.env.weather_scenario = scen
        for k, b in getattr(self, "scen_btns", {}).items():
            b.config(bg="#2b7ca8" if k == scen else "#5b6e5d")
        if scen != "auto":
            self._auto_weather_mode = False
        if scen == "klar":
            self.rain_sim = False
            self.env.rain_sim = False
            self.s_clouds.set(12.0)
            self.env.wind_kmh = 9.0
            self.btn_rain.config(text="Regen-Simulation: AUS", bg="#4a6572")
            self.lbl_day_weather.config(text="Szenario: Klarer Himmel & leichte Brise (9 km/h)")
        elif scen == "nebel":
            self.rain_sim = False
            self.env.rain_sim = False
            self.s_clouds.set(85.0)
            self.s_hum.set(94.0)
            self.env.wind_kmh = 4.0
            self.btn_rain.config(text="Regen-Simulation: AUS", bg="#4a6572")
            self.lbl_day_weather.config(text="Szenario: Dichter Nebel (94 % Feuchte, 4 km/h)")
        elif scen == "sturm":
            self.rain_sim = True
            self.env.rain_sim = True
            self.s_clouds.set(92.0)
            self.s_hum.set(86.0)
            self.env.wind_kmh = 46.0
            self.btn_rain.config(text="Regen-Simulation: STURM (92 % Wolken)", bg="#2b7ca8")
            self.lbl_day_weather.config(text="Szenario: Kräftiger Sturm (46 km/h Böen)")
        elif scen == "gewitter":
            self.rain_sim = True
            self.env.rain_sim = True
            self.s_clouds.set(98.0)
            self.s_hum.set(92.0)
            self.env.wind_kmh = 38.0
            self.btn_rain.config(text="Regen-Simulation: GEWITTER (98 % Wolken)", bg="#2b7ca8")
            self.lbl_day_weather.config(text="Szenario: Gewitterfront mit Blitzen & Starkregen")
        else:
            self._roll_daily_weather(force=True)
            return
        self._on_change()
        self._on_soil_hum_change()

    def _roll_daily_weather(self, day_idx: int = 0, force: bool = False) -> None:
        """Würfelt für jeden simulierten 24-Minuten-Tag einen realistischen, zur Jahreszeit passenden Tagesablauf."""
        if getattr(self.env, "live_siegen", False):
            return
        if not force and getattr(self, "_last_rolled_day", -1) == day_idx:
            return
        self._last_rolled_day = day_idx
        import random
        season = self.env.season
        rng = random.Random(int(time.time() // 60) + day_idx * 97 + len(season) * 13)
        pools = {
            "Frühling": [
                ("Sonniger Frühlingstag", 22.0, 7.5, 15.0, 11.0, 58.0, "klar", False),
                ("Wechselhaftes Aprilwetter", 17.5, 6.0, 62.0, 22.0, 72.0, "klar", False),
                ("Frühlingsregen", 15.0, 4.0, 88.0, 18.0, 88.0, "regen", True),
                ("Morgennebel im Tal", 14.5, 5.5, 80.0, 5.0, 93.0, "nebel", False),
            ],
            "Hochsommer": [
                ("Strahlender Hochsommer", 32.5, 9.5, 6.0, 10.0, 48.0, "klar", False),
                ("Warmer Sommertag", 28.5, 8.0, 28.0, 15.0, 56.0, "klar", False),
                ("Schwüle Hitze & Sommergewitter", 29.5, 7.0, 96.0, 36.0, 89.0, "gewitter", True),
                ("Sommersturm mit Schauern", 23.0, 5.0, 90.0, 44.0, 85.0, "sturm", True),
            ],
            "Herbst (trüb)": [
                ("Goldener Oktobertag", 15.5, 5.5, 22.0, 12.0, 68.0, "klar", False),
                ("Dichter Herbstnebel", 10.0, 3.0, 86.0, 4.0, 95.0, "nebel", False),
                ("Trüber Regentag", 11.5, 3.5, 92.0, 20.0, 90.0, "regen", True),
                ("Kräftiger Herbststurm", 11.0, 4.0, 94.0, 48.0, 87.0, "sturm", True),
            ],
            "Winter (frostig)": [
                ("Klirrender Winter-Sonnentag", -1.5, 5.5, 10.0, 8.0, 64.0, "klar", False),
                ("Eisiger Winternebel", 0.5, 2.5, 84.0, 4.0, 94.0, "nebel", False),
                ("Dichter Schneefall", -0.5, 3.0, 94.0, 18.0, 90.0, "regen", True),
                ("Wintersturm & Schneetreiben", -2.5, 4.0, 96.0, 47.0, 88.0, "sturm", True),
            ],
        }
        desc, t_base, swing, clouds, wind, hum, scen, is_rain = rng.choice(pools.get(season, pools["Frühling"]))
        self._auto_weather_mode = True
        self._day_profile = (desc, clouds, wind, hum, scen)
        self.env.temp_swing_c = swing
        self.env.wind_kmh = wind
        self.env.weather_scenario = scen
        self.rain_sim = is_rain
        self.env.rain_sim = is_rain
        self.s_temp.set(round(t_base, 1))
        self._auto_base_clouds = clouds
        self._set_slider_auto(self.s_clouds, clouds, bool(self.auto_time.get()))
        self.env.outside_humidity_pct = hum
        self.s_hum.set(hum)
        self.btn_rain.config(
            text=f"Regen-Simulation: AKTIV ({clouds:.0f} % Wolken)" if is_rain else "Regen-Simulation: AUS",
            bg="#2b7ca8" if is_rain else "#4a6572",
        )
        for k, b in getattr(self, "scen_btns", {}).items():
            b.config(bg="#2b7ca8" if k == "auto" else "#5b6e5d")
        self.lbl_day_weather.config(text=f"Tag {day_idx + 1}: {desc} ({wind:.0f} km/h)")
        self._on_change()
        self._on_soil_hum_change()

    def toggle_rain_sim(self) -> None:
        """Schaltet die 1-Klick-Regensimulation im Labor ein oder aus."""
        self.rain_sim = not getattr(self, "rain_sim", False)
        self.env.rain_sim = self.rain_sim
        if self.rain_sim:
            if getattr(self.env, "live_siegen", False):
                self.set_live_mode(False)
            self.env.outside_humidity_pct = 90.0
            self.s_clouds.set(95.0)
            if self.s_hum.get() < 86.0:
                self.s_hum.set(88.0)
            self.btn_rain.config(text="Regen-Simulation: AKTIV (95 % Wolken)", bg="#2b7ca8")
        else:
            self.s_clouds.set(25.0)
            self.btn_rain.config(text="Regen-Simulation: AUS", bg="#4a6572")
        self._on_change()
        self._on_soil_hum_change()

    def apply_season(self, name: str) -> None:
        """Jahreszeit wählen: übernimmt Temperatur, Schwankung, Sonnenfaktor und Bewölkung der Voreinstellung."""
        if self.env.live_siegen:
            self.set_live_mode(False)
        preset = SEASONS[name]
        self.env.season = name
        self.env.sun_peak_factor = preset["sun"]
        self.env.temp_swing_c = preset["swing"]
        self.s_temp.set(preset["temp"])
        self.s_clouds.set(preset["clouds"])
        self._highlight_season(name)
        self._roll_daily_weather(day_idx=getattr(self, "_last_rolled_day", 0), force=True)

    def _on_tank_change(self, _=None) -> None:
        if hasattr(self, "s_tank"):
            self.env.tank_liters = float(self.s_tank.get())

    def _on_soil_hum_change(self, _=None) -> None:
        self.env.override_soil_pct = float(self.s_soil.get())
        self.env.override_hum_pct = float(self.s_hum.get())

    def _on_user_hour(self, _=None) -> None:
        """Der Nutzer greift den Uhrzeit-Regler: Live-Wetter und automatischer Tagesverlauf werden beendet."""
        if self.env.live_siegen:
            self.set_live_mode(False)
        self.auto_time.set(False)
        if hasattr(self, "btn_auto_tag"):
            self.btn_auto_tag.config(text="Auto-Tag: AUS", bg="#5b6e5d", fg="white")
        self.after(10, self._on_change)

    def _trigger_fault(self, kind: str) -> None:
        if self.app is None:
            return
        if kind == "pump":
            self.app.sim_fault_pump = "Pumpe gesperrt – Trockenlauf erkannt → Pumpe AUS und gesperrt"
            self.app.ctrl.pump_fault = self.app.sim_fault_pump
            self.app.ctrl.pump.off()
            print(f"{time.strftime('%H:%M:%S')} Pumpe abgeschaltet und gesperrt: Trockenlauf erkannt")
            if hasattr(self.app, "pages") and hasattr(self.app.pages, "s_pump"):
                self.app.pages[0].s_pump.set(0.0)
            self.app.draw_header()
        elif kind == "sensor":
            self.app.sim_fault_sensor = "Sensorfehler – Klimasensor prüfen → Pumpe & 3× Lüfter AUS und gesperrt"
            self.app.ctrl_error = self.app.sim_fault_sensor
            tk._default_root._app_ctrl_error = True
            self.app.ctrl.pump.off()
            self.app.ctrl.fan.off()
            if hasattr(self.app, "pages") and len(self.app.pages) > 0:
                gp = self.app.pages[0]
                if hasattr(gp, "s_pump"): gp.s_pump.set(0.0)
                for sf in getattr(gp, "s_fans", ()): sf.set(0.0)
            self.app.draw_header()
        elif kind == "cell":
            self.app.sim_fault_cell_v = 10.8
            if self.power_source is not None: self.power_source.fault_cell_v = 10.8
            self.app.manager._lock("Fail-Safe: Zell-Unterspannung (< 11,5 V)")
        elif kind == "frost":
            self.app.sim_fault_temp_c = -1.5
            if self.power_source is not None:
                self.power_source.fault_temp_c = -1.5
        elif kind.startswith("fan_"):
            idx = int(kind.split("_")[1])
            faults = getattr(self.app, "sim_fault_fans", set())
            faults.add(idx)
            self.app.sim_fault_fans = faults
            tk._default_root._fan_faults = set(faults)
            self.app.ctrl.fan._fault_indices = set(faults)
            if hasattr(self.app, "pages") and hasattr(self.app.pages, "s_fans"):
                self.app.pages[0].s_fans[idx].set(0.0)
                vals = [0.0 if i in faults else sf.value for i, sf in enumerate(self.app.pages[0].s_fans)]
                self.app.ctrl.fan.set_level(sum(vals) / len(vals))
            active = [str(i + 1) for i in range(3) if i not in faults]
            act_txt = ", ".join(active) if active else "keine"
            msg = f"Lüfter {idx + 1} Störung (blockiert/Spannung) → Lüfter {idx + 1} AUS & gesperrt | Aktive Lüfter: {act_txt}"
            self.app.sim_fault_fan_msg = msg
            self.app.log_event(f"ALARM: {msg}")
            print(f"{time.strftime('%H:%M:%S')} {msg}")
            self.app.draw_header()
        elif kind.startswith("fan_"):
            idx = int(kind.split("_")[1])
            faults = getattr(self.app, "sim_fault_fans", set())
            faults.add(idx)
            self.app.sim_fault_fans = faults
            tk._default_root._fan_faults = set(faults)
            self.app.ctrl.fan._fault_indices = set(faults)
            if hasattr(self.app, "pages") and hasattr(self.app.pages, "s_fans"):
                self.app.pages[0].s_fans[idx].set(0.0)
                vals = [0.0 if i in faults else sf.value for i, sf in enumerate(self.app.pages[0].s_fans)]
                self.app.ctrl.fan.set_level(sum(vals) / len(vals))
            active = [str(i + 1) for i in range(3) if i not in faults]
            act_txt = ", ".join(active) if active else "keine"
            msg = f"Lüfter {idx + 1} Störung (blockiert/Spannung) → Lüfter {idx + 1} AUS & gesperrt | Aktive Lüfter: {act_txt}"
            self.app.sim_fault_fan_msg = msg
            print(f"{time.strftime('%H:%M:%S')} {msg}")
            self.app.draw_header()
            if self.power_source is not None: self.power_source.fault_temp_c = -1.5

    def reset_faults(self) -> None:
        """Setzt alle im Simulations-Labor ausgelösten Störfälle sauber zurück."""
        if self.app is not None:
            self.app.sim_fault_cell_v = None
            self.app.sim_fault_temp_c = None
        if self.power_source is not None:
            self.power_source.fault_cell_v = None
            self.power_source.fault_temp_c = None
            self.app.sim_fault_pump = None
            self.app.ctrl.clear_pump_fault()
            self.app.sim_fault_sensor = None
            self.app.ctrl_error = None
            tk._default_root._app_ctrl_error = False
        self.app.sim_fault_fans = set()
        self.app.sim_fault_fan_msg = None
        tk._default_root._fan_faults = set()
        if hasattr(self.app, "ctrl"): self.app.ctrl.fan._fault_indices = set()
        self.env.override_soil_pct = None
        self.env.override_hum_pct = None
        if hasattr(self, "s_soil"): self.s_soil.set(55)
        if hasattr(self, "s_hum"): self.s_hum.set(65)

    def _on_user_soc(self, _=None) -> None:
        """Der Nutzer setzt den Akku-Ladezustand direkt: wird sofort in die Simulation übernommen."""
        def apply():
            """Übernimmt eine vordefinierte Last- oder Batteriesituation in die Simulation."""
            if self.power_source is not None:
                self.power_source.soc = float(self.s_soc.get())
        self.after(10, apply)

    def _on_outlet_load(self, _=None) -> None:
        if self.power_source is not None and hasattr(self.power_source, "outlet_w"):
            self.power_source.outlet_w = [float(self.s_out1.get()), float(self.s_out2.get())]

    def _on_change(self, _=None) -> None:
        """Reglerwerte ins SimEnvironment übernehmen (Außentemperatur, Bewölkung, Zusatzlast, feste Uhrzeit).

        Im Live-Modus passiert nichts, dort liefert das echte Wetter die Werte und die Regler werden nur
        nachgeführt.
        """
        if self.env.live_siegen:
            return
        if str(self.s_temp.cget("state")) == "normal":
            self.env.outside_temp_base_c = float(self.s_temp.get())
        self.env.cloud_cover_pct = float(self.s_clouds.get())
        self.env.extra_load_w = 0.0
        self.env.manual_hour = None if self.auto_time.get() else float(self.s_hour.get())

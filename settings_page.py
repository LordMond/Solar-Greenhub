#!/usr/bin/env python3
"""Touch-Einstellungsseite (settings.json) mit Jahreszeit-Profilen für Lüfter- und Pumpenleistung."""

from __future__ import annotations

import json
import os
import tkinter as tk
from dataclasses import replace

BG, CARD, HEADER, ACCENT = "#f4f1e6", "#ffffff", "#2f6b3a", "#2b7ca8"
TEXT, MUTED, GOOD, FONT = "#1f2b1f", "#6b786b", "#2f7a3e", "DejaVu Sans"
SEASON_ORDER = ("Frühling", "Hochsommer", "Herbst", "Winter")

SEASON_DEFAULTS = {
    "Frühling": {"temp_max_c": 25.0, "winter_temp_c": 10.0, "dehum_start_pct": 76.0, "dehum_stop_pct": 65.0, "fan_max_pct": 85.0, "fan_active_cnt": 3.0, "pump_max_pct": 75.0, "pump_run_s": 60.0, "pump_interval_min": 20.0, "soil_min_pct": 40.0, "soil_hi_pct": 88.0},
    "Hochsommer": {"temp_max_c": 26.5, "winter_temp_c": 10.0, "dehum_start_pct": 74.0, "dehum_stop_pct": 62.0, "fan_max_pct": 75.0, "fan_active_cnt": 2.0, "pump_max_pct": 85.0, "pump_run_s": 75.0, "pump_interval_min": 15.0, "soil_min_pct": 45.0, "soil_hi_pct": 88.0},
    "Herbst": {"temp_max_c": 23.0, "winter_temp_c": 10.0, "dehum_start_pct": 78.0, "dehum_stop_pct": 68.0, "fan_max_pct": 95.0, "fan_active_cnt": 3.0, "pump_max_pct": 65.0, "pump_run_s": 45.0, "pump_interval_min": 30.0, "soil_min_pct": 38.0, "soil_hi_pct": 88.0},
    "Winter": {"temp_max_c": 22.0, "winter_temp_c": 10.0, "dehum_start_pct": 82.0, "dehum_stop_pct": 72.0, "fan_max_pct": 55.0, "fan_active_cnt": 1.0, "pump_max_pct": 50.0, "pump_run_s": 30.0, "pump_interval_min": 45.0, "soil_min_pct": 32.0, "soil_hi_pct": 85.0},
}
DEFAULT_SETTINGS = dict(SEASON_DEFAULTS["Frühling"])


def apply_runtime_settings(cfg, data: dict, ctrl=None):
    """Überträgt gespeicherte Laufzeit- und Leistungs-Einstellungen auf Konfiguration und Regler."""
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: float(v) for k, v in data.items() if isinstance(v, (int, float))})
    new_cfg = replace(
        cfg,
        temp_max_c=float(merged["temp_max_c"]),
        soil_min_pct=float(merged["soil_min_pct"]),
        pump_run_s=float(merged["pump_run_s"]),
        pump_soak_s=float(merged["pump_interval_min"]) * 60.0,
        pump_max_pwm=max(0.3, min(1.0, float(merged["pump_max_pct"]) / 100.0)),
    )
    if ctrl is not None:
        ctrl.cfg = new_cfg
        if hasattr(ctrl, "pump"):
            ctrl.pump.max_level = min(new_cfg.pump_limit, new_cfg.pump_max_pwm)
    if tk._default_root is not None:
        tk._default_root._runtime_settings = dict(merged)
        ff = getattr(tk._default_root, "_fan_faults", set())
        want = max(0, min(3, int(round(float(merged.get("fan_active_cnt", 3.0))))))
        healthy = tuple(i for i in (2, 0, 1) if i not in ff)
        active_ids = set(healthy.__getitem__(slice(0, want)))
        tk._default_root._fan_parked = set(i for i in healthy if i not in active_ids)
        cur_lvl = float(ctrl.fan.level) if (ctrl is not None and hasattr(ctrl, "fan")) else 0.0
        tk._default_root._fan_levels = tuple((cur_lvl if i in active_ids else 0.0) for i in range(3))
    return new_cfg


def load_runtime_settings(base_dir: str, cfg) -> dict:
    """Lädt gespeicherte Jahreszeit- und Leistungsprofile aus settings.json."""
    path = os.path.join(base_dir, "settings.json")
    profiles = {s: dict(vals) for s, vals in SEASON_DEFAULTS.items()}
    active_season = "Frühling"
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and "profiles" in raw:
                if raw.get("active_season") in profiles:
                    active_season = raw["active_season"]
                for s_name, p_vals in raw.get("profiles", {}).items():
                    if s_name in profiles and isinstance(p_vals, dict):
                        for k in DEFAULT_SETTINGS:
                            if k in p_vals:
                                profiles[s_name][k] = float(p_vals[k])
        except Exception:
            pass
    data = dict(profiles[active_season])
    apply_runtime_settings(cfg, data, None)
    return data


class SettingsPage(tk.Frame):
    """Touch-Einstellungsmenü mit Jahreszeit-Profilen für Lüfter, Pumpe und Klima (800x480)."""

    ITEMS = [
        ("fan_max_pct", "Lüfter-Leistung", "%", 5.0, 30.0, 100.0, "{:.0f}"),
        ("fan_active_cnt", "Aktive Lüfter", "Stk", 1.0, 0.0, 3.0, "{:.0f}"),
        ("temp_max_c", "Lüfter-Start Temp.", "°C", 0.5, 18.0, 35.0, "{:.1f}"),
        ("dehum_start_pct", "Entfeuchten ab", "%", 1.0, 62.0, 92.0, "{:.0f}"),
        ("dehum_stop_pct", "Entfeuchten bis", "%", 1.0, 50.0, 82.0, "{:.0f}"),
        ("winter_temp_c", "Winterschutz", "°C", 0.5, 4.0, 15.0, "{:.1f}"),
        ("pump_max_pct", "Pumpen-Durchsatz", "%", 5.0, 30.0, 100.0, "{:.0f}"),
        ("pump_run_s", "Pumpen-Laufzeit", "s", 5.0, 10.0, 180.0, "{:.0f}"),
        ("pump_interval_min", "Gieß-Intervall", "min", 5.0, 5.0, 90.0, "{:.0f}"),
    ]

    def __init__(self, master, app, cfg, base_dir: str):
        super().__init__(master, bg=BG)
        self.app, self.cfg = app, cfg
        self.settings_path = os.path.join(base_dir, "settings.json")
        self.profiles = {s: dict(vals) for s, vals in SEASON_DEFAULTS.items()}
        self.active_season = "Frühling"
        self._load_all()
        self.data = self.profiles[self.active_season]
        self.cfg = apply_runtime_settings(self.cfg, self.data, self.app.ctrl)
        self.val_labels: dict[str, tk.Label] = {}
        self.season_btns: dict[str, tk.Button] = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = tk.Frame(self, bg=BG)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 3))
        top.grid_columnconfigure(0, weight=1)
        self.info_lbl = tk.Label(top, text=f"Profil aktiv: {self.active_season} (Live & gespeichert)", bg=CARD, fg=TEXT, font=(FONT, 10, "bold"), anchor="w", padx=10, pady=7)
        self.info_lbl.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        tk.Button(top, text="Profil-Reset", command=self._reset_season_defaults, bg="#e6e1cf", fg=TEXT, bd=0, relief="flat", padx=10, pady=7, font=(FONT, 9, "bold"), cursor="hand2").grid(row=0, column=1, padx=(0, 6))
        tk.Button(top, text="✕ Schließen", command=self.app.close_settings, bg=HEADER, fg="white", bd=0, relief="flat", padx=12, pady=7, font=(FONT, 10, "bold"), cursor="hand2").grid(row=0, column=2)

        s_bar = tk.Frame(self, bg=BG)
        s_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 3))
        for idx_s, s_name in enumerate(SEASON_ORDER):
            s_bar.grid_columnconfigure(idx_s, weight=1)
            sb = tk.Button(s_bar, text=f"Profil: {s_name}", command=lambda s=s_name: self.select_season(s), bg=ACCENT if s_name == self.active_season else "#d8d3c0", fg="white" if s_name == self.active_season else TEXT, bd=0, relief="flat", pady=6, font=(FONT, 9, "bold"), cursor="hand2")
            sb.grid(row=0, column=idx_s, sticky="ew", padx=3)
            self.season_btns[s_name] = sb

        grid = tk.Frame(self, bg=BG)
        grid.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 6))
        for c in range(3):
            grid.grid_columnconfigure(c, weight=1, uniform="sc")
            grid.grid_rowconfigure(c, weight=1, uniform="sr")
        for idx, (key, title, unit, step, vmin, vmax, fmt) in enumerate(self.ITEMS):
            r, c = divmod(idx, 3)
            card = tk.Frame(grid, bg=CARD, padx=6, pady=4, highlightthickness=1, highlightbackground="#e0dbc8")
            card.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
            card.grid_columnconfigure(1, weight=1)
            card.grid_rowconfigure(1, weight=1)
            tk.Label(card, text=title, bg=CARD, fg=MUTED, font=(FONT, 9, "bold")).grid(row=0, column=0, columnspan=3, pady=(1, 2))
            tk.Button(card, text="−", width=3, bg="#e6e1cf", fg=TEXT, bd=0, relief="flat", font=(FONT, 14, "bold"), cursor="hand2", repeatdelay=350, repeatinterval=90, command=lambda k=key, st=-step, lo=vmin, hi=vmax: self._adjust(k, st, lo, hi)).grid(row=1, column=0, sticky="ns", pady=2)
            lbl = tk.Label(card, text=f"{fmt.format(self.data[key])} {unit}", bg=CARD, fg=GOOD, font=(FONT, 13, "bold"))
            lbl.grid(row=1, column=1, sticky="nsew", padx=2)
            self.val_labels[key] = lbl
            tk.Button(card, text="+", width=3, bg="#e6e1cf", fg=TEXT, bd=0, relief="flat", font=(FONT, 14, "bold"), cursor="hand2", repeatdelay=350, repeatinterval=90, command=lambda k=key, st=step, lo=vmin, hi=vmax: self._adjust(k, st, lo, hi)).grid(row=1, column=2, sticky="ns", pady=2)

    def _load_all(self) -> None:
        if not os.path.exists(self.settings_path):
            return
        try:
            raw = json.load(open(self.settings_path, encoding="utf-8"))
            if isinstance(raw, dict) and "profiles" in raw:
                if raw.get("active_season") in self.profiles:
                    self.active_season = raw.get("active_season")
                for s_name, p_vals in raw.get("profiles", {}).items():
                    if s_name in self.profiles and isinstance(p_vals, dict):
                        for k in DEFAULT_SETTINGS:
                            if k in p_vals:
                                self.profiles[s_name][k] = float(p_vals[k])
        except Exception:
            pass

    def select_season(self, season: str) -> None:
        """Aktiviert das gewünschte Jahreszeit-Profil und wendet alle Werte sofort an."""
        if season not in self.profiles:
            return
        self.active_season = season
        self.data = self.profiles[season]
        for s_name, btn in self.season_btns.items():
            btn.config(bg=ACCENT if s_name == season else "#d8d3c0", fg="white" if s_name == season else TEXT)
        self._save_and_apply()
        self._refresh_labels()
        self.info_lbl.config(text=f"Profil '{season}' aktiv & gespeichert", fg=GOOD)

    def _adjust(self, key: str, delta: float, vmin: float, vmax: float) -> None:
        val = round(max(vmin, min(vmax, self.data[key] + delta)), 2)
        if key == "dehum_stop_pct" and val >= self.data["dehum_start_pct"] - 2.0:
            val = max(vmin, self.data["dehum_start_pct"] - 2.0)
        if key == "dehum_start_pct" and val <= self.data["dehum_stop_pct"] + 2.0:
            val = min(vmax, self.data["dehum_stop_pct"] + 2.0)
        self.data[key] = val
        self._save_and_apply()
        self._refresh_labels()
        self.info_lbl.config(text=f"{self.active_season}: Wert aktualisiert & gespeichert", fg=GOOD)

    def _reset_season_defaults(self) -> None:
        self.profiles[self.active_season] = dict(SEASON_DEFAULTS[self.active_season])
        self.data = self.profiles[self.active_season]
        self._save_and_apply()
        self._refresh_labels()
        self.info_lbl.config(text=f"Standardwerte für '{self.active_season}' wiederhergestellt", fg=GOOD)

    def _save_and_apply(self) -> None:
        self.cfg = apply_runtime_settings(self.cfg, self.data, self.app.ctrl)
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump({"active_season": self.active_season, "profiles": self.profiles}, f, indent=2)
        except OSError:
            pass

    def _refresh_labels(self) -> None:
        for key, _title, unit, _step, _vmin, _vmax, fmt in self.ITEMS:
            if key in self.val_labels:
                self.val_labels[key].config(text=f"{fmt.format(self.data[key])} {unit}")

    def refresh(self, r=None, pr=None) -> None:
        """Aktualisiert die Statuszeile der Einstellungsseite mit aktuellen Live-Werten."""
        if r is not None and hasattr(self, "info_lbl"):
            self.info_lbl.config(
                text=f"Profil: {self.active_season} · Zelt {r.temperature_c:.1f} °C · {r.humidity_pct:.0f} % Feuchte"
            )

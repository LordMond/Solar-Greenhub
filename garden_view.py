#!/usr/bin/env python3
"""Animierte 4-Jahreszeiten- & Tag-/Nacht-Gartenwelt (Gartenhütte, Foliengewächshaus, Mondphase, Reh & Monti)."""

from __future__ import annotations

import math
import time
import tkinter as tk
from siegen_wetter import calc_moon_phase

FONT = "DejaVu Sans"
GOOD = "#2f7a3e"
WARN = "#d9822b"
BAD = "#c93b2b"


def _round_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class GardenSceneView(tk.Canvas):
    """Animierte 4-Jahreszeiten- & Tag-/Nacht-Gartenszene (auch als 5-Min-Screensaver)."""

    def __init__(self, master, on_close):
        super().__init__(master, bg="#162219", highlightthickness=0)
        self.on_close = on_close
        self.r = self.pr = self.ctrl = self.manager = self.env = None
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._on_click)

    def _on_click(self, e) -> None:
        self.on_close()

    def update_data(self, r, pr, ctrl, manager, env=None, weather=None) -> None:
        """Übernimmt Sensor-, Leistungs-, Steuerungs-, Umgebungs- und Live-Wetterdaten für die Szene."""
        self.r, self.pr, self.ctrl, self.manager, self.env, self.weather = r, pr, ctrl, manager, env, weather

    def step_anim(self) -> None:
        """Aktualisiert die laufende Garten-Animation um einen Takt."""
        self.draw()

    def _current_hour(self, env) -> float:
        if env and not getattr(env, "live_siegen", False):
            mh = getattr(env, "manual_hour", None)
            if mh is not None:
                return float(mh) % 24.0
            sh = getattr(env, "sim_hour", None)
            if sh is not None:
                return float(sh) % 24.0
            return (time.monotonic() * (24.0 / 600.0) + 8.0) % 24.0
        lt = time.localtime()
        return lt.tm_hour + lt.tm_min / 60.0 + lt.tm_sec / 3600.0

    def draw(self) -> None:
        """Zeichnet Himmel, Wetter, Berge, Bäume, Hütte, Gewächshaus, Reh und Monti neu."""
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 200 or h < 150:
            return
        r, pr, ctrl, env = self.r, self.pr, self.ctrl, self.env
        now = time.monotonic()
        hour_f = self._current_hour(env)
        w_obj = getattr(self, "weather", None)
        is_live = bool(env and getattr(env, "live_siegen", False))
        is_dst = time.localtime().tm_isdst > 0
        out_tmp = float(getattr(env, "outside_temp_base_c", 18.0)) if env else 18.0
        if is_live and w_obj is not None:
            sr = float(getattr(w_obj, "sunrise_hour", 7.8 if not is_dst else 5.8))
            ss = float(getattr(w_obj, "sunset_hour", 16.7 if not is_dst else 21.2))
        elif out_tmp <= 2.5 or (not is_dst and is_live):
            sr, ss = 8.0, 16.75
        elif out_tmp <= 14.5:
            sr, ss = (7.2, 18.2) if not is_dst else (6.8, 19.5)
        else:
            sr, ss = (5.4, 21.4) if is_dst else (6.2, 20.2)
        dawn_start, dawn_end = sr - 0.55, sr + 0.75
        dusk_start, dusk_end = ss - 0.75, ss + 0.55
        is_night = hour_f < dawn_start or hour_f >= dusk_end
        is_dawn = dawn_start <= hour_f < dawn_end
        is_dusk = dusk_start <= hour_f < dusk_end

        solar_w = pr.solar_power_w if pr else 0.0
        bat_soc = pr.battery_soc_pct if pr else 70.0
        bat_t = pr.battery_temp_c if pr else 15.0
        temp_c = r.temperature_c if r else 18.0
        hum_pct = r.humidity_pct if r else 65.0
        soil_pct = r.soil_moisture_pct if r else 55.0
        fan_lvl = ctrl.fan.level if ctrl else 0.0
        pump_lvl = ctrl.pump.level if ctrl else 0.0
        manual = bool(getattr(ctrl, "manual", False))

        out_t = float(getattr(env, "outside_temp_base_c", temp_c)) if env else temp_c
        clouds = 95.0 if (env and getattr(env, "rain_sim", False)) else (
            float(getattr(env, "cloud_cover_pct", 30.0)) if env else 30.0
        )
        m_phase, m_name, m_illum = calc_moon_phase()

        # Jahreszeit bestimmen
        m_now = int(time.strftime("%m"))
        if out_t <= 2.5 or (getattr(env, "live_siegen", False) and m_now in (12, 1, 2) and out_t < 7.0):
            season, hill_col, lawn_col = "winter", "#5a6875", "#dce8f2"
        elif out_t <= 14.5 or (getattr(env, "live_siegen", False) and m_now in (9, 10, 11) and out_t < 18.0):
            season, hill_col, lawn_col = "autumn", "#7d4f28", "#6b6e34"
        elif out_t <= 21.5:
            season, hill_col, lawn_col = "spring", "#466936", "#58873e"
        else:
            season, hill_col, lawn_col = "summer", "#335429", "#467532"

        if is_night:
            hill_col = "#1c261e"
            lawn_col = "#4a5c6b" if season == "winter" else "#233820"

        raw_scen = str(getattr(env, "weather_scenario", "auto")).lower() if env is not None else "auto"
        wcode_live = int(getattr(w_obj, "weather_code", 1)) if (is_live and w_obj is not None) else 1
        wind_pre = float(getattr(env, "wind_kmh", 0.0)) if env is not None else 0.0
        if raw_scen in ("gewitter", "nebel", "sturm", "regen", "klar"):
            scen = raw_scen
        elif wcode_live in (95, 96, 99):
            scen = "gewitter"
        elif wcode_live in (45, 48):
            scen = "nebel"
        elif wind_pre >= 38.0:
            scen = "sturm"
        elif (env and getattr(env, "rain_sim", False)) or (env and float(getattr(env, "precipitation_mm", 0.0)) > 0.05) or (51 <= wcode_live <= 86):
            scen = "regen"
        else:
            scen = "klar"
        bad_weather = scen in ("gewitter", "nebel", "sturm", "regen")

        # Blitz-Impuls bei Gewitter (zuckt alle ~3.8 Sekunden kurz auf)
        lightning_flash = (scen == "gewitter") and ((now % 3.8) < 0.22)

        # Himmel nach Tageszeit, Bewölkung & Wetterszenario
        if lightning_flash:
            sky_col = "#5a6d8a"
        elif scen == "gewitter":
            sky_col = "#141b26" if is_night else "#253042"
        elif scen == "nebel":
            sky_col = "#28323d" if is_night else "#8b98a3"
        elif is_night:
            sky_col = "#0d1626"
        elif is_dawn:
            sky_col = "#b86b5e" if clouds < 70 else "#4f5566"
        elif is_dusk:
            sky_col = "#8c4a5e" if clouds < 70 else "#434a5c"
        elif clouds >= 75.0 or scen == "sturm":
            sky_col = "#425363"
        elif clouds >= 35.0:
            sky_col = "#739ec4"
        else:
            sky_col = "#5ea8dc"
        self.create_rectangle(0, 0, w, h * 0.44, fill=sky_col, outline="")

        # Zickzack-Blitz bei Gewitter
        if lightning_flash:
            lx = w * (0.28 + 0.42 * abs(math.sin(int(now))))
            self.create_line(lx, 36, lx - 14, 78, lx + 8, 84, lx - 18, 134,
                             fill="#fff7a8", width=3.2, capstyle="round", joinstyle="round")
            self.create_line(lx + 8, 84, lx + 24, 118, fill="#ffe066", width=1.8)

        # Sonne am Tagbogen oder Sterne + echte Mondphase in der Nacht
        if not is_night and clouds < 80.0:
            day_prog = max(0.0, min(1.0, (hour_f - dawn_start) / max(1.0, dusk_end - dawn_start)))
            sx = 60 + day_prog * (w - 140)
            sy = 95 - math.sin(day_prog * math.pi) * 65
            scol = "#ff9e5e" if (is_dawn or is_dusk) else "#ffe066"
            self.create_oval(sx - 18, sy - 18, sx + 18, sy + 18, fill=scol, outline="#ffd166", width=2)
        elif is_night:
            if clouds < 65.0:
                for si, (stx, sty) in enumerate(((0.08, 0.08), (0.16, 0.16), (0.24, 0.06), (0.33, 0.14),
                                                 (0.45, 0.07), (0.58, 0.15), (0.69, 0.06), (0.78, 0.13), (0.88, 0.09))):
                    tw = 1.2 + abs(math.sin(now * 2.5 + si)) * 1.4
                    self.create_oval(w * stx - tw, h * sty - tw, w * stx + tw, h * sty + tw, fill="#fff9d6", outline="")
            if clouds < 80.0:
                mx, my, mr = w * 0.74, 46, 17
                self.create_oval(mx - mr, my - mr, mx + mr, my + mr, fill="#fff3b0", outline="#ffe066")
                if m_illum < 92:
                    sh_off = (0.5 - m_phase) * 2.0 * mr * 1.3
                    self.create_oval(mx - mr + sh_off, my - mr, mx + mr + sh_off, my + mr, fill=sky_col, outline="")

        wind_kmh = float(getattr(env, "wind_kmh", 0.0)) if env is not None else 0.0
        if scen in ("sturm", "gewitter"):
            wind_kmh = max(wind_kmh, 52.0 if scen == "sturm" else 44.0)
        elif scen == "nebel":
            wind_kmh = min(wind_kmh if wind_kmh > 0 else 5.0, 6.0)
        elif wind_kmh <= 0.1:
            wind_kmh = 8.0 + (clouds / 100.0) * 34.0 + (12.0 if (env and getattr(env, "rain_sim", False)) else 0.0)

        # Wind-abhängige Wolken (ziehen bei starkem Wind deutlich schneller & gestreckter)
        if clouds >= 25.0:
            w_speed = 6.0 + wind_kmh * 1.45
            stretch = min(22.0, wind_kmh * 0.42)
            for ci in range(4):
                cx = ((now * (w_speed + ci * 5.0) + ci * 210) % (w + 180)) - 90
                cy = 28 + ci * 13
                ccol = "#2a3545" if is_night else ("#7b8a99" if clouds >= 75 else "#eef4f8")
                self.create_oval(cx - 32 - stretch, cy - 11, cx + 32 + stretch, cy + 11, fill=ccol, outline="")
                self.create_oval(cx - 14 - stretch * 0.5, cy - 18, cx + 24 + stretch * 0.5, cy + 7, fill=ccol, outline="")

        # Sichtbare Windböen-Streifen bei kräftigem Wind (>= 22 km/h)
        if wind_kmh >= 22.0:
            gust_cnt = 5 if wind_kmh >= 34.0 else 3
            g_col = "#7b93ab" if is_night else "#d8e6f2"
            for gi in range(gust_cnt):
                gx = ((now * (wind_kmh * 4.2 + gi * 18) + gi * 195) % (w + 140)) - 70
                gy = h * (0.14 + gi * 0.08)
                self.create_line(gx, gy, gx + 48 + wind_kmh * 0.6, gy - 2, fill=g_col, width=1.6, capstyle="round")

        # Gestaffelte Berge im Hintergrund (im Winter mit weißen Schneegipfeln)
        m_far = "#263242" if is_night else ("#58697a" if season == "winter" else "#485e4c")
        self.create_polygon(0, h * 0.28, w * 0.18, h * 0.10, w * 0.36, h * 0.24, w * 0.56, h * 0.08,
                            w * 0.76, h * 0.23, w * 0.91, h * 0.11, w, h * 0.22, w, h * 0.46, 0, h * 0.46,
                            fill=m_far, outline="")
        if season == "winter":
            for pkx, pky in ((w * 0.18, h * 0.10), (w * 0.56, h * 0.08), (w * 0.91, h * 0.11)):
                self.create_polygon(pkx, pky, pkx - 28, pky + 24, pkx + 28, pky + 24, fill="#eef5fa", outline="")

        self.create_polygon(0, h * 0.26, w * 0.35, h * 0.19, w * 0.72, h * 0.22, w, h * 0.17,
                            w, h * 0.46, 0, h * 0.46, fill=hill_col, outline="")

        # Bäume an den Bergen, die sich im Wind neigen und wiegen
        tree_cols = {
            "spring": ("#2f6b32", "#448a3e"),
            "summer": ("#1f5425", "#327333"),
            "autumn": ("#8f451d", "#c46d23"),
            "winter": ("#2f4a3c", "#dce8f2"),
        }
        c_pine, c_leaf = tree_cols[season]
        for ti, (tx_r, ty_r, is_pine) in enumerate((
            (0.06, 0.25, True), (0.14, 0.23, False), (0.24, 0.21, True),
            (0.47, 0.21, True), (0.63, 0.22, False), (0.83, 0.20, True), (0.95, 0.19, False),
        )):
            bx, by = w * tx_r, h * ty_r
            sway = math.sin(now * (1.7 + wind_kmh * 0.07) + ti * 1.1) * min(14.0, wind_kmh * 0.28) + min(7.0, wind_kmh * 0.12)
            # Stamm
            self.create_line(bx, by, bx + sway * 0.35, by - 22, fill="#3b2618", width=3.5, capstyle="round")
            if is_pine:
                self.create_polygon(bx - 13 + sway * 0.4, by - 14, bx + sway, by - 46, bx + 13 + sway * 0.4, by - 14,
                                    fill="#1b2b24" if is_night else c_pine, outline="")
                self.create_polygon(bx - 10 + sway * 0.6, by - 26, bx + sway * 1.15, by - 56, bx + 10 + sway * 0.6, by - 26,
                                    fill="#22362d" if is_night else c_pine, outline="")
            else:
                self.create_oval(bx - 15 + sway, by - 48, bx + 15 + sway, by - 18,
                                 fill="#223326" if is_night else c_leaf, outline="")
        self.create_rectangle(0, h * 0.44, w, h, fill=lawn_col, outline="")
        self.create_polygon(w * 0.30, h * 0.56, w * 0.39, h * 0.56, w * 0.64, h, w * 0.46, h,
                            fill="#434a52" if is_night else ("#8a96a3" if season == "winter" else "#6e736b"), outline="#3a3f38")

        ffaults = getattr(tk._default_root, "_fan_faults", set()) if tk._default_root is not None else set()
        s_err = bool(getattr(tk._default_root, "_app_ctrl_error", False)) if tk._default_root is not None else False
        p_fault = bool(getattr(ctrl, "pump_fault", None) or s_err)
        rt = getattr(tk._default_root, "_runtime_settings", {}) if tk._default_root is not None else {}
        w_lim = float(rt.get("winter_temp_c", 10.0))
        w_lock = bool(not manual and temp_c < w_lim)
        fan_levels = getattr(tk._default_root, "_fan_levels", None) if tk._default_root is not None else None
        if not fan_levels or len(fan_levels) != 3:
            fan_levels = [0.0 if (s_err or i in ffaults) else fan_lvl for i in range(3)]

        hx1, hy1, hx2, hy2 = 36, h * 0.30, w * 0.41, h * 0.67
        hmid = (hx1 + hx2) / 2

        # 1. Reh im Hintergrund durch den Wald laufen lassen (eigener 28-Sekunden-Rhythmus)
        forest_t = now % 28.0
        if (not is_night) and (not bad_weather) and 3.0 <= forest_t <= 15.0:
            f_prog = (forest_t - 3.0) / 12.0
            f_dir = 1 if (int(now // 28.0) % 2 == 0) else -1
            fx_d = (w * 0.08 + f_prog * w * 0.84) if f_dir == 1 else (w * 0.92 - f_prog * w * 0.84)
            fy_d = h * 0.31 - abs(math.sin(now * 6.5)) * 3.0
            for lx_off in (-8, -3, 4, 9):
                self.create_line(fx_d + lx_off, fy_d + 4, fx_d + lx_off + math.sin(now * 8 + lx_off) * 2, fy_d + 15,
                                 fill="#7d5230", width=2)
            self.create_oval(fx_d - 13, fy_d - 6, fx_d + 13, fy_d + 6, fill="#a87344", outline="#6e4726")
            self.create_oval(fx_d - 14 * f_dir - 2, fy_d - 4, fx_d - 14 * f_dir + 2, fy_d + 1, fill="#f5ede3", outline="")
            hx_d, hy_d = fx_d + 12 * f_dir, fy_d - 9
            self.create_line(fx_d + 8 * f_dir, fy_d - 3, hx_d, hy_d, fill="#a87344", width=4)
            self.create_oval(hx_d - 5, hy_d - 5, hx_d + 5, hy_d + 4, fill="#b88050", outline="#6e4726")
            self.create_polygon(hx_d - 2 * f_dir, hy_d - 4, hx_d - 5 * f_dir, hy_d - 11, hx_d + 1 * f_dir, hy_d - 5,
                                fill="#a67246", outline="")

        # 2. Reh schaut abwechselnd LINKS oder RECHTS hinter der Gartenhuette hervor
        deer_cycle = now % 14.0
        deer_side = -1 if (int(now // 14.0) % 2 == 0) else 1
        deer_visible = bool((not is_night) and (not bad_weather) and 2.0 <= deer_cycle <= 8.5)
        if deer_visible:
            peek = math.sin(((deer_cycle - 2.0) / 6.5) * math.pi)
            rx = (hx1 + 14 - peek * 24) if deer_side == -1 else (hx2 - 14 + peek * 24)
            ry = hy1 + 78
            s = deer_side
            self.create_polygon(rx + 4 * s, ry - 9, rx + 13 * s, ry - 21, rx + 1 * s, ry - 14, fill="#a67246", outline="#6e4726")
            self.create_polygon(rx - 2 * s, ry - 10, rx + 2 * s, ry - 23, rx - 7 * s, ry - 14, fill="#a67246", outline="#6e4726")
            self.create_oval(rx - 11, ry - 10, rx + 11, ry + 11, fill="#b88050", outline="#6e4726", width=1.2)
            self.create_oval(rx + 5 * s, ry - 1, rx + 15 * s, ry + 8, fill="#d9b38c", outline="")
            self.create_oval(rx + 11 * s, ry + 1, rx + 15 * s, ry + 5, fill="#1c1612", outline="")
            self.create_oval(rx + 1 * s, ry - 5, rx + 6 * s, ry, fill="#1a1410", outline="")
            self.create_oval(rx + 3.5 * s, ry - 4, rx + 5 * s, ry - 2.5, fill="white", outline="")

        self.create_rectangle(hx1 + 14, hy1 + 38, hx2 - 14, hy2, fill="#5c3d28", outline="#3d2718", width=2)
        self.create_rectangle(hx1 + 26, hy1 + 48, hx2 - 26, hy2 - 4, fill="#5e4624" if is_night else "#2e2118", outline="#4a3120")
        roof_col = "#eef5fa" if season == "winter" else "#3b281c"
        self.create_polygon(hx1, hy1 + 42, hmid, hy1, hx2, hy1 + 42, fill=roof_col, outline="#261911", width=2)
        self.create_polygon(hx1 + 24, hy1 + 35, hmid - 6, hy1 + 10, hmid - 6, hy1 + 34, hx1 + 24, hy1 + 42,
                            fill="#1b4268", outline="#7cc4f0", width=1.5)
        self.create_polygon(hmid + 6, hy1 + 10, hx2 - 24, hy1 + 35, hx2 - 24, hy1 + 42, hmid + 6, hy1 + 34,
                            fill="#1b4268", outline="#7cc4f0", width=1.5)
        _round_rect(self, hmid - 58, hy1 - 24, hmid + 58, hy1 - 2, 7, fill="#1f2d22", outline="#ffd166", width=1.5)
        self.create_text(hmid, hy1 - 13, text=f"PV-DACH: {solar_w:.0f} W", fill="#ffd166", font=(FONT, 8, "bold"))

        # Kleine bunte Voegel: fliegen weite Boegen ueber den Garten & landen auf wechselnden Dachpunkten
        if (not is_night) and (not bad_weather) and clouds < 50.0:
            def _roof_spot(bird_i, trip_n):
                seed_v = (bird_i * 37 + trip_n * 73 + 19) % 100
                u = 0.08 + (seed_v / 100.0) * 0.84
                rx_p = hx1 + 14 + u * (hx2 - hx1 - 28)
                if rx_p <= hmid:
                    ry_p = hy1 + 40 - ((rx_p - hx1) / max(1.0, hmid - hx1)) * 40 - 3
                else:
                    ry_p = hy1 + ((rx_p - hmid) / max(1.0, hx2 - hmid)) * 40 - 3
                return rx_p, ry_p

            palette = (("#e76f51", "#f4a261"), ("#3a86ff", "#ffd166"), ("#8d6e63", "#d7ccc8"))
            for bi in range(3):
                b_body, b_breast = palette.__getitem__(bi)
                cyc_l = 13.0 + bi * 2.5
                t_local = (now + bi * 3.7) % cyc_l
                trip_idx = int((now + bi * 3.7) // cyc_l)
                sx_r, sy_r = _roof_spot(bi, trip_idx)
                ex_r, ey_r = _roof_spot(bi, trip_idx + 1)
                if t_local < 4.4:
                    # Weiter Rundflug ueber Himmel, Baeume & Garten zum neuen Landeplatz
                    p = t_local / 4.4
                    arc = math.sin(p * math.pi)
                    sweep = math.sin(p * 2.0 * math.pi) * (w * (0.22 if bi % 2 == 0 else -0.20))
                    vx = sx_r + (ex_r - sx_r) * p + sweep
                    vy = sy_r + (ey_r - sy_r) * p - arc * (h * 0.24)
                    wing = math.sin(now * 24.0) * 6.5
                    self.create_oval(vx - 5, vy - 3, vx + 5, vy + 3, fill=b_body, outline="")
                    self.create_line(vx - 7, vy - wing, vx, vy, vx + 7, vy - wing, fill=b_breast, width=2)
                else:
                    # Sitzt auf dem neuen Dachpunkt & pickt ab und zu
                    peck = 2.0 if (int(now * 3 + bi) % 7 == 0) else 0.0
                    self.create_oval(ex_r - 4, ey_r - 5 + peck, ex_r + 4, ey_r + 1 + peck, fill=b_body, outline="")
                    self.create_oval(ex_r + 2, ey_r - 7 + peck, ex_r + 6, ey_r - 3 + peck, fill=b_breast, outline="")
                    self.create_polygon(ex_r + 6, ey_r - 5 + peck, ex_r + 9, ey_r - 4 + peck, ex_r + 6, ey_r - 3 + peck, fill="#ffb703", outline="")
        bx1, by1, bx2, by2 = hx1 + 34, hy1 + 60, hx1 + 112, hy2 - 14
        b_col = BAD if bat_t < 0.0 else (WARN if bat_soc < 25.0 else GOOD)
        _round_rect(self, bx1, by1, bx2, by2, 6, fill="#1d2820", outline=b_col, width=2)
        fill_h = max(4, (by2 - by1 - 22) * (bat_soc / 100.0))
        self.create_rectangle(bx1 + 6, by2 - 6 - fill_h, bx2 - 6, by2 - 6, fill=b_col, outline="")
        self.create_text((bx1 + bx2) / 2, by1 + 11, text=f"48V {bat_soc:.0f}%", fill="white", font=(FONT, 8, "bold"))

        tank_l = float(getattr(env, "tank_liters", 165.0)) if env is not None else 165.0
        tank_pct = max(0.0, min(100.0, (tank_l / 220.0) * 100.0))
        tx1, ty1, tx2, ty2 = hx2 - 108, hy1 + 60, hx2 - 34, hy2 - 14
        p_col = BAD if (p_fault or tank_l < 10.0) else (WARN if manual else ("#5ec2e8" if pump_lvl > 0 else ("#4b7a9c" if w_lock else GOOD)))
        _round_rect(self, tx1, ty1, tx2, ty2, 6, fill="#1b2b34", outline=p_col, width=2)
        w_h = max(3, (ty2 - ty1 - 22) * (tank_pct / 100.0))
        w_fill = "#c93b2b" if tank_l < 10.0 else ("#d9822b" if tank_l < 35.0 else "#2e7ca8")
        self.create_rectangle(tx1 + 5, ty2 - 5 - w_h, tx2 - 5, ty2 - 5, fill=w_fill, outline="")
        self.create_text((tx1 + tx2) / 2, ty1 + 11, text=f"TANK {tank_l:.0f} L", fill="white", font=(FONT, 7, "bold"))
        p_txt = "LEER!" if tank_l < 10.0 else ("GESPERRT" if p_fault else (f"{round(pump_lvl*100)}%" if pump_lvl > 0 else ("WINTER" if w_lock else "BEREIT")))
        self.create_text((tx1 + tx2) / 2, (ty1 + ty2) / 2 + 6, text=p_txt, fill="white", font=(FONT, 8, "bold"))
        pipe_y = hy2 + 18
        gx1, gy1, gx2, gy2 = w * 0.49, h * 0.27, w - 34, h * 0.78
        self.create_line((tx1 + tx2) / 2, ty2, (tx1 + tx2) / 2, pipe_y, gx1 + 36, pipe_y, gx1 + 36, gy2 - 18,
                         fill="#3a6b88", width=5, joinstyle="round")
        if pump_lvl > 0:
            for di in range(6):
                off = ((now * 90 + di * 45) % 270) / 270.0
                dx = (tx1 + tx2) / 2 + off * ((gx1 + 36) - (tx1 + tx2) / 2)
                self.create_oval(dx - 4, pipe_y - 4, dx + 4, pipe_y + 4, fill="#7cc4f0", outline="")

        gmid = (gx1 + gx2) / 2
        arch_h = 84
        gh_fill = "#7d7648" if is_night else "#4d8a68"
        gh_arch = "#a89d5e" if is_night else ("#d9ebf7" if season == "winter" else "#5da37b")
        self.create_polygon(gx1, gy1 + arch_h, gx1, gy2, gx2, gy2, gx2, gy1 + arch_h,
                            fill=gh_fill, outline="#79c298", width=2)
        self.create_arc(gx1, gy1, gx2, gy1 + arch_h * 2, start=0, extent=180,
                        fill=gh_arch, outline="#8fd9ae", width=2.5)
        for bi in range(1, 4):
            bx = gx1 + (gx2 - gx1) * (bi / 4.0)
            self.create_line(bx, gy1 + 14, bx, gy2, fill="#72b58e", width=1.5, dash=(4, 3))

        soil_col = "#3d2817" if soil_pct > 65 else ("#593b22" if soil_pct > 40 else "#7d5836")
        self.create_rectangle(gx1 + 14, gy2 - 44, gx2 - 14, gy2 - 8, fill=soil_col, outline="#2d1d10", width=1.5)
        self.create_line(gx1 + 22, gy2 - 52, gx2 - 22, gy2 - 52, fill="#2b6c94", width=3)
        for pi in range(5):
            px = gx1 + 36 + pi * ((gx2 - gx1 - 72) / 4.0)
            self.create_line(px, gy2 - 44, px, gy2 - 64, fill="#2f7a3e", width=3)
            self.create_oval(px - 10, gy2 - 70, px, gy2 - 58, fill="#49a85a", outline="")
            self.create_oval(px, gy2 - 72, px + 10, gy2 - 60, fill="#3b8f4b", outline="")
            if pump_lvl > 0:
                drop_y = gy2 - 50 + ((now * 35 + pi * 7) % 10)
                self.create_oval(px - 2, drop_y, px + 2, drop_y + 5, fill="#7cc4f0", outline="")

        fparked = getattr(tk._default_root, "_fan_parked", set()) if tk._default_root is not None else set()
        for li in range(3):
            fx = gx1 + (gx2 - gx1) * (0.24 + li * 0.26)
            fy = gy1 + 66
            fparked = getattr(tk._default_root, "_fan_parked", set()) if tk._default_root is not None else set()
            f_err = s_err or (li in ffaults)
            f_park = (not f_err) and (li in fparked)
            f_run = (not f_err) and (not f_park) and (fan_levels.__getitem__(li) > 0.01)
            f_col = BAD if f_err else ("#b388eb" if f_park else ("#2b7ca8" if f_run else ("#4b7a9c" if w_lock else GOOD)))
            self.create_oval(fx - 20, fy - 20, fx + 20, fy + 20, fill="#1d2b22", outline=f_col, width=2.5)
            rot = (now * (2.0 + fan_levels.__getitem__(li) * 15.0)) if f_run else 0.4
            for b in range(3):
                ang = rot + b * (2 * math.pi / 3)
                b_fill = "#b388eb" if f_park else ("#7cc4f0" if f_run else ("#e06c6c" if f_err else "#8fd9ae"))
                self.create_line(fx, fy, fx + 14 * math.cos(ang), fy + 14 * math.sin(ang), fill=b_fill, width=3, capstyle="round")
            lbl_f = f"L{li+1} PARK" if f_park else f"L{li+1}"
            self.create_text(fx, fy - 28, text=lbl_f, fill="#e0c3fc" if f_park else "white", font=(FONT, 7 if f_park else 8, "bold"))

        if season != "winter":
            f_cols = ["#ff6b8b", "#ffd166", "#ff9f43", "#a29bfe", "#ffffff", "#ff7675"]
            for fi, (fx_r, fy_r) in enumerate(((0.08, 0.74), (0.15, 0.90), (0.26, 0.77), (0.38, 0.92),
                                               (0.68, 0.86), (0.78, 0.91), (0.89, 0.84), (0.94, 0.92))):
                flx, fly = w * fx_r, h * fy_r
                self.create_line(flx, fly, flx, fly + 9, fill="#2d5e24", width=2)
                bc = f_cols[fi % len(f_cols)]
                for p_ang in range(5):
                    pa = p_ang * (2 * math.pi / 5)
                    self.create_oval(flx + 4 * math.cos(pa) - 3, fly + 4 * math.sin(pa) - 3,
                                     flx + 4 * math.cos(pa) + 3, fly + 4 * math.sin(pa) + 3, fill=bc, outline="")
                self.create_oval(flx - 2.5, fly - 2.5, flx + 2.5, fly + 2.5, fill="#ffeaa7", outline="")

        if (not is_night) and season in ("spring", "summer") and clouds < 75.0:
            flap = abs(math.sin(now * 14.0))
            for bi, bcol in enumerate(("#ff7eb3", "#70d6ff")):
                bx = w * (0.25 + bi * 0.45) + math.sin(now * (1.3 + bi * 0.4) + bi) * 55
                by = h * (0.52 + bi * 0.14) + math.cos(now * 2.1 + bi) * 18
                ws = 3 + flap * 6
                self.create_oval(bx - ws, by - 5, bx, by + 4, fill=bcol, outline="#2d2d2d")
                self.create_oval(bx, by - 5, bx + ws, by + 4, fill=bcol, outline="#2d2d2d")
                self.create_line(bx, by - 5, bx, by + 5, fill="#222222", width=1.5)
            beex = w * 0.56 + math.cos(now * 2.6) * 42
            beey = h * 0.64 + math.sin(now * 4.2) * 12
            self.create_oval(beex - 4, beey - 5 - flap * 3, beex + 1, beey, fill="#e8f4f8", outline="")
            self.create_oval(beex - 5, beey - 3, beex + 5, beey + 3, fill="#ffd166", outline="#222222")

        # Montis eigene kleine Hundehütte rechts neben der Gartenhütte
        khx, khy = hx2 + 24, hy2 + 6
        self.create_rectangle(khx - 22, khy - 24, khx + 22, khy, fill="#6e472b", outline="#3d2718", width=1.5)
        self.create_polygon(khx - 27, khy - 22, khx, khy - 42, khx + 27, khy - 22,
                            fill="#eef5fa" if season == "winter" else "#8c3b2b", outline="#2e1b10", width=1.5)
        self.create_oval(khx - 11, khy - 20, khx + 11, khy + 2, fill="#17120e", outline="#3d2718", width=1.2)
        self.create_text(khx, khy - 28, text="MONTI", fill="#ffeaa7", font=(FONT, 6, "bold"))
        wb_x, wb_y, fb_x, fb_y = khx - 14, khy + 10, khx + 10, khy + 10
        self.create_oval(wb_x - 7, wb_y - 3, wb_x + 7, wb_y + 4, fill="#2980b9", outline="#1a5276", width=1.2)
        self.create_oval(wb_x - 5, wb_y - 2, wb_x + 5, wb_y + 2, fill="#7cc4f0", outline="")
        self.create_oval(fb_x - 7, fb_y - 3, fb_x + 7, fb_y + 4, fill="#c0392b", outline="#78281f", width=1.2)
        self.create_oval(fb_x - 5, fb_y - 2, fb_x + 5, fb_y + 2, fill="#6e3b1e", outline="")
        holes = [h_i for h_i in getattr(self, "_dig_holes", []) if (now - h_i[2]) < 22.0]
        for hx_d, hy_d, t_dig in holes:
            f = 1.0 if (now - t_dig) < 14.0 else max(0.15, 1.0 - (now - t_dig - 14.0) / 8.0)
            self.create_oval(hx_d - 16*f, hy_d - 4*f, hx_d + 16*f, hy_d + 7*f, fill="#6e472b", outline="")
            self.create_oval(hx_d - 13*f, hy_d - 5.5*f, hx_d + 13*f, hy_d + 5.5*f, fill="#2b1a0e", outline="")
        self._dig_holes = holes
        wb_x, wb_y, fb_x, fb_y = khx - 14, khy + 10, khx + 10, khy + 10
        self.create_oval(wb_x - 7, wb_y - 3, wb_x + 7, wb_y + 4, fill="#2980b9", outline="#1a5276", width=1.2)
        self.create_oval(wb_x - 5, wb_y - 2, wb_x + 5, wb_y + 2, fill="#7cc4f0", outline="")
        self.create_oval(fb_x - 7, fb_y - 3, fb_x + 7, fb_y + 4, fill="#c0392b", outline="#78281f", width=1.2)
        self.create_oval(fb_x - 5, fb_y - 2, fb_x + 5, fb_y + 2, fill="#6e3b1e", outline="")
        holes = [h_i for h_i in getattr(self, "_dig_holes", []) if (now - h_i[2]) < 22.0]
        for hx_d, hy_d, t_dig in holes:
            f = 1.0 if (now - t_dig) < 14.0 else max(0.15, 1.0 - (now - t_dig - 14.0) / 8.0)
            self.create_oval(hx_d - 16*f, hy_d - 4*f, hx_d + 16*f, hy_d + 7*f, fill="#6e472b", outline="")
            self.create_oval(hx_d - 13*f, hy_d - 5.5*f, hx_d + 13*f, hy_d + 5.5*f, fill="#2b1a0e", outline="")
        self._dig_holes = holes

        rain_snow = scen in ("regen", "sturm", "gewitter", "nebel")
        if is_night or rain_snow:
            # Monti liegt in seiner Hütte & schaut nur mit seinem süßen Köpfchen aus dem Eingang!
            hx, hy = khx, khy - 9
            self.create_oval(hx - 9, hy - 9, hx + 9, hy + 8, fill="white", outline="#e6e6e6", width=1.2)
            self.create_oval(hx - 9, hy - 4, hx - 4, hy + 7, fill="#f2f2f2", outline="#dcdcdc")
            self.create_oval(hx + 4, hy - 4, hx + 9, hy + 7, fill="#f2f2f2", outline="#dcdcdc")
            self.create_oval(hx - 4, hy - 3, hx - 1, hy, fill="#1a1410", outline="")
            self.create_oval(hx + 1, hy - 3, hx + 4, hy, fill="#1a1410", outline="")
            self.create_oval(hx - 2, hy + 1, hx + 2, hy + 4.5, fill="#111111", outline="")
            m_lbl = "Monti (schläft)" if is_night else "Monti (im Trockenen)"
            self.create_text(khx, khy - 49, text=m_lbl, fill="white", font=(FONT, 7, "bold"))
        else:
            bone_x, bone_y = w * 0.60, h * 0.88
            ball_spots = ((w * 0.14, h * 0.87), (w * 0.36, h * 0.78), (w * 0.54, h * 0.90), (w * 0.75, h * 0.83), (w * 0.89, h * 0.88))
            dig_spots = ((w * 0.22, h * 0.86), (w * 0.68, h * 0.89), (w * 0.84, h * 0.84))
            cyc_idx = int(now // 120.0)
            ax, ay = ball_spots[cyc_idx % len(ball_spots)]
            bx_pos, by_pos = ball_spots[(cyc_idx + 1) % len(ball_spots)]
            sx, sy = dig_spots[cyc_idx % len(dig_spots)]
            day_t = now % 120.0
            carrying = lying = head_dn = hidden = digging = chewing = False
            toy_x, toy_y = (bx_pos, by_pos) if day_t >= 112.0 else (ax, ay)
            p_home = (khx + 6, khy + 12)
            p_water = (wb_x + 16, wb_y + 2)
            p_food = (fb_x + 16, fb_y + 2)
            p_gh_r = (w - 46, h * 0.87)
            p_dig = (sx + 16, sy - 1)
            p_bone = (bone_x + 16, bone_y)
            p_ball_src = (ax, ay)
            p_ball_dst = (bx_pos + 16, by_pos)

            def _lerp(a, b, t):
                ax_, ay_ = a; bx_, by_ = b
                return ax_ + (bx_ - ax_) * t, ay_ + (by_ - ay_) * t

            if day_t < 7.0:
                dx, dy = _lerp(p_home, p_water, day_t / 7.0)
                facing, hop, m_lbl = (-1 if p_water[0] < p_home[0] else 1), abs(math.sin(now * 8.0)) * 5.0, "Monti"
            elif day_t < 15.0:
                dx, dy = p_water
                facing, hop, head_dn, m_lbl = -1, 0.0, (int(now * 3.5) % 2 == 0), "Monti (trinkt)"
            elif day_t < 19.0:
                dx, dy = _lerp(p_water, p_food, (day_t - 15.0) / 4.0)
                facing, hop, m_lbl = 1, abs(math.sin(now * 7.5)) * 3.5, "Monti"
            elif day_t < 27.0:
                dx, dy = p_food
                facing, hop, head_dn, m_lbl = -1, 0.0, (int(now * 3.5) % 2 == 0), "Monti (frisst)"
            elif day_t < 38.0:
                p = (day_t - 27.0) / 11.0
                dx, dy = _lerp(p_food, p_gh_r, p)
                dy -= math.sin(p * math.pi) * (h * 0.04)
                facing, hop, m_lbl = 1, abs(math.sin(now * 9.0)) * 6.5, "Monti"
            elif day_t < 45.0:
                hidden, dx, dy, facing, hop, m_lbl = True, gmid, gy2 - 12, -1, 0.0, "Monti"
            elif day_t < 55.0:
                dx, dy = _lerp(p_gh_r, p_dig, (day_t - 45.0) / 10.0)
                facing, hop, m_lbl = (-1 if p_dig[0] < p_gh_r[0] else 1), abs(math.sin(now * 8.5)) * 6.0, "Monti"
            elif day_t < 68.0:
                dx, dy = p_dig
                facing, head_dn, digging, hop, m_lbl = -1, True, True, abs(math.sin(now * 16.0)) * 2.5, "Monti (buddelt!)"
                if not any(abs(hx_d - sx) < 12 and abs(hy_d - sy) < 12 for hx_d, hy_d, _ in holes):
                    holes.append((sx, sy, now))
            elif day_t < 77.0:
                dx, dy = _lerp(p_dig, p_bone, (day_t - 68.0) / 9.0)
                facing, hop, m_lbl = (-1 if p_bone[0] < p_dig[0] else 1), abs(math.sin(now * 8.5)) * 5.5, "Monti"
            elif day_t < 91.0:
                dx, dy = p_bone
                facing, lying, chewing, hop, m_lbl = -1, True, True, 0.0, "Monti (kaut Knochen)"
            elif day_t < 100.0:
                dx, dy = _lerp(p_bone, p_ball_src, (day_t - 91.0) / 9.0)
                facing, hop, m_lbl = (-1 if p_ball_src[0] < p_bone[0] else 1), abs(math.sin(now * 8.5)) * 6.0, "Monti"
            elif day_t < 112.0:
                p = (day_t - 100.0) / 12.0
                dx, dy = _lerp(p_ball_src, p_ball_dst, p)
                dy -= math.sin(p * math.pi) * 14.0
                facing, hop, carrying, m_lbl = (1 if p_ball_dst[0] >= p_ball_src[0] else -1), abs(math.sin(now * 9.5)) * 6.5, True, "Monti (spielt)"
            else:
                self._ball_pos = (bx_pos, by_pos)
                dx, dy = _lerp(p_ball_dst, p_home, (day_t - 112.0) / 8.0)
                facing, hop, m_lbl = (-1 if p_home[0] < p_ball_dst[0] else 1), abs(math.sin(now * 8.5)) * 6.0, "Monti"
            if deer_visible and not hidden:
                facing, hop, lying, head_dn, m_lbl = -1, abs(math.sin(now * 6.0)) * 2.0, False, False, "Monti: Wuff! ?" 
            self._dig_holes = holes
            if not chewing:
                self.create_line(bone_x - 7, bone_y, bone_x + 7, bone_y, fill="#f5f3eb", width=4, capstyle="round")
            if hidden:
                self.create_text(gmid, gy2 + 12, text="Monti schnüffelt hinter dem Zelt...", fill="#d4e4d6", font=(FONT, 7, "italic"))
            else:
                dy_draw = (dy + 4) if lying else (dy - hop)
                wag = math.sin(now * (24.0 if (deer_visible or digging) else 16.0)) * 4.5
                self.create_oval(dx - 17, dy + 8, dx + 17, dy + 14, fill="#9eb3c4" if season == "winter" else "#354f27", outline="")
                tx = dx - 15 * facing
                self.create_line(tx, dy_draw - 2, tx - 6 * facing, dy_draw - 10 + wag, fill="white", width=4, capstyle="round")
                for px_off in ((-12, 14) if lying else (-9, -3, 4, 10)):
                    self.create_oval(dx + px_off - 3, dy_draw + 3, dx + px_off + 3, dy_draw + 10, fill="#f7f7f7", outline="#dcdcdc")
                self.create_oval(dx - 15, dy_draw - 9, dx + 15, dy_draw + 6, fill="white", outline="#e6e6e6", width=1.2)
                hx, hy = dx + 13 * facing, (dy_draw - 2) if head_dn else (dy_draw - 8)
                self.create_oval(hx - 10, hy - 10, hx + 10, hy + 9, fill="white", outline="#e6e6e6", width=1.2)
                ear_lift = -4 if deer_visible else 0
                self.create_oval(hx - 9, hy - 5 + ear_lift, hx - 3, hy + 7 + ear_lift, fill="#f2f2f2", outline="#dcdcdc")
                self.create_oval(hx + 3, hy - 5 + ear_lift, hx + 9, hy + 7 + ear_lift, fill="#f2f2f2", outline="#dcdcdc")
                self.create_oval(hx + 3 * facing - 1.8, hy - 3, hx + 3 * facing + 1.8, hy + 0.6, fill="#1a1410", outline="")
                self.create_oval(hx + 7 * facing - 2, hy + 0.5, hx + 7 * facing + 2, hy + 4.5, fill="#111111", outline="")
                if chewing:
                    self.create_line(hx + 6 * facing, hy + 5, hx + 16 * facing, hy + 5, fill="#f5f3eb", width=4, capstyle="round")
                if digging:
                    for ci in range(4):
                        self.create_oval(hx + 8*facing - ((now*35 + ci*9) % 18) - 2, hy + 4 - abs(math.sin(now*16 + ci))*10, hx + 8*facing - ((now*35 + ci*9) % 18) + 2, hy + 8 - abs(math.sin(now*16 + ci))*10, fill="#5c3d28", outline="")
                self.create_text(dx, dy_draw - 23, text=m_lbl, fill="#ffeaa7" if deer_visible else "white", font=(FONT, 7, "bold"))
            bx_ball, by_ball = ((dx + 22 * facing) if (carrying and not hidden) else toy_x), ((dy - 4) if (carrying and not hidden) else toy_y)
            self.create_oval(bx_ball - 4.5, by_ball - 4.5, bx_ball + 4.5, by_ball + 4.5, fill="#e63946", outline="#9b1c26", width=1.2)

        w_obj = getattr(self, "weather", None)
        # Weiche Nebelschwaden & Niederschlag (Regen / Sturm / Gewitter / Schnee)
        if scen == "nebel":
            fog_col = "#4a5866" if is_night else "#cfd9e0"
            for fi in range(5):
                fx = ((now * (9 + fi * 2) + fi * 190) % (w + 260)) - 130
                fy = h * (0.28 + fi * 0.12)
                self.create_oval(fx - 110, fy - 14, fx + 110, fy + 14, fill=fog_col, outline="")

        if scen in ("regen", "sturm", "gewitter") or season == "winter":
            is_raining = scen in ("regen", "sturm", "gewitter")
            cnt = 36 if scen in ("sturm", "gewitter") else (26 if is_raining else 16)
            slant = 12 if scen in ("sturm", "gewitter") else (6 if is_raining else 3)
            for pi in range(cnt):
                px = ((pi * 53 + int(now * (140 if is_raining else 28))) % max(1, int(w)))
                py = ((pi * 37 + int(now * (260 if is_raining else 55))) % max(1, int(h * 0.92)))
                if is_raining:
                    self.create_line(px, py, px - slant, py + 12, fill="#7cc4f0", width=1.6)
                else:
                    self.create_oval(px - 2.5, py - 2.5, px + 2.5, py + 2.5, fill="white", outline="")

        loc_lbl = getattr(w_obj, "location_name", "Altenkirchen") if (env and getattr(env, "live_siegen", False)) else "Simulation"
        s_names = {"spring": "FRÜHLING", "summer": "HOCHSOMMER", "autumn": "HERBST", "winter": "WINTER"}
        hh, mm = int(hour_f), int(round((hour_f % 1.0) * 60)) % 60
        scen_lbl = {"gewitter": "GEWITTER", "nebel": "NEBEL", "sturm": "STURM", "regen": "REGEN"}.get(scen, "")
        extra_hdr = (f"{scen_lbl} · " if scen_lbl else "") + (f"{m_name} ({m_illum}%)" if is_night else s_names[season])
        _round_rect(self, 12, 8, 465, 40, 10, fill="#1d2b22", outline="#8fd9ae", width=1)
        self.create_text(24, 24, text=f"{loc_lbl.upper()} · {hh:02d}:{mm:02d} UHR · {extra_hdr}",
                         anchor="w", fill="white", font=(FONT, 9, "bold"))
        _round_rect(self, w - 142, 8, w - 12, 40, 10, fill=BAD, outline="")
        self.create_text(w - 77, 24, text="✕ Schließen", fill="white", font=(FONT, 10, "bold"))

#!/usr/bin/env python3
"""Animiertes Vollbild-Anlagenschema (48-V-Energiefluss, Relais, Lüfter L1-L3, 220-L-Tanksensor & Klima)."""

from __future__ import annotations
import time

import math
import tkinter as tk


def _round_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class SystemDiagramView(tk.Canvas):
    """Zeichnet das interaktive Energie- und Steuerungsschema als Vollbild-Canvas."""
    def __init__(self, master, on_close):
        super().__init__(master, bg="#1e2820", highlightthickness=0)
        self.on_close = on_close
        self.r = None
        self.pr = None
        self.ctrl = None
        self.manager = None
        self.phase = 0.0
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._on_click)

    def _on_click(self, e) -> None:
        w = self.winfo_width()
        if e.x >= w - 146 and e.y <= 44:
            self.on_close()

    def update_data(self, r, pr, ctrl, manager, env=None) -> None:
        """Übernimmt aktuelle Sensor-, Leistungs-, Steuerungs- und Umgebungsdaten für das Schaltbild."""
        self.r, self.pr, self.ctrl, self.manager, self.env = r, pr, ctrl, manager, env

    def step_anim(self) -> None:
        """Führt einen einzelnen Animationsschritt für die Energiefluss-Punkte aus."""
        self.phase = (self.phase + 0.08) % 1.0
        if self.winfo_ismapped():
            self.draw()

    def _wire_dots(self, pts: list[tuple[float, float]], active: bool, color: str, speed: float = 1.0) -> None:
        for i in range(len(pts) - 1):
            p0, p1 = pts[i], pts[i + 1]
            self.create_line(p0[0], p0[1], p1[0], p1[1], fill="#384a3c", width=5, capstyle="round")
        if not active:
            return
        shift = (self.phase * speed) % 1.0
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            dist = math.hypot(x1 - x0, y1 - y0)
            if dist < 1:
                continue
            step = 24.0
            for k in range(int(dist // step) + 1):
                pos = k * step + shift * step
                if pos <= dist:
                    t = pos / dist
                    px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
                    self.create_oval(px - 4, py - 4, px + 4, py + 4, fill=color, outline="")

    def _relay_switch(self, x: float, y: float, closed: bool, label: str = "") -> None:
        self.create_oval(x - 16, y - 3, x - 10, y + 3, fill="#cfd8cf", outline="")
        self.create_oval(x + 10, y - 3, x + 16, y + 3, fill="#cfd8cf", outline="")
        if closed:
            self.create_line(x - 13, y, x + 13, y, fill="#4f9d4f", width=4)
        else:
            self.create_line(x - 13, y, x + 10, y - 11, fill="#d2553f", width=3)
        if label:
            self.create_text(x, y + 13, text=label, fill="#4f9d4f" if closed else "#7a8a7a", font=("DejaVu Sans", 7, "bold"))

    def draw(self) -> None:
        """Zeichnet alle Blöcke, Leitungen, Relais, Sensoren und Statuswerte des Schemas neu."""
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 200 or h < 150:
            return
        pr, r, ctrl, mgr = self.pr, self.r, self.ctrl, self.manager
        solar_w = pr.solar_power_w if pr else 0.0
        bat_v = pr.battery_voltage_v if pr else 48.0
        bat_i = pr.battery_current_a if pr else 0.0
        bat_soc = pr.battery_soc_pct if pr else 70.0
        bat_t = pr.battery_temp_c if pr else 20.0
        ctrl_v = r.battery_v if r else 12.2
        fan_lvl = ctrl.fan.level if ctrl else 0.0
        pump_lvl = ctrl.pump.level if ctrl else 0.0

        outs = list(mgr.outlets) if mgr else []
        loads = list(pr.outlet_loads_w) if pr else []
        out1_on = outs[0].is_on if len(outs) > 0 else False
        out2_on = outs[1].is_on if len(outs) > 1 else False
        inv_on = out1_on or out2_on
        out1_w = loads[0] if len(loads) > 0 else 0.0
        out2_w = loads[1] if len(loads) > 1 else 0.0

        F = "DejaVu Sans"
        SUN, BLUE, GOOD, WARN, BAD, MUTED = "#e0a030", "#3b8fc4", "#4f9d4f", "#e0a030", "#d2553f", "#8fa092"
        self.create_text(18, 18, text="ANLAGENSCHEMA · LIVE-ENERGIEFLUSS & SCHALTZUSTÄNDE",
                         anchor="w", fill="#a8d98a", font=(F, 11, "bold"))

        x_sol, y_sol = 115, 95
        x_bat, y_bat = 400, 95
        x_dc,  y_dc  = 145, 255
        x_inv, y_inv = 445, 255
        x_o1,  y_o1  = 695, 205
        x_o2,  y_o2  = 695, 305

        self._wire_dots([(x_sol + 75, y_sol), (x_bat - 95, y_bat)], solar_w > 5, SUN, min(2.2, 0.5 + solar_w / 400))
        self._wire_dots([(x_bat - 30, y_bat + 48), (x_bat - 30, 165), (x_dc, 165), (x_dc, y_dc - 38)],
                        True, BLUE, 0.8 + (fan_lvl + pump_lvl) * 0.6)
        self._wire_dots([(x_dc - 45, y_dc + 38), (x_dc - 45, 345)], fan_lvl > 0.01, BLUE, 0.6 + fan_lvl * 1.4)
        self._wire_dots([(x_dc + 45, y_dc + 38), (x_dc + 45, 345)], pump_lvl > 0.01, "#5ec2e8", 0.6 + pump_lvl * 1.4)
        self._wire_dots([(x_bat + 45, y_bat + 48), (x_bat + 45, 165), (x_inv, 165), (x_inv, y_inv - 38)],
                        inv_on, WARN if bat_i < -0.5 else GOOD, 0.7 + (out1_w + out2_w) / 300)
        self._wire_dots([(x_inv + 75, y_inv - 12), (575, y_inv - 12), (575, y_o1), (x_o1 - 75, y_o1)],
                        out1_on, "#ff9f43", 0.7 + out1_w / 250)
        self._wire_dots([(x_inv + 75, y_inv + 12), (575, y_inv + 12), (575, y_o2), (x_o2 - 75, y_o2)],
                        out2_on, "#ff9f43", 0.7 + out2_w / 250)

        _round_rect(self, x_sol - 75, y_sol - 42, x_sol + 75, y_sol + 42, 14, fill="#29382c", outline=SUN, width=2)
        self.create_text(x_sol, y_sol - 24, text=f"SOLAR ({len(pr.strings) if pr else 2}× MPPT)", fill=SUN, font=(F, 9, "bold"))
        self.create_text(x_sol, y_sol + 2, text=f"{solar_w:.0f} W", fill="white", font=(F, 17, "bold"))
        raw_pv_w = sum(max(0.0, s.voltage_v * s.current_a) for s in pr.strings) if (pr and pr.strings) else 0.0
        if solar_w < 1.0:
            eta_txt = "MPPT Standby"
        elif raw_pv_w > solar_w and raw_pv_w > 1.0:
            eta_pct = min(98.5, max(50.0, (solar_w / raw_pv_w) * 100.0))
            eta_txt = f"{eta_pct:.1f} % MPPT-Wirkungsgrad"
        else:
            eta_pct = min(97.5, max(78.0, (solar_w / (solar_w * 1.028 + 1.8)) * 100.0))
            eta_txt = f"{eta_pct:.1f} % MPPT-Wirkungsgrad"
        self.create_text(x_sol, y_sol + 25, text=eta_txt, fill=MUTED, font=(F, 8))

        bcol = BAD if bat_soc < 20 else (WARN if bat_soc < 40 else GOOD)
        _round_rect(self, x_bat - 95, y_bat - 48, x_bat + 95, y_bat + 48, 14, fill="#29382c", outline=bcol, width=2)
        n_p = len(pr.battery_packs) if (pr and pr.battery_packs) else 4
        self.create_text(x_bat, y_bat - 31, text=f"48-V-BANK ({n_p} AKKUS)", fill=bcol, font=(F, 9, "bold"))
        self.create_text(x_bat, y_bat - 8, text=f"{bat_soc:.0f} % · {bat_v:.1f} V", fill="white", font=(F, 15, "bold"))
        self.create_text(x_bat, y_bat + 14, text=f"{bat_i:+.1f} A ({pr.battery_power_w if pr else 0:+.0f} W)", fill="#d4e4d6", font=(F, 10, "bold"))
        bms_txt = f"BMS: FROST-SCHLAF ({bat_t:.1f} °C)" if bat_t < 0.0 else "BMS: FROST-SPERRE (<2°)" if bat_t < 2.0 else ("BMS: KÄLTE-LIMIT" if bat_t < 10.0 else f"BMS OK · {bat_t:.1f} °C")
        bms_col = BAD if bat_t < 2.0 else (WARN if bat_t < 10.0 else "#a8d98a")
        self.create_text(x_bat, y_bat + 33, text=bms_txt, fill=bms_col, font=(F, 8, "bold"))

        _round_rect(self, x_dc - 85, y_dc - 38, x_dc + 85, y_dc + 38, 14, fill="#29382c", outline=BLUE, width=2)
        self.create_text(x_dc, y_dc - 21, text="DC-WANDLER & RASPI", fill="#7cc4f0", font=(F, 9, "bold"))
        self.create_text(x_dc, y_dc + 1, text=f"{ctrl_v:.2f} V DC", fill="white", font=(F, 15, "bold"))
        self.create_text(x_dc, y_dc + 22, text="Grundlast: 7 W", fill=MUTED, font=(F, 8))

        ffaults = getattr(tk._default_root, "_fan_faults", set()) if tk._default_root is not None else set()
        s_err = bool(getattr(tk._default_root, "_app_ctrl_error", False)) if tk._default_root is not None else False
        _w_lim = float(getattr(tk._default_root, "_runtime_settings", {}).get("winter_temp_c", 10.0)) if tk._default_root is not None else 10.0
        w_lock = bool(not getattr(self.ctrl, "manual", False) and (r.temperature_c < _w_lim if r else False))
        _round_rect(self, x_dc - 124, 342, x_dc - 4, 402, 10, fill="#29382c", outline=BAD if (s_err or len(ffaults) >= 3) else (WARN if getattr(self.ctrl, "manual", False) else (BLUE if fan_lvl > 0 else ("#4b7a9c" if w_lock else GOOD))), width=2)
        fcx, fcy = x_dc - 107, 372
        self.create_oval(fcx - 9, fcy - 9, fcx + 9, fcy + 9, outline="#7cc4f0" if fan_lvl > 0 else "#4a5c4e", width=1.5)
        rot = (time.monotonic() * (1.0 + fan_lvl * 14.0)) if fan_lvl > 0 else 0.3
        for b in range(3):
            ang = rot + b * (2 * math.pi / 3)
            self.create_line(fcx, fcy, fcx + 6 * math.cos(ang), fcy + 6 * math.sin(ang),
                             fill="#7cc4f0" if fan_lvl > 0 else MUTED, width=2.5, capstyle="round")
        ffaults = getattr(tk._default_root, "_fan_faults", set()) if tk._default_root is not None else set()
        s_err = bool(getattr(tk._default_root, "_app_ctrl_error", False)) if tk._default_root is not None else False
        dehum_until = getattr(tk._default_root, "_dehum_until", 0.0) if tk._default_root is not None else 0.0
        dehum_rem = max(0, int(round(dehum_until - time.monotonic())))
        _w_lim = float(getattr(tk._default_root, "_runtime_settings", {}).get("winter_temp_c", 10.0)) if tk._default_root is not None else 10.0
        w_lock = bool(not getattr(self.ctrl, "manual", False) and (r.temperature_c < _w_lim if r else False))

        # Drei kleine Einzel-Badges L1, L2, L3 mittig über dem Textbereich (x_dc - 36)
        ftx = x_dc - 49
        fan_levels = getattr(tk._default_root, "_fan_levels", None) if tk._default_root is not None else None
        if not fan_levels or len(fan_levels) != 3:
            fan_levels = [0.0 if (s_err or i in ffaults) else fan_lvl for i in range(3)]
        fparked = getattr(tk._default_root, "_fan_parked", set()) if tk._default_root is not None else set()
        for li in range(3):
            bx = ftx - 20 + li * 20
            by = 354
            fparked = getattr(tk._default_root, "_fan_parked", set()) if tk._default_root is not None else set()
            if s_err or li in ffaults:
                bcol, tcol = BAD, "white"
            elif li in fparked:
                bcol, tcol = "#b388eb", "#1f1629"
            elif fan_levels.__getitem__(li) > 0.01:
                bcol, tcol = "#2b7ca8", "white"
            elif w_lock:
                bcol, tcol = "#2d4c63", "#7cc4f0"
            else:
                bcol, tcol = GOOD, "white" 
            _round_rect(self, bx - 7, by - 5, bx + 7, by + 5, 3, fill=bcol, outline="")
            self.create_text(bx, by, text=f"L{li+1}", fill=tcol, font=(F, 6, "bold"))

        if s_err or len(ffaults) >= 3:
            f_sub, f_scol = "GESPERRT", BAD
        elif w_lock and fan_lvl == 0:
            f_sub, f_scol = f"WINTER (<{_w_lim:.0f}°)", "#7cc4f0"
        elif fan_lvl > 0 and dehum_rem > 0:
            f_sub, f_scol = f"ENTF. {round(fan_lvl*100)}% ({dehum_rem}s)", "#7cc4f0"
        elif fan_lvl > 0 and getattr(self.ctrl, "manual", False):
            f_sub, f_scol = f"HAND {round(fan_lvl*100)}%", "#7cc4f0"
        elif fan_lvl > 0:
            f_sub, f_scol = f"KÜHL. {round(fan_lvl*100)}%", "#7cc4f0"
        elif fparked:
            p_names = "/".join(f"L{i+1}" for i in sorted(fparked))
            f_sub, f_scol = f"0 % · {p_names} PARK", "#b388eb"
        else:
            f_sub, f_scol = "0 % · BEREIT", GOOD
        self.create_text(ftx, 373, text=f_sub, fill=f_scol, font=(F, 6, "bold"))
        self.create_text(ftx, 391, text="3× 120mm LÜFTER", fill=MUTED, font=(F, 6))

        p_fault = bool(getattr(self.ctrl, "pump_fault", None) or s_err)
        _round_rect(self, x_dc + 4, 342, x_dc + 116, 402, 10, fill="#29382c", outline=BAD if p_fault else (WARN if getattr(self.ctrl, "manual", False) else ("#5ec2e8" if pump_lvl > 0 else ("#4b7a9c" if w_lock else GOOD))), width=2)
        pcx, pcy = x_dc + 18, 372
        pcol = BAD if p_fault else ("#5ec2e8" if pump_lvl > 0 else ("#7cc4f0" if w_lock else GOOD))
        self.create_polygon(pcx, pcy - 9, pcx - 6, pcy + 2, pcx, pcy + 9, pcx + 6, pcy + 2, smooth=True, fill=pcol, outline="")
        now_m = time.monotonic()
        p_fault = bool(getattr(self.ctrl, "pump_fault", None) or (getattr(tk._default_root, "_app_ctrl_error", False) if tk._default_root is not None else False))
        run_rem = max(0, int(round(getattr(self.ctrl, "_pump_until", 0.0) - now_m)))
        soak_rem = max(0, int(round(getattr(self.ctrl, "_soak_until", 0.0) - now_m)))
        if p_fault:
            p_sub, p_scol = "GESPERRT", BAD
        elif w_lock and pump_lvl == 0:
            p_sub, p_scol = f"WINTER (<{_w_lim:.0f}°)", "#7cc4f0"
        elif pump_lvl > 0:
            p_sub, p_scol = (f"{round(pump_lvl*100)}% ({run_rem}s)" if run_rem > 0 else f"{round(pump_lvl*100)} %"), "#5ec2e8"
        elif soak_rem > 0:
            p_sub, p_scol = f"PAUSE {soak_rem // 60}:{soak_rem % 60:02d}", WARN
        else:
            p_sub, p_scol = "0 % · BEREIT", GOOD
        self.create_text(x_dc + 72, 360, text="PUMPE", fill="white", font=(F, 8, "bold"))
        self.create_text(x_dc + 62, 383, text=p_sub, fill=p_scol, font=(F, 7 if len(p_sub) > 9 else 8, "bold"))

        # Füllstandssensor (Regentonne 0-220 L) mit Sensorleitung zur Pumpe
        env = getattr(self, "env", None)
        tank_l = float(getattr(env, "tank_liters", getattr(tk._default_root, "_tank_liters", 165.0)))
        tank_pct = max(0.0, min(100.0, (tank_l / 220.0) * 100.0))
        tx1, tx2 = x_dc + 124, x_dc + 258
        t_col = BAD if tank_l < 10.0 else (WARN if tank_l < 35.0 else "#5ec2e8")
        self.create_line(x_dc + 114, 372, tx1, 372, fill=t_col, width=1.8, dash=(3, 2))
        _round_rect(self, tx1, 342, tx2, 402, 10, fill="#29382c", outline=t_col, width=2)
        self.create_text((tx1 + tx2) / 2, 354, text="TANK-SENSOR (220 L)", fill=t_col, font=(F, 7, "bold"))
        self.create_text((tx1 + tx2) / 2, 373, text=f"{tank_l:.0f} L ({tank_pct:.0f} %)", fill="white", font=(F, 9, "bold"))
        self.create_text((tx1 + tx2) / 2, 390, text="LEER -> PUMPE SPERRE" if tank_l < 10.0 else ("RESERVE NIEDRIG" if tank_l < 35.0 else "FÜLLSTAND OK"),
                         fill=t_col, font=(F, 7, "bold"))

        # Live-Klima-Kachel rechts unten im Schema
        if r is not None:
            kx1, kx2 = tx2 + 10, w - 24
            if kx2 - kx1 >= 140:
                kcx = (kx1 + kx2) / 2
                kcol = "#7cc4f0" if r.temperature_c < 10.0 else (BAD if (r.soil_moisture_pct > 88.0 or (r.temperature_c >= 12.0 and r.humidity_pct > 92.0)) else GOOD)
                _round_rect(self, kx1, 342, kx2, 402, 10, fill="#29382c", outline=kcol, width=2)
                self.create_text(kcx, 355, text="GEWÄCHSHAUS-KLIMA (SCHALTGRUND)", fill=kcol, font=(F, 7, "bold"))
                self.create_text(kcx, 374, text=f"Innen: {r.temperature_c:.1f} °C  ·  Luft: {r.humidity_pct:.0f} %",
                                 fill="white", font=(F, 9, "bold"))
                self.create_text(kcx, 391, text=f"Bodenfeuchte: {r.soil_moisture_pct:.0f} %  ·  Steuer-U: {r.battery_v:.2f} V",
                                 fill="#d4e4d6", font=(F, 8))

        self._relay_switch(x_inv, 190, inv_on, "WR-RELAIS")
        wr_col = GOOD if inv_on else "#5a6b5e"
        _round_rect(self, x_inv - 75, y_inv - 38, x_inv + 75, y_inv + 38, 14, fill="#29382c", outline=wr_col, width=2)
        self.create_text(x_inv, y_inv - 21, text="WECHSELRICHTER", fill=GOOD if inv_on else MUTED, font=(F, 9, "bold"))
        self.create_text(x_inv, y_inv + 1, text="230 V EIN" if inv_on else "FROST-SCHLAF" if bat_t < 0.0 else "STANDBY", fill="white" if inv_on else MUTED, font=(F, 14, "bold"))
        self.create_text(x_inv, y_inv + 22, text="Eigenverbrauch: 4 W" if inv_on else "0 W (ausgeschaltet)", fill=MUTED, font=(F, 8))

        self._relay_switch(598, y_o1, out1_on)
        _round_rect(self, x_o1 - 75, y_o1 - 28, x_o1 + 75, y_o1 + 28, 12, fill="#29382c", outline=GOOD if out1_on else "#4a5c4e", width=2)
        self.create_text(x_o1, y_o1 - 11, text="STECKDOSE 1", fill="white", font=(F, 9, "bold"))
        self.create_text(x_o1, y_o1 + 10, text=f"AN · {out1_w:.0f} W" if out1_on else "AUS", fill=GOOD if out1_on else MUTED, font=(F, 11, "bold"))

        self._relay_switch(598, y_o2, out2_on)
        _round_rect(self, x_o2 - 75, y_o2 - 28, x_o2 + 75, y_o2 + 28, 12, fill="#29382c", outline=GOOD if out2_on else "#4a5c4e", width=2)
        self.create_text(x_o2, y_o2 - 11, text="STECKDOSE 2", fill="white", font=(F, 9, "bold"))
        self.create_text(x_o2, y_o2 + 10, text=f"AN · {out2_w:.0f} W" if out2_on else "AUS", fill=GOOD if out2_on else MUTED, font=(F, 11, "bold"))

        _round_rect(self, w - 142, 8, w - 12, 40, 10, fill=BAD, outline="")
        self.create_text(w - 77, 24, text="✕ Schließen", fill="white", font=(F, 10, "bold"))

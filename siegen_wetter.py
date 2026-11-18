#!/usr/bin/env python3
"""Live-Wetter & Astronomie (Standort-Erkennung, Open-Meteo WMO-Codes & Mondphase)."""
from __future__ import annotations

import datetime
import json
import logging
import math
import ssl
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("greenhouse.weather")

LOCATION_NAME = "Altenkirchen"
LATITUDE, LONGITUDE = 50.6873, 7.6458
API_HOST = "https://api.open-meteo.com/v1/forecast"


def calc_moon_phase(ts: float | None = None) -> tuple[float, str, int]:
    """Berechnet Mondphase (0.0=Neumond .. 0.5=Vollmond .. 1.0=Neumond), deutschen Namen & Beleuchtung %."""
    now_ts = time.time() if ts is None else ts
    days = (now_ts - 947182440.0) / 86400.0
    phase = (days % 29.53058867) / 29.53058867
    illum = int(round((1.0 - math.cos(2.0 * math.pi * phase)) * 50.0))
    names = [
        "Neumond", "Zunehmende Sichel", "Erstes Viertel", "Zunehmender Mond",
        "Vollmond", "Abnehmender Mond", "Letztes Viertel", "Abnehmende Sichel",
    ]
    idx = int((phase * 8.0 + 0.5)) % 8
    return phase, names[idx], illum


def weather_code_text(code: int) -> str:
    """Übersetzt einen numerischen WMO-Wettercode in eine verständliche deutsche Wetterbeschreibung."""
    if code == 0:
        return "Klarer Himmel"
    if code in (1, 2):
        return "Teils bewölkt"
    if code == 3:
        return "Bedeckt"
    if code in (45, 48):
        return "Nebel"
    if code in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "Regen"
    if code in (71, 73, 75, 77, 85, 86):
        return "Schneefall"
    if code in (95, 96, 99):
        return "Gewitter"
    return "Wechselhaft"


@dataclass(frozen=True)
class LiveWeather:
    """Unveränderlicher Datensatz für aktuelle Wetter-, Strahlungs-, Standort- und Mondphasendaten."""
    temperature_c: float
    humidity_pct: float
    cloud_cover_pct: float
    radiation_wm2: float
    timestamp: str
    wind_kmh: float = 0.0
    weather_code: int = 1
    weather_text: str = "Teils bewölkt"
    is_day: bool = True
    precipitation_mm: float = 0.0
    sunrise_hour: float = 6.5
    sunset_hour: float = 19.5
    moon_phase: float = 0.5
    moon_name: str = "Vollmond"
    moon_illum_pct: int = 100
    location_name: str = LOCATION_NAME
    source_name: str = f"{LOCATION_NAME} (Open-Meteo)"


class SiegenWeatherClient:
    """Asynchroner Hintergrund-Client für Standort-Erkennung, Open-Meteo-Wetterdaten und Mondphasen."""
    def __init__(self, poll_interval_s: float = 300.0, auto_locate: bool = True) -> None:
        self.poll_interval_s = poll_interval_s
        self.auto_locate = auto_locate
        self.lat = LATITUDE
        self.lon = LONGITUDE
        self.loc_name = LOCATION_NAME
        self._located = False
        self._latest: LiveWeather | None = None
        self._error: str = ""
        self._lock = threading.Lock()
        self._ssl_ctx = ssl.create_default_context()
        threading.Thread(target=self._loop, daemon=True, name="weather-client").start()

    def refresh_now(self) -> None:
        """Startet sofort eine asynchrone Aktualisierung der Live-Wetterdaten."""
        threading.Thread(target=self._fetch_once, daemon=True).start()

    def _detect_location(self) -> None:
        if not self.auto_locate or self._located:
            return
        self._located = True
        try:
            req = urllib.request.Request("https://ipapi.co", headers={"User-Agent": "SolarGreenHub/0.27.3"})
            with urllib.request.urlopen(req, timeout=4.0, context=self._ssl_ctx) as resp:
                geo = json.loads(resp.read().decode("utf-8"))
            if "latitude" in geo and "longitude" in geo:
                self.lat = float(geo["latitude"])
                self.lon = float(geo["longitude"])
                self.loc_name = str(geo.get("city") or LOCATION_NAME)
                log.info("Standort erkannt: %s (%.4f, %.4f)", self.loc_name, self.lat, self.lon)
        except Exception:
            pass

    @staticmethod
    def _parse_hour(iso_str: str, fallback: float) -> float:
        try:
            t_part = iso_str.split("T")[1]
            hh, mm = t_part.split(":")[:2]
            return int(hh) + int(mm) / 60.0
        except Exception:
            return fallback

    def _fetch_once(self) -> None:
        self._detect_location()
        try:
            params = urllib.parse.urlencode({
                "latitude": f"{self.lat:.4f}",
                "longitude": f"{self.lon:.4f}",
                "current": "temperature_2m,relative_humidity_2m,cloud_cover,shortwave_radiation,wind_speed_10m,weather_code,is_day,precipitation",
                "daily": "sunrise,sunset",
                "forecast_days": "1",
                "timezone": "auto",
            })
            req = urllib.request.Request(
                f"{API_HOST}?{params}",
                headers={"User-Agent": "SolarGreenHub/0.27.3", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=8.0, context=self._ssl_ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            cur = data["current"]
            daily = data.get("daily", {})
            sr = self._parse_hour((daily.get("sunrise") or [""])[0], 6.5)
            ss = self._parse_hour((daily.get("sunset") or [""])[0], 19.5)
            wcode = int(cur.get("weather_code", 1))
            m_phase, m_name, m_illum = calc_moon_phase()

            weather = LiveWeather(
                temperature_c=float(cur["temperature_2m"]),
                humidity_pct=float(cur["relative_humidity_2m"]),
                cloud_cover_pct=float(cur["cloud_cover"]),
                radiation_wm2=float(cur.get("shortwave_radiation", 0.0)),
                wind_kmh=float(cur.get("wind_speed_10m", 0.0)),
                weather_code=wcode,
                weather_text=weather_code_text(wcode),
                is_day=bool(cur.get("is_day", 1)),
                precipitation_mm=float(cur.get("precipitation", 0.0)),
                sunrise_hour=sr,
                sunset_hour=ss,
                moon_phase=m_phase,
                moon_name=m_name,
                moon_illum_pct=m_illum,
                location_name=self.loc_name,
                source_name=f"{self.loc_name} (Open-Meteo)",
                timestamp=str(cur.get("time", "")),
            )
            with self._lock:
                self._latest = weather
                self._error = ""
            log.info(
                "Wetter %s aktualisiert: %.1f°C, %s (%d%% Wolken), Mond: %s (%d%%)",
                self.loc_name, weather.temperature_c, weather.weather_text,
                int(weather.cloud_cover_pct), m_name, m_illum
            )
        except Exception as e:
            with self._lock:
                self._error = str(e)
            log.warning("Wetter-Abruf fehlgeschlagen: %s", e)

    def _loop(self) -> None:
        while True:
            self._fetch_once()
            time.sleep(self.poll_interval_s)

    def get_latest(self) -> tuple[LiveWeather | None, str]:
        """Liefert den zuletzt abgerufenen Wetterdatensatz sowie eine eventuelle Fehlermeldung."""
        with self._lock:
            return self._latest, self._error

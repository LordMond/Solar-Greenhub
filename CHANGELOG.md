# Changelog – Solar GreenHub

## `build_0.27.3_STABLE` – Revision 3 (Aktueller Entwicklungsstand – 18.11.2026)
### Neu & Erweitert
* **Erweiterte Wetterszenarios & PV-Kopplung (`garden_view.py`, `sim_panel.py`, `power.py`):**
  * Neue Wetterszenarios **`Gewitter`** (dunkelblauer Sturmhimmel, peitschender Regen & aufzuckende Zickzack-Blitze), **`Nebel`** (milchig ziehende Nebelschwaden) und **`Sturm`** (stark geneigte Bäume & heftige Windböen) im Simulations-Labor und automatisch gekoppelt an Live-WMO-Wettercodes.
  * Die **48-V-PV-Anlage (`SimPower`)** reagiert jetzt realistisch auf jedes einzelne Wetterszenario (starker Leistungseinbruch bei `Gewitter`, `Sturm`, `Regen` und `Nebel`, volle Leistung bei `Klar`).
* **24-Minuten-Tagesverlauf & saisonaler Tages-Zufallsgenerator (`sim_panel.py`, `greenhouse.py`, `power.py`):**
  * Automatische Tageszeit (`Auto-Tag`) auf **24 Minuten (`DAY_S = 1440.0 s`, 1 Echtminute = 1 simulierte Stunde)** umgestellt.
  * Jeder simulierte Tag würfelt automatisch einen zur Jahreszeit passenden Tagesablauf (Temperatur-, Wolken-, Wind- und Wettervariation); im `Auto-Tag` werden `Luftfeuchte (%)`, `Bodenfeuchte (%)` und `Regentonne` live im Tagesverlauf mitgeführt.
* **Lebendige Gartenwelt & Montis 120-Sekunden-Tagesablauf (`garden_view.py`):**
  * **Malteser Monti:** Bewegt sich ohne Springen/Spawnen in einer stetigen Wegpunkt-Kette über den gesamten Vordergrund – trinkt & frisst aus seinem **blauen Wasser- und roten Fressnapf** vor der Hundehütte, verschwindet kurz **hinter dem Gewächshaus**, buddelt an wechselnden Stellen **selbstheilende Erdlöcher** (schließen sich nach `22 Sekunden` wieder), kaut liegend auf seinem **weißen Hundeknochen**, trägt seinen **roten Spielball** zwischen **5 Ablageplätzen** hin und her und ruft **`Monti: Wuff! ?`**, sobald das Reh links oder rechts hinter der Hütte hervorschaut.
  * **Tierwelt:** Das **Reh** schaut links und rechts hinter der Gartenhütte hervor und spaziert zusätzlich im Hintergrund durch den Wald; bunte **Vögel** fliegen weite Bögen über den Himmel und landen auf wechselnden Positionen auf dem Hüttendach.

* **Sanfte 4-Zustands-Lüfterkurve & Einzel-Lüfter-Parkposition (`greenhouse_gui.py`, `greenhouse.py`, `settings_page.py`, `garden_view.py`, `diagram_view.py`):**
  * **Feuchtegesteuerte Lüfterkurve:** Lüfter starten ab der eingestellten Start-Luftfeuchte (Standard `76 %`) über einen weichen **Sanftanlauf**, halten ihre maximale Profildrehzahl bis zum Erreichen der Ziel-Luftfeuchte (Standard `65 %`), laufen über einen **Sanftauslauf** auf `0 %` aus und warten in Ruhepause bis zum erneuten Erreichen von `76 %` (automatisch skaliert zwischen `24-Minuten-Auto-Tag` und `24-Stunden-Live-Wetter`).
  * **Jahreszeiten- & Leistungs-Einstellungsmenü (`settings_page.py`):** 4 getrennte, dauerhaft gespeicherte Profile (`Frühling`, `Hochsommer`, `Herbst`, `Winter`) mit `Auto-Repeat`-Touchtasten für `Lüfter-Leistung (30–100 %)`, `Aktive Lüfter (0–3 Stk)`, `Entfeuchten ab/bis`, `Lüfter-Start Temp.`, `Winterschutz`, `Pumpen-Durchsatz`, `Pumpen-Laufzeit` und `Gieß-Intervall`.
  * **Parkposition (`#b388eb` – Helles Lila):** Deaktivierte bzw. geparkte Lüfter (`0–3 Stk`) werden in der Reihenfolge **Mitte (`L2`) → Links (`L1`) → Rechts (`L3`)** in **Hellem Lila (`PARK`)** mit stillstehenden Rotorblättern im Gewächshaus und als hell-lila Badges im Vollbild-Schaltbild angezeigt (kollisionsfrei zum **Orange** des Handbetriebs).

* **Überarbeitetes Simulations-Labor & 14-Stufen-Geschwindigkeitsregler (`sim_panel.py`):**
  * **Gemeinsame `Tageszeit-Steuerung`-Box:** Der blaue Button **`Auto-Tag: AN/AUS`** ersetzt die alte Häkchenbox direkt oben unter den Szenario-Buttons, inklusive Live-Berechnung der echten Tageslänge (`1 Tag = 24.0 min` bis `8.0 min` bei `1.00×` bis `3.00×` über **Stufe 1–14**).
  * **Ausgegraute Live- & Auto-Slider:** Alle automatisch mitgeführten Regler (`Uhrzeit`, `Außentemperatur`, `Bewölkung`, `Bodenfeuchte`, `Luftfeuchte`, `Regentonne`) werden im Live- und Auto-Tag-Modus optisch ausgegraut und gesperrt, laufen aber sichtbar mit.
  * **Dynamische Tages-Wetterfronten im `Auto-Tag`:** Morgennebel lichtet sich ab `09:30 Uhr`, Regen- und Schneeschauer ziehen in begrenzten Fronten durch und Sommergewitter entladen sich nachmittags mit anschließendem klaren Abend.
* **Erweitertes Alarm- & Hinweissystem (`greenhouse_gui.py`):**
  * **Vollflächen-Alarm-Blinken:** Bei kritischen Störungen (**Pumpen-Trockenlauf `< 10 L`** oder **BMS Zell-Unterspannung `< 11,5 V`**) pulsiert der gesamte Hintergrund über alle **4 Hauptmenüs** (`Gewächshaus`, `Solar`, `Akku`, `Steckdosen`) alarmrot mit rot umrandeten weißen Karten, bis der Alarm quittiert wird.
  * **Zweiklang-Alarmton (`880 Hz -> 660 Hz`):** Dezenter akustischer Hinweiston alle **5 Live-Minuten** bzw. **5 Simulations-Minuten** bei jeder aktiven Störung.
  * **Ereignis-Laufmeldungen für Doppelklick-Nutzer:** Live-Wetter-Updates sowie Start-, Drehzahl- (`L1–L3`, `PARK`, `FEHLER`) und Stopp-Meldungen von Lüftern und Pumpe werden automatisch in den normalen Ereignisspeicher (`events.log`) geschrieben.

### Beseitigte Bugs (`build_0.27.3_STABLE` – Revision 3)
1. **Regen-Animation & Bewölkung ohne Regen (`garden_view.py`):** Reine Bewölkung ohne Niederschlag (`clouds >= 80 %`) schickte Monti fälschlicherweise in die Hütte, und beim Umbau von Montis Ablauf war die Niederschlags-Schleife entfernt bzw. vor Definition von `scen` eingefügt worden (`UnboundLocalError`). Behoben durch Trennung von reiner Bewölkung und echtem Niederschlag sowie Platzierung der Regen-/Schneeschleife vor dem Titelbalken.
2. **Springen / „Spawnen“ von Monti & Zurückspringen des Balls (`garden_view.py`):** Beim Wechsel von Aktionen und bei Reh-Sichtung sprang Monti auf feste Startpunkte, und der Ball sprang nach dem Ablegen auf die Ausgangsposition zurück. Behoben durch eine stetige Wegpunkt-Kette (`120 s`), Stehenbleiben an Ort und Stelle bei Reh-Sichtung sowie Speicherung des aktuellen Ball-Liegeplatzes (`_ball_pos`) über 5 Gartenplätze.
3. **Tuple-Index- & Typfehler in der Garten-Animation (`garden_view.py`):** `TypeError` beim Altern der Erdlöcher (`now - h_i` statt `now - h_i.__getitem__(2)`) sowie in `_lerp` bei Koordinaten-Substraktion behoben.
4. **Fehlende `Auto-Tag`-Synchronisation (`sim_panel.py`, `power.py`, `greenhouse.py`):** Im `Auto-Tag`-Modus wurden `manual_hour`, `override_hum_pct` und `override_soil_pct` nicht freigegeben, sodass Luft- und Bodenfeuchte stehen blieben und `SimPower`/`SimSensors` `sim_hour` nicht auswerteten. Behoben durch durchgängige Kopplung an `sim_hour` und laufende Regler-Aktualisierung.
5. **Feststeckender 165-L-Füllstand im Vollbild-Schaltbild (`diagram_view.py`, `greenhouse_gui.py`):** Der `TANK-SENSOR (220 L)` im Anlagenschema las ein ungesetztes Root-Attribut statt `env.tank_liters` aus und blieb dadurch konstant bei `165 L` stehen. Behoben durch Übergabe von `self.env` in `diagram_view.update_data(...)` und direkte Live-Anbindung an `env.tank_liters`.
6. **Unrealistisch niedrige Luftfeuchte (16 %) bei Regen im `Auto-Tag` (`greenhouse.py`, `sim_panel.py`):** Bei aktivem Niederschlag (`regen`, `sturm`, `gewitter`, `nebel`, `rain_sim`) wurde `env.outside_humidity_pct` nicht angehoben, und der Entfeuchtungslüfter drückte die Zeltluftfeuchte bis auf `15–16 %`. Behoben durch Kopplung der Außenluftfeuchte (`>= 88 %`) an Niederschlags-Szenarien und feuchte Zuluft im Lüftermodell (`80–96 %` Zeltfeuchte bei Regen).
7. **Zu geringe Entfeuchtungsleistung der 3 Lüfter (`greenhouse.py`):** In `SimSensors.read()` wurde nur die Leistung eines einzelnen Lüfters statt aller aktiven Lüfter (`3 × 2.600 U/min`) bei der Luftfeuchte-Absenkung einberechnet, und manuelles Slider-Feedback (`override_hum_pct`) verhinderte ein erneutes Ansteigen der Luftfeuchte in Lüfterpausen. Behoben durch Skalierung mit der aktiven Lüfteranzahl und realistisches Ansteigen der Zeltfeuchte in Ruhephasen.
8. **Fehlende Live-Rückmeldung geparkter Lüfter (`settings_page.py`, `garden_view.py`, `diagram_view.py`):** Änderungen an der aktiven Lüfteranzahl (`0–3 Stk`) im Einstellungsmenü wurden erst beim nächsten Regelzyklus und ohne eigene Statusfarbe übernommen. Behoben durch sofortige Aktualisierung von `_fan_parked` & `_fan_levels` beim Tippen sowie Darstellung in **Hellem Lila (`#b388eb`)** mit stehenden Rotoren.
9. **Fehlende `SettingsPage.refresh()`-Methode (`settings_page.py`):** Beim zyklischen Aktualisieren aller Seiten in `App.tick()` löste `SettingsPage` einen `AttributeError` aus. Behoben durch Ergänzung von `refresh(r, pr)` mit Live-Zeltwerten in der Kopfzeile.
10. **Nicht blinkendes Warndreieck bei Lüfterstörungen (`greenhouse_gui.py`):** Einzelne Lüfterausfälle (`L1–L3`) wurden protokolliert, lösten aber kein Blinken des Kopfzeilen-Warndreiecks aus. Behoben durch Einbindung von `_fan_faults` in die Alarm-Erkennung.
11. **Attributfehler in `_anim_loop()` bei Zell-Unterspannung (`greenhouse_gui.py`):** `BatteryPackReading.voltage_v` statt `cell_min_v` verwendet, wodurch das rote Alarm-Blinken im `80-ms`-Animationstakt fehlerfrei arbeitet.

---

## `build_0.27.3_STABLE` – Revision 2 (17.11.2026)
* Gestaffelte Berge mit Schneegipfeln, im Wind wiegende Bäume, Windböen-Streifen, astronomische Dämmerung (Sommer-/Winterzeit), plausibles Bodenverdunstungsmodell, Live-Wetter-Slider-Synchronisation und `100 %` Docstring-Abdeckung über alle 9 Python-Dateien.

---

## `build_0.27.3_STABLE` – Basis-Release (17.11.2026)
* 220-Liter-Regentonnen-System mit Trockenlaufschutz (`< 10 L`), animierte 4-Jahreszeiten-Gartenwelt (`garden_view.py`), 5-Minuten-Bildschirmschoner und Touch-Einstellungsmenü (`settings_page.py`).

---

## `build_0.27.2_STABLE` (17.11.2026)
* Eigenständiges Linux-Release (`SolarGreenHub_0.27.2_Stable_Linux`), zweigeteiltes Störungs- & Ereignismenü (`EventsPage`) und Einzel-Lüfter-Überwachung (`L1–L3`).

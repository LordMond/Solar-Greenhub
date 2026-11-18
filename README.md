# Projektdokumentation: Solar GreenHub (`build_0.27.3_STABLE`)

## 1. Projektübersicht & Zielsetzung
**Solar GreenHub** ist eine autarke, netzunabhängige Solar-, Speicher- und Gewächshaus-Steuerung auf Basis eines **Raspberry Pi** mit Python- und Tkinter-Touch-Oberfläche (`800 × 480 px` für das offizielle 7-Zoll-Touchdisplay).  
Das System verbindet zwei Aufgaben in einer gemeinsamen, fehlertoleranten Steuerung:
1. **Insel-Energie- & Batteriemanagement (48-V-Speicherbank):** Überwachung der PV-Erzeugung (MPPT), einer 48-V-LiFePO4-Speicherbank (inklusive Einzelpack-Spannungen und Akkutemperatur), eines 12-V-DC-Abwärtswandlers sowie eines 230-V-Sinus-Wechselrichters mit zwei zeit- und schutzgesteuerten Steckdosen.
2. **Klima-, Wasser- & Bewässerungsregelung (12-V-Gewächshauskreis):** Vollautomatische und manuelle Steuerung von drei einzeln überwachten 120-mm-Abluftlüftern sowie einer 12-V-Bewässerungspumpe inklusive **220-Liter-Regentonnen-Überwachung** anhand von Innen-/Außentemperatur, Luftfeuchte, Bodenfeuchte und lokalen Live-Wetterdaten.

---

## 2. Modulare Systemarchitektur (`build_0.27.3_STABLE`)
Alle Hauptfunktionen und Untermenüs sind sauber in eigenständige Python-Module getrennt:

| Datei / Modul | Aufgabe & Verantwortung |
| :--- | :--- |
| **`greenhouse.py`** | Kern-Steuerlogik (`Controller`), Datenklassen (`Reading`, `Config`), PWM-Aktorsteuerung (`SimActuator` / `GpioActuator`), Simulations-Sensoren (`SimSensors`) und Hardware-Schnittstelle (`PiSensors`). |
| **`power.py`** | Energiemanagement (`PowerManager`), 48-V-Batterie- & BMS-Überwachung, MPPT-Laderegelung, Wechselrichter-Freigabe sowie Timer- und Laststeuerung der 230-V-Steckdosen. |
| **`power_hw.py`** | Hardware-Anbindung und Real-World-Schnittstelle für die Leistungselektronik (Strom-/Spannungsmessung, Relais- und BMS-Daten). |
| **`greenhouse_gui.py`** | Hauptanwendung (`App`), Touch-Bedienoberfläche mit 4 Hauptseiten, aufgeräumter Kopfzeile (Gewächshaus-Icon, Zahnrad-Icon, blinkendes Warndreieck, Schaltbild-Icon), **5-Minuten-Bildschirmschoner** und zweigeteiltem Störungs-/Ereignismenü (`EventsPage`). |
| **`settings_page.py`** | Eigenständiges Touch-Untermenü (`SettingsPage`) zur Live-Einstellung & dauerhaften Speicherung (`settings.json`) der 6 wichtigsten Grenzwerte & Timer (`Lüfter-Starttemperatur`, `Winterschutz-Schwelle`, `Entfeuchtung ab`, `Staunässe-Sperre`, `Bewässerung unter`, `Pumpen-Laufzeit`). |
| **`garden_view.py`** | Animierte 4-Jahreszeiten- & Tag-/Nacht-Gartenwelt (`GardenSceneView`, auch als **5-Minuten-Bildschirmschoner**): Holz-Gartenhütte mit PV-Dach, 48-V-Akku, **220-Liter-Regentonne**, Leitung zum grünen Folien-Tunnelgewächshaus mit 3 drehenden Lüftern (`L1–L3`) und Tropfbewässerung, Blumen, Schmetterlingen, Bienen, scheuem Reh, Sternenhimmel mit echter Mondphase sowie Malteser **Monti** mit eigener Hundehütte. |
| **`diagram_view.py`** | Animiertes Vollbild-Anlagenschema (`SystemDiagramView`) mit Live-Energieflüssen, Relais-Schaltzuständen, Einzel-Lüfter-Badges (`L1–L3`), **`TANK-SENSOR (220 L)`**, Timern und Live-Klima-Schaltgrund. |
| **`sim_panel.py`** | Interaktives Simulations-Labor (`SimControlWindow`, `SimEnvironment`) mit Jahreszeiten-Profilen, Live-Wetter-Umschaltung, **Regen-Simulation**, **220-Liter-Regentonnen-Regler (`0–220 L`)**, Klima-/Last-Schiebereglern und 7 Störfall-Tastern. |
| **`siegen_wetter.py`** | Asynchrone Hintergrund-Wetter- & Astronomie-Abfrage mit automatischer Standort-Erkennung, Open-Meteo WMO-Wettercodes, Sonnenauf-/-untergang und exakter Mondphasen-Berechnung. |
| **`events.log` / `settings.json`** | Persistentes Störungs-/Ereignisprotokoll (`[ALARM]` / `[INFO]`) sowie gespeicherte Benutzereinstellungen. |

---

## 3. Automatik- & Fail-Safe-Schutzlogik (Stand `build_0.27.3_STABLE`)

| Schutzfunktion | Auslösebedingung | Automatische Systemreaktion (Fail-Safe) | Freigabe / Reset |
| :--- | :--- | :--- | :--- |
| **Regentonnen-Trockenlauf** | Füllstand `< 10,0 L` (220-L-Fass) | Pumpe wird sofort abgeschaltet & gesperrt (`[ALARM]`), Schaltbild-Sensor & Tank in Gartenhütte zeigen `LEER`. | Tank nachfüllen (`>= 10 L`) + **`Störung quittieren`**. |
| **Herbst-/Winterschutz** | Innentemperatur `< winter_temp_c` (Standard `10,0 °C`) | Lüfter- & Bewässerungs-Automatik pausieren (`WINTER`), kein Fehlalarm durch hohe Außenluftfeuchte. | Automatisch ab `> Schwelle + 0,8 °C`. |
| **Zelt-Entfeuchtung** | Temp. `>= 12,0 °C` & Luftfeuchte `> dehum_start_pct` (`78 %`) | Gesunde Lüfter laufen auf `70 %` (ab `> 86 %` auf `100 %`) mit **30-s-Mindestlaufzeit**. | Automatisch bei `<= Schwelle − 10 %` nach Timer-Ablauf. |
| **Schimmelschutz** | Temp. `>= 12,0 °C` & Luftfeuchte `> 92 %` | Pumpe wird sofort gesperrt (`[ALARM]`), gesunde Lüfter laufen auf `100 %` Entfeuchtung. | Luftfeuchte sinkt + **`Störung quittieren`**. |
| **Staunässe-Schutz** | Bodenfeuchte `> soil_hi_pct` (`88 %`) | Sofortige Pumpen-Abschaltung & Hard-Sperre (AUTO & HAND) + `[ALARM]`. | Bodenfeuchte `< Schwelle` + **`Störung quittieren`**. |
| **Pumpen-Trockenlauf** | Kein Feuchteanstieg nach Gießlauf / Taster | Sofortige Pumpen-Sperre (AUTO & HAND) + `[ALARM]`. | **`Störung quittieren`** oder Labor-Reset. |
| **Einzel-Lüfter-Ausfall** | Blockade / Spannungsfehler an `L1`, `L2` oder `L3` | Betroffener Lüfter wird auf `0 %` gesperrt (`Rot`), übrige gesunde Lüfter laufen weiter + `[ALARM]`. | **`Störung quittieren`** oder Labor-Reset. |
| **Sensor-Ausfall** | Fehler beim Lesen der Klimasensoren / Taster | Pumpe & alle 3 Lüfter werden hart auf `0 %` abgeschaltet und gesperrt + `[ALARM]`. | Sensor OK + **`Störung quittieren`**. |
| **BMS-Frost-Schlafmodus** | Akkutemperatur `< 0,0 °C` | MPPT-Ladung `0 W`, Wechselrichter & 230-V-Steckdosen gesperrt (`FROST-SCHLAF`), nur DC-Wandler & Pi (`7 W`) aktiv. | Akkutemperatur `>= 0,0 °C` + Quittierung. |
| **BMS-Zell-Unterspannung** | Einzelpack-Spannung `< 11,5 V` | Sofortiger Lastabwurf und Verriegelung der 230-V-Steckdosen + `[ALARM]`. | Zellspannung erholt + Quittierung. |

---

## 3a. Aktueller Entwicklungsstand (`build_0.27.3_STABLE` – Verifiziert & Dokumentiert)
* **Code-Qualität & Dokumentation:** Alle **9 Python-Module** (`greenhouse.py`, `power.py`, `power_hw.py`, `greenhouse_gui.py`, `settings_page.py`, `garden_view.py`, `diagram_view.py`, `sim_panel.py`, `siegen_wetter.py`) sind fehlerfrei kompiliert, vollständig mit Docstrings dokumentiert (`100 %` Abdeckung) und durch einen 5-stufigen automatischen Systemtest verifiziert.
* **Realistische Umwelt-, PV- & Wetter-Simulation:**
  * **6 Wetterszenarios & 24-Minuten-Tagesverlauf:** `Klar`, `Regen`, `Nebel`, `Sturm`, `Gewitter` (mit Zickzack-Blitzen) sowie ein saisonaler Tages-Zufallsgenerator (`DAY_S = 1440 s`) mit realistischer Reaktion der 48-V-PV-Anlage, Luft- und Bodenfeuchte.
  * **Lebendige 4-Jahreszeiten-Gartenwelt & 5-Minuten-Bildschirmschoner:** Astronomische Dämmerung, Mondphasen, Berge & Bäume im Wind, Vögel auf dem Hüttendach, ein Reh im Wald/an der Hütte sowie **Malteser Monti** mit Wasser-/Fressnapf, Hundeknochen, 5 Ball-Ablageplätzen und selbstheilenden Buddel-Löchern.
  * **220-Liter-Regentonnen-Bilanz:** Durchgängige Kopplung zwischen Labor-Regler (`0–220 L`), Regen-Auffüllung (`6 m²` Dachfläche), Pumpen-Verbrauch, Schaltbild-Sensor (`TANK-SENSOR (220 L)`), Gartenhütten-Tank und Störmanagement (`Trockenlaufschutz < 10 L`).

---

## 4. Geplante Ausbaustufen & Roadmap (ab `build_0.27.3_STABLE`)
* **PIN-geschützte Admin-Ebene (`admin_page.py`):**
  * Touch-Ziffernblock (`4–6-stellige PIN`, als Hash gespeichert, automatisches Sperren nach Inaktivität).
  * Trennung zwischen normalem Nutzerbetrieb und geschützten System-Parametern (Sensor-Kalibrierung für `220-L`-Tank & Bodenfeuchte, kritische BMS-/Trockenlauf-Grenzwerte, PIN-Verwaltung).
* **Flexible Hardware-Abstraktionsschicht (Multi-System-Treiber):**
  * Austauschbare Treiber-Adapter, auswählbar direkt in der Admin-Ebene:
    * **Raspberry Pi Direktanschluss:** GPIO/PWM für Lüfter `L1–L3` & Pumpe, I2C für `BME280`, `ADS1115` und Ultraschall-/Drucksensor (`JSN-SR04T` / `A02YYUW`).
    * **Abgesetzter ESP32 / Mikrocontroller:** Anbindung über WLAN (`MQTT`) oder USB (`JSON-over-Serial`) für minimale Kabellängen im Garten.
    * **Solar- & BMS-Schnittstellen:** Unterstützung für `VE.Direct` (Victron MPPT/SmartShunt), `RS485/Modbus` und `JK-/DALY-BMS`.
* **System- & Worst-Case-Diagnose (`diag_page.py`):**
  * Plausibilitätsprüfung der Sensoren (Kabelbruch-, Kurzschluss- und Stillstandserkennung) sowie interaktiver Selbsttest vor der Hardware-Inbetriebnahme.

## Aktuelle Erweiterungen in `build_0.27.3_STABLE` (Revision 3 – Feinschliff)
* **Jahreszeiten- & Leistungs-Einstellungsmenü (`settings_page.py`):** 4 dauerhaft gespeicherte Profile (`Frühling`, `Hochsommer`, `Herbst`, `Winter`) für `Lüfter-Leistung (30–100 %)`, `Aktive Lüfter (0–3 Stk)`, `Entfeuchten ab/bis (Standard 76 % -> 65 %)`, `Lüfter-Starttemperatur`, `Winterschutz`, `Pumpen-Durchsatz`, `Pumpen-Laufzeit` und `Gieß-Intervall`.
* **Sanfte 4-Zustands-Lüfterkurve & Parkposition (`#b388eb`):** Weicher Sanftanlauf, Haltephase bis zur Ziel-Luftfeuchte und Sanftauslauf. Deaktivierte Lüfter (`0–3 Stk`) gehen in der Reihenfolge **Mitte (`L2`) -> Links (`L1`) -> Rechts (`L3`)** in **Helles Lila (`PARK`)** mit stillstehenden Rotorblättern (kollisionsfrei zum **Orange** des Handbetriebs).
* **Simulations-Labor mit 14-Stufen-Geschwindigkeitsregler (`sim_panel.py`):** Gemeinsame `Tageszeit-Steuerung`-Box mit blauem `Auto-Tag`-Button, Live-Anzeige der echten Tageslänge (`1 Tag = 24.0 min` bis `8.0 min`, Faktor `1.00×` bis `3.00×`), ausgegrauten Live-/Auto-Reglern und dynamischen Tages-Wetterfronten.
* **Dreistufiges Alarmsystem (`greenhouse_gui.py`):** Blinkendes Warndreieck bei allen Störungen, dezenter **Zweiklang-Alarmton (`880 Hz -> 660 Hz`)** alle 5 Live- bzw. Simulations-Minuten, **rot pulsierender Gesamthintergrund** über alle 4 Hauptmenüs bei Pumpen-Trockenlauf (`< 10 L`) oder BMS Zell-Unterspannung (`< 11,5 V`) sowie automatische Protokollierung aller Wetter-, Lüfter- und Pumpen-Laufwerte im Ereignisspeicher.

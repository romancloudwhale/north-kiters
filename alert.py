# Runs every 15 minutes.
# 1) Live: when the Angelochori station measures THRESHOLD+ knots now (08:00-20:00), one ntfy
#    notification per day.
# 2) Forecast, once per hour: one ntfy notification when the multi-model forecast reaches the
#    threshold at a spot tomorrow, about 24 hours before the wind starts.
# A spot or source that fails is skipped and logged; it never fails the whole run.
import datetime as dt
import json
import math
import pathlib
import sys
import time
import urllib.parse
import urllib.request

TOPIC = "spots-anemou-7kq2mx9f"
SITE = "https://romancloudwhale.github.io/north-kiters/"
THRESHOLD = 15  # knots, compared the way the page displays it (rounded)
STATE = pathlib.Path("state.json")

SPOTS = [
    ("kleidi", "Κλειδί", 40.490, 22.690),
    ("alykes", "Αλυκές Κίτρους", 40.362, 22.638),
    ("epanomi", "Επανομή", 40.407, 22.896),
    ("aggelohori", "Αγγελοχώρι", 40.502, 22.817),
    ("agiosmamas", "Άγιος Μάμας", 40.240, 23.330),
    ("vourvourou", "Βουρβουρού", 40.193, 23.808),
    ("stomio", "Στόμιο", 39.862, 22.775),
]
MODELS = ["ecmwf_ifs", "gfs_seamless", "icon_seamless", "meteofrance_seamless",
          "ukmo_seamless", "gem_seamless", "jma_seamless", "cma_grapes_global"]
DAYS = ["Δευ", "Τρί", "Τετ", "Πέμ", "Παρ", "Σάβ", "Κυρ"]
COMPASS = ["Β", "ΒΑ", "Α", "ΝΑ", "Ν", "ΝΔ", "Δ", "ΒΔ"]


STATION_URL = "https://active-sport-club.eu/wp-content/uploads/weather-station/realtime.txt"
STATION_PAGE = "https://active-sport-club.eu/live-angelochori/"
DIR_GR = {"N": "Β", "NNE": "ΒΒΑ", "NE": "ΒΑ", "ENE": "ΑΒΑ", "E": "Α", "ESE": "ΑΝΑ", "SE": "ΝΑ", "SSE": "ΝΝΑ",
          "S": "Ν", "SSW": "ΝΝΔ", "SW": "ΝΔ", "WSW": "ΔΝΔ", "W": "Δ", "WNW": "ΔΒΔ", "NW": "ΒΔ", "NNW": "ΒΒΔ"}


def get(url, timeout=60, tries=3):
    """GET with retries; the shared GitHub runners are sometimes refused or time out."""
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "north-kiters-alert"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - any network error: wait and retry
            if n == tries - 1:
                raise
            print(f"retry {n + 1} for {url[:60]}...: {e}")
            time.sleep(10 * (n + 1))


def fetch(lat, lon):
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "models": ",".join(MODELS), "wind_speed_unit": "kn",
        "timezone": "Europe/Athens", "forecast_days": 5, "cell_selection": "sea",
    })
    return json.loads(get("https://api.open-meteo.com/v1/forecast?" + query))


def athens_now():
    """Current Athens time, without tz libraries: EU summer time rule."""
    utc = dt.datetime.now(dt.timezone.utc)
    y = utc.year
    start = max(dt.datetime(y, 3, d, 1, tzinfo=dt.timezone.utc) for d in range(25, 32) if dt.date(y, 3, d).weekday() == 6)
    end = max(dt.datetime(y, 10, d, 1, tzinfo=dt.timezone.utc) for d in range(25, 32) if dt.date(y, 10, d).weekday() == 6)
    return (utc + dt.timedelta(hours=3 if start <= utc < end else 2)).replace(tzinfo=None)


def live_check(sent, now):
    """Angelochori station (Cumulus realtime.txt). Returns (message, key) or None."""
    f = get(STATION_URL, timeout=30).decode("utf-8", "replace").split()
    if len(f) < 52 or f[13] != "kts":
        print("station: unexpected format")
        return None
    obs = dt.datetime.strptime(f"{f[0]} {f[1]}", "%d/%m/%y %H:%M:%S")
    age = (now - obs).total_seconds() / 60
    wind, gust = float(f[5]), float(f[40])
    direction = DIR_GR.get(f[51], "")
    print(f"station {obs:%H:%M} ({age:.0f} min old): {wind} kn, gust {gust}, {f[51]}")
    key = f"aggelohori|{now:%Y-%m-%d}|live"
    if age > 30 or not 8 <= now.hour <= 20 or shown(wind) < THRESHOLD or key in sent:
        return None
    msg = f"Αγγελοχώρι ΤΩΡΑ: {shown(wind)} κόμβοι {direction}, ριπή {shown(gust)} (live σταθμός, {obs:%H:%M})"
    return msg, key


def ntfy(title, message, click):
    body = json.dumps({"topic": TOPIC, "title": title, "message": message,
                       "tags": ["dash"], "priority": 4, "click": click}).encode("utf-8")
    req = urllib.request.Request("https://ntfy.sh/", data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def hour_mean(hourly, i):
    """Mean speed and vector-mean direction across models, or None with under 3 models."""
    speeds, x, y = [], 0.0, 0.0
    for m in MODELS:
        s = (hourly.get("wind_speed_10m_" + m) or [None] * (i + 1))[i]
        d = (hourly.get("wind_direction_10m_" + m) or [None] * (i + 1))[i]
        if s is None or d is None:
            continue
        speeds.append(s)
        x += s * math.sin(math.radians(d))
        y += s * math.cos(math.radians(d))
    if len(speeds) < 3:
        return None
    return sum(speeds) / len(speeds), (math.degrees(math.atan2(x, y)) + 360) % 360


def shown(v):
    return math.floor(v + 0.5)


def main(dry):
    state = json.loads(STATE.read_text("utf-8")) if STATE.exists() else {}
    sent = state.get("sent", {})
    now_local = athens_now()
    today = now_local.strftime("%Y-%m-%d")
    hour_key = now_local.strftime("%Y-%m-%dT%H")

    # 1) Live station, every run.
    try:
        live = live_check(sent, now_local)
    except Exception as e:  # noqa: BLE001
        print(f"station failed: {e}")
        live = None
    if live:
        print(live[0])
        if not dry:
            try:
                ntfy(f"Αγγελοχώρι: φυσάει {THRESHOLD}+ κόμβους τώρα", live[0], STATION_PAGE)
                sent[live[1]] = 1
            except Exception as e:  # noqa: BLE001
                print(f"ntfy failed: {e}")

    # 2) Forecast, once per hour (the workflow runs every 15 minutes).
    found = []
    run_forecast = dry or state.get("forecastHour") != hour_key
    for sid, name, lat, lon in (SPOTS if run_forecast else []):
        try:
            data = fetch(lat, lon)
        except Exception as e:  # noqa: BLE001
            print(f"{sid}: forecast failed, skipped: {e}")
            continue
        now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=data["utc_offset_seconds"])
        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        limit = (now + dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:00")
        best, first = None, None
        for i, t in enumerate(data["hourly"]["time"]):
            hour = int(t[11:13])
            if t[:10] != tomorrow or hour < 8 or hour > 20:
                continue
            r = hour_mean(data["hourly"], i)
            if not r:
                continue
            if first is None and shown(r[0]) >= THRESHOLD:
                first = t
            if best is None or r[0] > best[0]:
                best = (r[0], r[1], t[11:16])
        # Announce tomorrow only, once its first windy hour is 24 hours away; nothing late at night.
        key = f"{sid}|{tomorrow}|24h"
        if first and first <= limit and now.hour <= 21 and key not in sent:
            found.append((tomorrow, name, shown(best[0]), COMPASS[round(best[1] / 45) % 8], best[2], key))

    found.sort()
    lines, last = [], None
    for day, name, kn, compass, at, _ in found:
        if day != last:
            d = dt.date.fromisoformat(day)
            lines.append(f"{DAYS[d.weekday()]} {d.day}/{d.month}:")
            last = day
        lines.append(f"  {name}: {kn} κόμβοι {compass}, {at}")
    print("\n".join(lines) if lines else "No new alerts")

    if dry:
        return
    if lines:
        try:
            ntfy(f"Αύριο άνεμος {THRESHOLD}+ κόμβοι", "\n".join(lines), SITE)
            for item in found:
                sent[item[5]] = 1
        except Exception as e:  # noqa: BLE001
            print(f"ntfy failed: {e}")
    new_state = {"sent": {k: 1 for k in sent if k.split("|")[1] >= today}, "checked": today,
                 "forecastHour": hour_key if run_forecast else state.get("forecastHour")}
    STATE.write_text(json.dumps(new_state, indent=0, sort_keys=True) + "\n", "utf-8")


if __name__ == "__main__":
    main("--dry" in sys.argv)

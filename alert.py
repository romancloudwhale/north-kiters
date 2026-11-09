# Hourly check: sends one ntfy notification when the multi-model forecast
# reaches the threshold at a spot tomorrow, about 24 hours before the wind starts.
import datetime as dt
import json
import math
import pathlib
import sys
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


def fetch(lat, lon):
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "models": ",".join(MODELS), "wind_speed_unit": "kn",
        "timezone": "Europe/Athens", "forecast_days": 5, "cell_selection": "sea",
    })
    with urllib.request.urlopen("https://api.open-meteo.com/v1/forecast?" + query, timeout=60) as r:
        return json.load(r)


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
    found, today = [], None
    for sid, name, lat, lon in SPOTS:
        data = fetch(lat, lon)
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
        body = json.dumps({"topic": TOPIC, "title": f"Αύριο άνεμος {THRESHOLD}+ κόμβοι", "message": "\n".join(lines),
                           "tags": ["dash"], "priority": 4, "click": SITE}).encode("utf-8")
        req = urllib.request.Request("https://ntfy.sh/", data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        for item in found:
            sent[item[5]] = 1
    state = {"sent": {k: 1 for k in sent if k.split("|")[1] >= today}, "checked": today}
    STATE.write_text(json.dumps(state, indent=0, sort_keys=True) + "\n", "utf-8")


if __name__ == "__main__":
    main("--dry" in sys.argv)

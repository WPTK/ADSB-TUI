import curses
import json
import math
import time
import requests
import pandas as pd
from datetime import datetime
import logging

# Logging setup
logging.basicConfig(filename="adsb_tracker.log", level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration (can be loaded dynamically)
TRACKER_LAT = 0.0  # Replace with your latitude
TRACKER_LON = 0.0  # Replace with your longitude
DUMP1090_URL = "http://<DUMP1090_SERVER>/run/readsb/aircraft.json"  # Replace with your server URL
MASTER_CSV_PATH = "./master.csv"  # Path to the master.csv file
KM_TO_MI = 0.621371  # Conversion factor from kilometers to miles
RADIUS_LIMIT = 15  # Maximum radius in miles
PROXIMITY_ALERT = 5  # Proximity alert distance in miles
REFRESH_INTERVAL = 5  # Seconds between data refreshes

# Load owner data from CSV
def load_owner_data():
    try:
        owner_data = pd.read_csv(
            MASTER_CSV_PATH,
            usecols=["MODE S CODE HEX", "NAME"],
            dtype=str
        )
        owner_data["MODE S CODE HEX"] = owner_data["MODE S CODE HEX"].str.strip().str.upper()
        return dict(zip(owner_data["MODE S CODE HEX"], owner_data["NAME"]))
    except Exception as e:
        logging.error(f"Error loading owner data: {e}")
        return {}

# Calculate distance between two lat/lon points using the haversine formula
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c * KM_TO_MI  # Return distance in miles

# Determine direction based on heading
def get_direction(heading):
    directions = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW", "N"
    ]
    index = round(heading / 22.5) % 16
    return directions[index]

# Fetch data from dump1090
def fetch_aircraft_data(owner_data):
    try:
        response = requests.get(DUMP1090_URL, timeout=5)
        response.raise_for_status()
        data = response.json()
        tracker_data = []

        for aircraft in data.get("aircraft", []):
            lat = aircraft.get("lat")
            lon = aircraft.get("lon")
            heading = aircraft.get("track")

            if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                distance = haversine(TRACKER_LAT, TRACKER_LON, lat, lon)
                if 0 < distance <= RADIUS_LIMIT:
                    speed_mph = aircraft.get("gs", 0) * KM_TO_MI
                    hex_code = aircraft.get("hex", "N/A").upper()
                    owner = owner_data.get(hex_code, "Unknown")
                    direction = get_direction(heading) if heading is not None else "Unknown"

                    tracker_data.append({
                        "hex": hex_code,
                        "flight": aircraft.get("flight", "").strip() or "N/A",
                        "altitude": aircraft.get("alt_baro", 0) or "N/A",
                        "distance": distance,
                        "distance_display": f"{distance:.1f} mi",
                        "speed": f"{speed_mph:.1f} mph" if speed_mph else "N/A",
                        "owner": owner,
                        "heading": f"{heading:.0f}" if heading is not None else "N/A",
                        "direction": direction,
                        "lat": lat,
                        "lon": lon,
                    })
        return tracker_data
    except Exception as e:
        logging.error(f"Error fetching aircraft data: {e}")
        return []

# Modularized Header Display
def display_header(stdscr, max_width):
    stdscr.addstr(0, 0, "+" + "-" * (max_width - 2) + "+")
    stdscr.addstr(1, 0, "| ADS-B Tracker TUI".ljust(max_width - 1) + "|")
    stdscr.addstr(2, 0, "| Last Update: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S').ljust(max_width - 18) + "|")
    stdscr.addstr(3, 0, "+" + "-" * (max_width - 2) + "+")
    stdscr.addstr(4, 0, f"| {'Flight':<10} | {'Hex':<8} | {'Altitude':<10} | {'Speed':<10} | {'Distance':<10} | {'Owner':<30} | {'Heading':<8} | {'Direction':<10} | {'Alert':<8} |")
    stdscr.addstr(5, 0, "+" + "-" * (max_width - 2) + "+")

# Modularized Footer Display
def display_footer(stdscr, row, max_width, seen_aircraft):
    stdscr.addstr(row, 0, "+" + "-" * (max_width - 2) + "+")
    stdscr.addstr(row + 1, 0, f"Total Unique Aircraft Tracked: {len(seen_aircraft)}".ljust(max_width - 1))

# Main TUI function
def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)

    owner_data = load_owner_data()
    seen_aircraft = set()
    last_fetch_time = datetime.now()
    tracked_aircraft = []

    while True:
        stdscr.clear()

        # Get terminal dimensions
        max_height, max_width = stdscr.getmaxyx()

        # Limit rows dynamically to avoid writing out of bounds
        max_display_rows = max_height - 8
        if max_display_rows < 1:
            max_display_rows = 1

        # Refresh aircraft data
        if (datetime.now() - last_fetch_time).total_seconds() > REFRESH_INTERVAL:
            tracked_aircraft = fetch_aircraft_data(owner_data)
            for aircraft in tracked_aircraft:
                if "hex" in aircraft:
                    seen_aircraft.add(aircraft["hex"])
            last_fetch_time = datetime.now()

        # Sort aircraft and identify proximity alerts
        tracked_aircraft.sort(key=lambda x: x["distance"])
        proximity_alerts = [a for a in tracked_aircraft if a["distance"] <= PROXIMITY_ALERT]

        # Display header
        display_header(stdscr, max_width)

        # Aircraft List
        row = 6
        for idx, aircraft in enumerate(tracked_aircraft[:max_display_rows]):
            flight = aircraft.get("flight", "N/A")[:10]
            hex_code = aircraft.get("hex", "N/A")[:8]
            altitude = str(aircraft.get("altitude", "N/A"))[:10]
            speed = aircraft.get("speed", "N/A")[:10]
            distance = aircraft.get("distance_display", "N/A")[:10]
            owner = aircraft.get("owner", "Unknown")[:30]
            heading = aircraft.get("heading", "N/A")[:8]
            direction = aircraft.get("direction", "N/A")[:10]
            alert = "YES" if aircraft in proximity_alerts else ""

            line = f"| {flight:<10} | {hex_code:<8} | {altitude:<10} | {speed:<10} | {distance:<10} | {owner:<30} | {heading:<8} | {direction:<10} | {alert:<8} |"
            try:
                if alert == "YES":
                    stdscr.addstr(row, 0, line[:max_width - 1], curses.A_BOLD)
                else:
                    stdscr.addstr(row, 0, line[:max_width - 1])
            except curses.error:
                break
            row += 1

        # Display footer
        display_footer(stdscr, row, max_width, seen_aircraft)

        # Refresh display
        stdscr.refresh()

        # Exit on 'q'
        key = stdscr.getch()
        if key == ord('q'):
            break

        time.sleep(0.1)

if __name__ == "__main__":
    curses.wrapper(main)

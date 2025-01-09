![image](https://github.com/user-attachments/assets/5cebeb98-304f-411b-b949-9e3c5add8b48)

ADSB TUI is a Python-based terminal interface that connects to a `dump1090` or compatible ADS-B receiver. It tracks aircraft in real-time and displays detailed information, such as flight number, altitude, distance, speed, heading, and direction, along with owner data fetched from a `master.csv` file.

---

## **Features**
- **Real-Time Tracking**: Monitor aircraft within a specified radius.
- **Key Metrics**:
  - Flight Number
  - Mode S Hex Code
  - Altitude (feet)
  - Ground Speed (mph)
  - Distance from Tracker (miles)
  - Aircraft Owner (via FAA `master.csv`)
  - Heading (degrees) and Direction (e.g., N, NE, SW)
  - Proximity Alerts (very basic, it just adds "YES" to the alert tab if it's going to fly over your house. 
- **Customizable**: Adjust the radius, proximity alert distance, refresh rate, and more.
- **Retro TUI Style**: Classic terminal-based user interface.

---

## **Getting Started**

### **Prerequisites**
1. **Python**: Install Python 3.6 or later.
2. **Libraries**:
   ```bash
   pip install pandas requests curses
   ```
   For Windows:
   ```bash
   pip install windows-curses
   ```

3. **dump1090**: Ensure you have a `dump1090` server running and accessible (e.g., [dump1090-fa](https://github.com/flightaware/dump1090)).

4. **FAA Aircraft Master Database** (Optional): Download and convert the FAA master file to CSV.

---

### **Installation**
1. Clone this repository:
   ```bash
   git clone https://github.com/your-repo/adsb-tracker-tui.git
   cd adsb-tracker-tui
   ```

2. Set up the `master.csv` file:
   - **Download FAA Aircraft Data**:
     1. Visit the [FAA database page](https://registry.faa.gov/aircraftinquiry/).
     2. Download the `Aircraft Registration Database` (a `.txt` file).

   - **Convert to CSV**:
     Use a tool like Excel or Python to convert the `.txt` file to a `.csv` with the following columns:
     - `MODE S CODE HEX`
     - `NAME`
     ```python
     import pandas as pd
     df = pd.read_csv("MASTER.txt", delimiter="|")  # Adjust delimiter as necessary
     df.to_csv("master.csv", index=False)
     ```

3. Edit `adsb_tracker.py`:
   - Update the following configuration variables:
     ```python
     TRACKER_LAT = 37.7749  # Your latitude
     TRACKER_LON = -122.4194  # Your longitude
     DUMP1090_URL = "http://192.168.1.100/run/readsb/aircraft.json"  # Your dump1090 server
     MASTER_CSV_PATH = "./master.csv"  # Path to your master.csv
     ```

4. Run the script:
   ```bash
   python adsb_tracker.py
   ```

---

### **Common File Paths for `aircraft.json`**
Depending on your setup, the `aircraft.json` file may be located in one of the following paths:
- `/run/readsb/aircraft.json`
- `/run/adsbexchange-feed/aircraft.json`
- `/run/theairtraffic-feed/aircraft.json`
- `/run/airplanes-feed/aircraft.json`
- `/run/adsblol-feed/aircraft.json`
- `/run/adsbfi-feed/aircraft.json`

Ensure the `DUMP1090_URL` variable is set to the correct file path.

---

### **File Permissions**
If your script is running on a machine separate from the `dump1090` server, ensure the machine running the script can access the `aircraft.json` file over the network.

1. **SSH into the `dump1090` server**:
   ```bash
   ssh user@<dump1090-server-ip>
   ```

2. Verify the `aircraft.json` file exists and is accessible:
   ```bash
   ls /run/readsb/aircraft.json
   ```

3. Adjust permissions to allow network access:
   ```bash
   sudo chmod 644 /run/readsb/aircraft.json
   sudo chown <user>:<group> /run/readsb/aircraft.json
   ```

4. Ensure your `DUMP1090_URL` points to the correct server and file path, e.g.,
   ```
   DUMP1090_URL = "http://<dump1090-server-ip>/run/readsb/aircraft.json"
   ```
5. You may need to restart a few processes after making these changes. Note: your `dump1090` may have a different name, like `dump1090-fa`
   ```bash
   sudo systemctl restart dump1090
   sudo systemctl restart readsb
   sudo systemctl restart lighttpd
   ```
   

---

### **Customization**

#### **Radius and Proximity**
- Adjust the radius for tracking and proximity alerts:
  ```python
  RADIUS_LIMIT = 20  # Radius in miles
  PROXIMITY_ALERT = 3  # Alert distance in miles
  ```

#### **Refresh Rate**
- Update the refresh interval for data fetching:
  ```python
  REFRESH_INTERVAL = 10  # Refresh every 10 seconds
  ```

#### **Server URL / aircraft.json File Path**
- If using a different ADS-B tracking service, replace `DUMP1090_URL` with the appropriate endpoint. URLs and filepaths vary. 

---

### **Keyboard Shortcuts**
- **`q`**: Quit the application.
- This probably didn't need it's own section. 

---

## **Logging**
- Errors and unique aircraft logs are saved in `adsb_tracker.log`.
- I should probably limit the file size of this. 

---

## **Known Limitations**
- Assumes a `dump1090`-compatible server for aircraft data.
- FAA master file must be manually downloaded and converted to CSV.
- Does not work on non-US aircraft (only have access to those registered with the FAA). Working on this -- suggestions appreciated
- Does not work on military, LADD, or PIA aircraft. 
- Aircraft type requires access to an API, that costs money. I don't know of any free options out there, or any database that I can crossreference the `hex code` with.
- 

---

## **Contributing**
Contributions are welcome!

---

## **License**
This project is licensed under [Creative Commons Zero (CC0)](https://creativecommons.org/publicdomain/zero/1.0/). This means you can use, modify, and distribute it freely without attribution or restrictions.


import json
import shutil
import subprocess


class Wifi:
    def __init__(self):
        self.D = None
        with open("networks.json", "r") as f:
            self.networks = json.load(f)
        self.hostname = "DimensionDoors"
        self.ssid = ''
        self.password = ''
        self.ap_ssid = self.hostname
        self.ap_password = "everyversionofyou"
        self._nmcli = shutil.which("nmcli")
        self.initialize()

    def _run_nmcli(self, args):
        if not self._nmcli:
            return ""
        try:
            result = subprocess.run(
                [self._nmcli, *args],
                check=False,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    def initialize(self):
        self.check_wifis()
        if not self.connect():
            self.start_ap_mode()

    def connect(self):
        if not self._nmcli:
            print("nmcli not found. Skipping automatic Wi-Fi management.")
            return False

        if not self.ssid:
            return False

        print(f"Connecting to network '{self.ssid}'...")
        out = self._run_nmcli(["device", "wifi", "connect", self.ssid, "password", self.password])
        if out:
            print(out)

        status = self._run_nmcli(["-t", "-f", "ACTIVE,SSID", "connection", "show", "--active"])
        connected = any(line.startswith("yes:") and self.ssid in line for line in status.splitlines())
        if connected:
            print(f"Connected to '{self.ssid}'")
        return connected

    def check_wifis(self):
        if not self._nmcli:
            return
        print("Scanning available networks.....")
        scan = self._run_nmcli(["-t", "-f", "SSID", "device", "wifi", "list"])
        wifi_ssids = {line.strip() for line in scan.splitlines() if line.strip()}
        for ssid, password in self.networks.items():
            if ssid in wifi_ssids:
                self.ssid = ssid
                self.password = password
                return

    def start_ap_mode(self):
        if not self._nmcli:
            print("nmcli not found. Cannot start AP mode automatically.")
            return
        out = self._run_nmcli([
            "device",
            "wifi",
            "hotspot",
            "ssid",
            self.ap_ssid,
            "password",
            self.ap_password,
        ])
        print(out or f"Access Point requested. SSID: {self.ap_ssid}")

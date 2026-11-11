"""Download the public wind turbine SCADA dataset used in the demo.

Source: a 3.6 MW wind turbine in Turkey, 10-minute SCADA readings for 2018
(originally published on Kaggle as "Wind Turbine Scada Dataset", mirrored on GitHub).
"""
from pathlib import Path
import urllib.request

URL = ("https://raw.githubusercontent.com/dominodatalab/"
       "reference-project-wind-turbine-scada/main/data/T1.csv")
DEST = Path(__file__).parent / "wind_turbine_scada.csv"


def main() -> None:
    if DEST.exists():
        print(f"Already downloaded: {DEST}")
        return
    print(f"Downloading {URL} ...")
    urllib.request.urlretrieve(URL, DEST)
    print(f"Saved to {DEST} ({DEST.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

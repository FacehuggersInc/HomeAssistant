from __future__ import annotations
from pathlib import Path
import random
from datetime import datetime

import openmeteo_requests
import requests_cache
import pandas as pd
from retry_requests import retry


class OpenMeteoAPI:
    def __init__(self, plugin, client):
        self.plugin = plugin
        self.client = client

        cache_path = str(Path(self.client.DATAPATH) / "openmeteoapi_requests.cache")
        self.cache_session  = requests_cache.CachedSession(cache_path, expire_after=3600)
        self.retry_session  = retry(self.cache_session, retries=5, backoff_factor=0.2)
        self.openmeteo      = openmeteo_requests.Client(session=self.retry_session)

        self.BASE = "https://api.open-meteo.com/v1/forecast"

        self.PARAMS = {
            "hourly": {
                "latitude":         self.plugin.settings.weather.latitude.value,
                "longitude":        self.plugin.settings.weather.longitude.value,
                "hourly":           ["temperature_2m", "apparent_temperature"],
                "temperature_unit": "fahrenheit",
                "timezone":         self.plugin.settings.weather.timezone.value,
            },
            "current": {
                "latitude":           self.plugin.settings.weather.latitude.value,
                "longitude":          self.plugin.settings.weather.longitude.value,
                "current":            [
                    "temperature_2m", "is_day", "precipitation", "rain",
                    "showers", "snowfall", "cloud_cover",
                    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
                ],
                "temperature_unit":   "fahrenheit",
                "wind_speed_unit":    "mph",
                "precipitation_unit": "inch",
                "timeformat":         "unixtime",
                "timezone":           self.plugin.settings.weather.timezone.value,
            },
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def get_beaufort_scale(self, wind_speed: float) -> int:
        thresholds = [0, 3, 7, 12, 18, 24, 31, 38, 46, 54, 63, 72]
        for scale, threshold in enumerate(thresholds):
            if wind_speed <= threshold:
                return scale
        return 12

    def get_icon(self, data: dict) -> str:
        """Return an MDI icon name for the current weather conditions."""
        day        = data["is_day"] > 0
        showers    = data["showers"]  > 0
        raining    = data["rain"]     > 0
        snowing    = data["snowfall"] > 0
        wind_scale = self.get_beaufort_scale(data["wind_speed_10m"])
        windy      = wind_scale >= 4
        cloud_cover = showers or raining or data["cloud_cover"] > 0

        if windy:
            if (raining or showers) and snowing: return "mdi.weather-snowy-rainy"
            elif snowing:                         return "mdi.weather-snowy"
            elif showers:                         return "mdi.weather-pouring"
            elif raining:                         return "mdi.weather-rainy"
            elif cloud_cover:
                return "mdi.weather-windy" if wind_scale < 6 else "mdi.weather-windy-variant"
            else:
                return "mdi.weather-windy" if wind_scale < 6 else "mdi.weather-windy-variant"
        else:
            if not cloud_cover:
                if day:
                    return "mdi.weather-sunny"
                else:
                    return random.choice(["mdi.weather-night", "mdi.weather-night-partly-cloudy"])
            else:
                if (raining or showers) and snowing: return "mdi.weather-snowy-rainy"
                elif snowing:                         return "mdi.weather-snowy"
                elif showers:                         return "mdi.weather-pouring"
                elif raining:                         return "mdi.weather-rainy"
                elif cloud_cover and day:             return "mdi.weather-partly-cloudy"
                elif cloud_cover:                     return "mdi.weather-night-partly-cloudy"
                else:                                 return "mdi.weather-cloudy" 

    # ── API calls ─────────────────────────────────────────────────────────────

    def _sync_params(self) -> None:
        for key in ("hourly", "current"):
            self.PARAMS[key]["latitude"]  = self.plugin.settings.weather.latitude.value
            self.PARAMS[key]["longitude"] = self.plugin.settings.weather.longitude.value
            self.PARAMS[key]["timezone"]  = self.plugin.settings.weather.timezone.value

    def get_current_weather(self) -> dict | None:
        self._sync_params()
        try:
            responses = self.openmeteo.weather_api(self.BASE, params=self.PARAMS["current"])
            if not responses:
                return None
            data    = {}
            current = responses[0].Current()
            for i, item in enumerate(self.PARAMS["current"]["current"]):
                data[item] = current.Variables(i).Value()
            return data
        except Exception as e:
            self.client.log("error", f"[OpenMeteoAPI] get_current_weather failed: {e}")
            return None

    def get_current_forecast(self) -> dict | None:
        self._sync_params()
        try:
            responses = self.openmeteo.weather_api(self.BASE, params=self.PARAMS["hourly"])
            if not responses:
                return None

            response = responses[0]
            if not response or not response.Hourly():
                return None

            hourly = response.Hourly()
            hourly_data = {
                "date": pd.date_range(
                    start     = pd.to_datetime(hourly.Time(), unit="s", utc=True),
                    end       = pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
                    freq      = pd.Timedelta(seconds=hourly.Interval()),
                    inclusive = "left",
                ),
                "temperature_2m":        hourly.Variables(0).ValuesAsNumpy(),
                "apparent_temperature":  hourly.Variables(1).ValuesAsNumpy(),
            }
            df = pd.DataFrame(data=hourly_data)
            raw = df.to_dict(orient="dict")

            return_data = {}
            index       = 0
            last_date   = None
            now_hour    = datetime.now().hour

            for key in raw["date"]:
                if index == 7:
                    break
                ts: pd.Timestamp = raw["date"][key]
                if index == 0:
                    if str(ts.date()) != last_date and ts.hour == now_hour:
                        last_date = str(ts.date())
                        return_data[str(index)] = [int(raw["temperature_2m"][key]), str(ts.date())]
                        index += 1
                else:
                    if str(ts.date()) != last_date and ts.hour == 13:
                        last_date = str(ts.date())
                        return_data[str(index)] = [int(raw["temperature_2m"][key]), str(ts.date())]
                        index += 1

            forecast_path = Path(self.client.DATAPATH) / "forecast.json"
            self.client.dump(return_data, forecast_path)
            return return_data

        except Exception as e:
            self.client.log("error", f"[OpenMeteoAPI] get_current_forecast failed: {e}")
            return None
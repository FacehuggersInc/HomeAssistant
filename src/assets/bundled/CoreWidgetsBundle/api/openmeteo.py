from __future__ import annotations
from pathlib import Path
import random
from datetime import datetime

import openmeteo_requests
import requests_cache
import pandas as pd
from retry_requests import retry


class OpenMeteoAPI:
    def coordinates(self) -> tuple:
        """
        Where the panel thinks it is, as (latitude, longitude).

        Here so nothing else has to read this plugin's settings file. A
        plugin's settings are its own - `client.setting()` walks the CLIENT's
        tree and never reaches a plugin key, so a path like
        `corewidgetsbundle.weather.latitude.value` silently answers with the
        caller's default and the caller has no way to tell.
        """
        weather = self.plugin.settings.weather
        return float(weather.latitude.value or 0), float(weather.longitude.value or 0)

    def __init__(self, plugin, client):
        self.plugin = plugin
        self.client = client

        cache_path = str(Path(self.client.DATAPATH) / "openmeteoapi_requests.cache")
        self.cache_session  = requests_cache.CachedSession(cache_path, expire_after=3600)
        self.retry_session  = retry(self.cache_session, retries=5, backoff_factor=0.2)
        self.openmeteo      = openmeteo_requests.Client(session=self.retry_session)

        self.BASE = "https://api.open-meteo.com/v1/forecast"
        # A different host, not a different path. Air quality is its own
        # service on open-meteo and does not answer on the forecast one.
        self.AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"

        self.PARAMS = {
            "hourly": {
                "latitude":         self.plugin.settings.weather.latitude.value,
                "longitude":        self.plugin.settings.weather.longitude.value,
                "hourly":           ["temperature_2m", "apparent_temperature"],
                "temperature_unit": "fahrenheit",
                "timezone":         self.plugin.settings.weather.timezone.value,
            },
            # Its own block rather than more fields on "hourly". Both readers
            # of that one map the response by POSITION, so an extra variable
            # there relabels every series after it.
            "precipitation": {
                "latitude":           self.plugin.settings.weather.latitude.value,
                "longitude":          self.plugin.settings.weather.longitude.value,
                "hourly":             ["precipitation_probability",
                                       "precipitation",
                                       # Appended. Rain and snow are the same
                                       # question asked about different
                                       # weather, and answering "will it
                                       # snow" out of the rain total says
                                       # yes to a wet afternoon in October.
                                       "snowfall"],
                "precipitation_unit": "inch",
                "timezone":           self.plugin.settings.weather.timezone.value,
            },
            "daily": {
                "latitude":           self.plugin.settings.weather.latitude.value,
                "longitude":          self.plugin.settings.weather.longitude.value,
                "daily":              ["weather_code", "temperature_2m_max",
                                       "temperature_2m_min",
                                       "precipitation_probability_max"],
                "temperature_unit":   "fahrenheit",
                "forecast_days":      7,
                "timezone":           self.plugin.settings.weather.timezone.value,
            },
            "air": {
                "latitude":  self.plugin.settings.weather.latitude.value,
                "longitude": self.plugin.settings.weather.longitude.value,
                # Appended, never inserted - same positional mapping.
                "current":   ["us_aqi", "pm2_5", "pm10", "ozone",
                              "nitrogen_dioxide", "uv_index"],
                "timezone":  self.plugin.settings.weather.timezone.value,
            },
            "current": {
                "latitude":           self.plugin.settings.weather.latitude.value,
                "longitude":          self.plugin.settings.weather.longitude.value,
                "current":            [
                    "temperature_2m", "is_day", "precipitation", "rain",
                    "showers", "snowfall", "cloud_cover",
                    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
                    # Appended, never inserted: get_current_weather() maps the
                    # response by POSITION in this list, so putting a new field
                    # anywhere but the end silently relabels every one after it.
                    "weather_code",
                    # Asked for at last. The weather answer has read
                    # `apparent_temperature` and `relative_humidity_2m` since
                    # it was written, and neither was ever requested - so
                    # "Feels like" and "Humidity" resolved to None and were
                    # skipped by the loop that builds the lines. Two rows
                    # nobody had ever seen, failing in the quietest way there
                    # is.
                    "apparent_temperature", "relative_humidity_2m",
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

    def unit(self) -> str:
        """'fahrenheit' or 'celsius', from the setting."""
        try:
            value = str(self.plugin.settings.weather.units.value).strip().lower()
        except Exception:
            return "fahrenheit"
        return "celsius" if value.startswith("c") else "fahrenheit"

    def unit_symbol(self) -> str:
        """The letter to put after the degree sign."""
        return "C" if self.unit() == "celsius" else "F"

    def _sync_params(self) -> None:
        # Requested in the unit that is wanted rather than converted here:
        # open-meteo does it properly, including for apparent temperature, and
        # a conversion in two places is a rounding disagreement waiting to
        # happen.
        unit = self.unit()
        for key in ("hourly", "current", "daily"):
            self.PARAMS[key]["temperature_unit"] = unit
        # Location and timezone on every block, temperature only on the ones
        # that ask for a temperature. Setting `temperature_unit` on a request
        # with no temperature in it is not an error, but it is a claim about
        # the request that is not true.
        for key in self.PARAMS:
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

    def get_hourly_forecast(self, hours: int = 6) -> list | None:
        """
        [(datetime, temperature), ...] for the next `hours` hours.

        get_current_forecast() is a *daily* summary despite reading the hourly
        endpoint - it keeps one entry per day at 1pm. This returns the hours
        themselves, which is what an at-a-glance tile wants.
        """
        self._sync_params()
        try:
            responses = self.openmeteo.weather_api(self.BASE, params=self.PARAMS["hourly"])
            if not responses or not responses[0].Hourly():
                return None

            hourly = responses[0].Hourly()
            stamps = pd.date_range(
                start     = pd.to_datetime(hourly.Time(), unit="s", utc=True),
                end       = pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
                freq      = pd.Timedelta(seconds=hourly.Interval()),
                inclusive = "left",
            ).tz_convert(self.plugin.settings.weather.timezone.value)
            temps = hourly.Variables(0).ValuesAsNumpy()

            now = pd.Timestamp.now(tz=self.plugin.settings.weather.timezone.value)
            out = []
            for stamp, temp in zip(stamps, temps):
                if stamp < now:
                    continue
                out.append((stamp.to_pydatetime(), float(temp)))
                if len(out) >= hours:
                    break
            return out
        except Exception as e:
            self.client.log("error", f"[OpenMeteoAPI] get_hourly_forecast failed: {e}")
            return None

    # US EPA breakpoints, and the words the EPA puts against them. The bands
    # are what somebody actually wants: "58" means nothing to anyone who does
    # not already know where 58 sits.
    AQI_BANDS = (
        (50,  "good",                           "mdi.leaf"),
        (100, "moderate",                       "mdi.weather-hazy"),
        (150, "unhealthy for sensitive groups", "mdi.weather-fog"),
        (200, "unhealthy",                      "mdi.smog"),
        (300, "very unhealthy",                 "mdi.smog"),
    )

    def aqi_band(self, aqi: float) -> tuple:
        """(word, icon) for a US AQI number."""
        for ceiling, word, glyph in self.AQI_BANDS:
            if aqi <= ceiling:
                return word, glyph
        return "hazardous", "mdi.skull-outline"

    def get_air_quality(self) -> dict | None:
        """
        What is in the air now, or None.

        A separate service from the forecast, so it fails separately: a panel
        somewhere the air quality model does not cover still gets its
        weather, and asking about the air says so rather than saying nothing.
        """
        self._sync_params()
        try:
            responses = self.openmeteo.weather_api(self.AIR, params=self.PARAMS["air"])
            if not responses:
                return None
            current = responses[0].Current()
            data = {}
            for i, item in enumerate(self.PARAMS["air"]["current"]):
                value = current.Variables(i).Value()
                # NaN is what this endpoint returns for a pollutant it does
                # not model where the panel is - a real number-shaped answer
                # meaning "no answer", which formats as "nan" and reads as a
                # reading if it is not caught here.
                data[item] = None if value != value else float(value)
            return data
        except Exception as e:
            self.client.log("error", f"[OpenMeteoAPI] get_air_quality failed: {e}")
            return None

    # 16 points, because eight is not enough to be useful and thirty-two is
    # not something anybody says out loud.
    COMPASS = ("north", "north-northeast", "northeast", "east-northeast",
               "east", "east-southeast", "southeast", "south-southeast",
               "south", "south-southwest", "southwest", "west-southwest",
               "west", "west-northwest", "northwest", "north-northwest")

    def compass(self, degrees: float) -> str:
        """Which way the wind is coming FROM, in words."""
        try:
            index = int((float(degrees) % 360) / 22.5 + 0.5) % 16
        except (TypeError, ValueError):
            return ""
        return self.COMPASS[index]

    # What each Beaufort number is called. The scale is already computed for
    # the icon; naming it is what makes "12 mph" mean something.
    BEAUFORT_WORDS = (
        "calm", "light air", "a light breeze", "a gentle breeze",
        "a moderate breeze", "a fresh breeze", "a strong breeze",
        "near gale", "gale", "a severe gale", "storm force",
        "violent storm", "hurricane force",
    )

    def beaufort_word(self, wind_speed: float) -> str:
        try:
            return self.BEAUFORT_WORDS[self.get_beaufort_scale(float(wind_speed))]
        except (TypeError, ValueError, IndexError):
            return ""

    # WMO codes, which is what the daily endpoint reports the sky as. Grouped
    # rather than listed one per line: 61, 63 and 65 are the same weather at
    # three intensities, and a forecast row has no space to say which.
    WMO = {
        0: ("Clear", "mdi.weather-sunny"),
        1: ("Mostly clear", "mdi.weather-sunny"),
        2: ("Partly cloudy", "mdi.weather-partly-cloudy"),
        3: ("Overcast", "mdi.weather-cloudy"),
        45: ("Fog", "mdi.weather-fog"), 48: ("Freezing fog", "mdi.weather-fog"),
        51: ("Drizzle", "mdi.weather-rainy"), 53: ("Drizzle", "mdi.weather-rainy"),
        55: ("Drizzle", "mdi.weather-rainy"),
        56: ("Freezing drizzle", "mdi.weather-snowy-rainy"),
        57: ("Freezing drizzle", "mdi.weather-snowy-rainy"),
        61: ("Rain", "mdi.weather-rainy"), 63: ("Rain", "mdi.weather-rainy"),
        65: ("Heavy rain", "mdi.weather-pouring"),
        66: ("Freezing rain", "mdi.weather-snowy-rainy"),
        67: ("Freezing rain", "mdi.weather-snowy-rainy"),
        71: ("Snow", "mdi.weather-snowy"), 73: ("Snow", "mdi.weather-snowy"),
        75: ("Heavy snow", "mdi.weather-snowy-heavy"),
        77: ("Snow grains", "mdi.weather-snowy"),
        80: ("Showers", "mdi.weather-rainy"), 81: ("Showers", "mdi.weather-rainy"),
        82: ("Heavy showers", "mdi.weather-pouring"),
        85: ("Snow showers", "mdi.weather-snowy"),
        86: ("Snow showers", "mdi.weather-snowy"),
        95: ("Thunderstorms", "mdi.weather-lightning"),
        96: ("Thunderstorms", "mdi.weather-lightning-rainy"),
        99: ("Thunderstorms", "mdi.weather-lightning-rainy"),
    }

    def sky_from_code(self, code) -> tuple:
        """(words, icon) for a WMO weather code."""
        try:
            return self.WMO[int(code)]
        except (TypeError, ValueError, KeyError):
            return "", "mdi.weather-cloudy"

    # WHO/EPA bands. Below 3 there is nothing to do about it, which is worth
    # saying as plainly as the number.
    UV_BANDS = ((2, "low"), (5, "moderate"), (7, "high"), (10, "very high"))

    def uv_band(self, uv: float) -> str:
        for ceiling, word in self.UV_BANDS:
            if uv <= ceiling:
                return word
        return "extreme"

    def get_daily_forecast(self, days: int = 7) -> list | None:
        """
        [{day, code, high, low, chance}, ...] for the week.

        Separate from `get_current_forecast`, which reads the HOURLY endpoint
        and keeps whichever hour happens to be 1pm - a daily high is not the
        temperature at one o'clock, and on a day that peaks at four it is out
        by several degrees.
        """
        self._sync_params()
        try:
            self.PARAMS["daily"]["forecast_days"] = max(1, min(16, int(days)))
            responses = self.openmeteo.weather_api(self.BASE, params=self.PARAMS["daily"])
            if not responses or not responses[0].Daily():
                return None

            daily = responses[0].Daily()
            zone  = self.plugin.settings.weather.timezone.value
            stamps = pd.date_range(
                start     = pd.to_datetime(daily.Time(), unit="s", utc=True),
                end       = pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
                freq      = pd.Timedelta(seconds=daily.Interval()),
                inclusive = "left",
            ).tz_convert(zone)

            series = [daily.Variables(i).ValuesAsNumpy() for i in range(4)]

            def clean(value):
                return None if value != value else float(value)

            out = []
            for index, stamp in enumerate(stamps):
                out.append({
                    "day":    stamp.to_pydatetime(),
                    "code":   clean(series[0][index]),
                    "high":   clean(series[1][index]),
                    "low":    clean(series[2][index]),
                    "chance": clean(series[3][index]),
                })
            return out
        except Exception as e:
            self.client.log("error", f"[OpenMeteoAPI] get_daily_forecast failed: {e}")
            return None

    def get_precipitation_outlook(self, hours: int = 12) -> list | None:
        """
        [(datetime, chance %, amount, snow), ...] for the next `hours` hours.

        Probability is hourly and has no current value - there is no such
        thing as the chance of rain right now, only whether it is raining -
        so "will it rain later" cannot be answered from the current call at
        all. `snow` is the part of `amount` falling as snow, not a separate
        total on top of it.
        """
        self._sync_params()
        try:
            responses = self.openmeteo.weather_api(
                self.BASE, params=self.PARAMS["precipitation"])
            if not responses or not responses[0].Hourly():
                return None

            hourly = responses[0].Hourly()
            zone   = self.plugin.settings.weather.timezone.value
            stamps = pd.date_range(
                start     = pd.to_datetime(hourly.Time(), unit="s", utc=True),
                end       = pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
                freq      = pd.Timedelta(seconds=hourly.Interval()),
                inclusive = "left",
            ).tz_convert(zone)

            chances = hourly.Variables(0).ValuesAsNumpy()
            amounts = hourly.Variables(1).ValuesAsNumpy()
            snows   = hourly.Variables(2).ValuesAsNumpy()

            # From the top of the CURRENT hour, not from now. Dropping the
            # hour in progress at ten past means the answer to "will it rain
            # in the next hour" skips the hour being asked about.
            now = pd.Timestamp.now(tz=zone).floor("h")
            out = []
            for stamp, chance, amount, snow in zip(stamps, chances, amounts, snows):
                if stamp < now:
                    continue
                out.append((stamp.to_pydatetime(),
                            None if chance != chance else float(chance),
                            None if amount != amount else float(amount),
                            None if snow != snow else float(snow)))
                if len(out) >= hours:
                    break
            return out
        except Exception as e:
            self.client.log("error",
                            f"[OpenMeteoAPI] get_precipitation_outlook failed: {e}")
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
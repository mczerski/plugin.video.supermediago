"""
SupermediaGO / InsysGO API Client
===================================
Built from HAR capture of www.supermediago.pl

Real API base:  https://api-supermedia.app.insysgo.pl
Platform code:  www

Auth flow (confirmed from HAR):
  1. POST /v1/InsysGoAccount/Authenticate  → token (UUID string, ~24h)
  2. POST /v1/Devices/RegisterDevice       → register deviceKey
  3. GET  /v1/Player/AcquireContent        → MediaFiles with signed CDN URLs + CAP session

Stream delivery (confirmed from HAR):
  - CDN:      supermedia3.cf.insyscd.net  (JWT-signed URL, 24h expiry)
  - DASH MPD: .../wd/wigo-<channel>-fhd.mpd   (Type=9, Protection=4)
  - HLS m3u8: .../wh/wigo-<channel>-fhd.m3u8  (Type=2, Protection=5)
  - DRM:      Widevine + PlayReady via wigo.la.drm.cloud
  - CAP heartbeat required every ~60s: POST cap-ha.app.insysgo.pl/v1/CAP/Ping

Channel list flow (confirmed from HAR):
  1. POST /v1/EpgTile/FilterChannelTiles → list of {id, codename} tiles
  2. POST /v2/Tile/GetTiles              → full tile metadata (name, logos, categories)

EPG (confirmed from HAR):
  - GET  /v1/EpgTile/FilterNowOnTvTiles  → current programme on all channels
  - POST /v1/EpgTile/FilterProgramTiles  → schedule for date range
"""

import hashlib
import json
import os
import time
import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import xbmcaddon
import xbmcvfs

from .logging import log_info, log_error, log_warn, debug


ADDON = xbmcaddon.Addon()
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))


# ---------------------------------------------------------------------------
# Session / token cache
# ---------------------------------------------------------------------------


class _SessionCache:
    """Persists auth token and deviceKey between Kodi sessions."""

    _PATH = None

    @classmethod
    def _path(cls):
        if cls._PATH is None:
            if not xbmcvfs.exists(PROFILE):
                xbmcvfs.mkdirs(PROFILE)
            cls._PATH = os.path.join(PROFILE, "session.json")
        return cls._PATH

    @classmethod
    def load(cls):
        try:
            with xbmcvfs.File(cls._path()) as fh:
                raw = fh.read()
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    @classmethod
    def save(cls, data):
        try:
            with xbmcvfs.File(cls._path(), "w") as fh:
                fh.write(json.dumps(data))
        except Exception as ex:
            log_error("Session save failed: {}".format(ex))

    @classmethod
    def clear(cls):
        try:
            xbmcvfs.delete(cls._path())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class APIError(Exception):
    def __init__(self, message, code=0):
        super().__init__(message)
        self.code = code


class AuthError(APIError):
    pass


class DeviceError(APIError):
    pass


class NetworkError(APIError):
    pass


# ---------------------------------------------------------------------------
# Main API client
# ---------------------------------------------------------------------------


class InsysGoAPI:
    """
    Client for the InsysGO REST API as used by SupermediaGO.

    All endpoint paths, request bodies and response field names are taken
    directly from the HAR capture of the live production service.
    """

    # Confirmed from HAR: all API calls go to this host
    _DEFAULT_BASE = "https://api-supermedia.app.insysgo.pl"
    _CAP_URL = "https://cap-ha.app.insysgo.pl/v1/CAP/Ping"

    # Media type codes found in AcquireContent response
    FORMAT_TYPE_HLS = 2
    FORMAT_TYPE_DASH = 9

    def __init__(self):
        self._base = ADDON.getSetting("api_url").rstrip("/") or self._DEFAULT_BASE
        self._platform = ADDON.getSetting("platform_codename") or "www"

        # Build a stable device key from the Kodi machine name
        # Must be 32 hex chars – matches format seen in HAR (c13d18266a8f47b86e41566e6a7ab0ba)
        machine_seed = ADDON.getSetting("device_name") or "Kodi"
        self._device_key = hashlib.md5(("supermediago-kodi-" + machine_seed).encode()).hexdigest()

        # Session state
        cache = _SessionCache.load()
        self._token = cache.get("token", "")
        self._token_expiry = cache.get("token_expiry", 0)  # unix timestamp
        self._user_id = cache.get("user_id", 0)
        self._device_registered = cache.get("device_registered", False)

        # HTTP session with retry
        self._session = requests.Session()
        adapter = HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504]))
        self._session.mount("https://", adapter)
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json;charset=utf-8",
                "Origin": "https://www.supermediago.pl",
                "Referer": "https://www.supermediago.pl/",
                "User-Agent": "Mozilla/5.0 (compatible; Kodi/{})".format(ADDON.getAddonInfo("version")),
            }
        )

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _common_params(self, authenticated=True):
        """Build the standard query parameters sent on every request."""
        params = {
            "platformCodename": self._platform,
        }
        if authenticated and self._token:
            params["token"] = self._token
        return params

    def _get(self, path, params=None, authenticated=True):
        p = self._common_params(authenticated)
        if params:
            p.update(params)
        url = self._base + path
        debug("GET {} params={}".format(url, p))
        try:
            r = self._session.get(url, params=p, timeout=15)
            return self._handle(r)
        except requests.RequestException as ex:
            raise NetworkError(str(ex))

    def _post(self, path, body, raw_url=None):
        url = raw_url or (self._base + path)
        debug("POST {} body={}".format(url, str(body)[:200]))
        try:
            r = self._session.post(url, json=body, timeout=15)
            return self._handle(r)
        except requests.RequestException as ex:
            raise NetworkError(str(ex))

    def _delete(self, path, params=None):
        p = self._common_params()
        if params:
            p.update(params)
        url = self._base + path
        try:
            r = self._session.delete(url, params=p, timeout=10)
            return r.status_code in (200, 204)
        except requests.RequestException as ex:
            raise NetworkError(str(ex))

    @staticmethod
    def _handle(resp):
        debug("HTTP {} {}".format(resp.status_code, resp.url))
        if resp.status_code == 401:
            raise AuthError("Unauthorised (401)")
        if resp.status_code == 403:
            raise DeviceError("Device forbidden (403)")
        try:
            resp.raise_for_status()
        except requests.HTTPError as ex:
            raise APIError(str(ex), resp.status_code)
        try:
            return resp.json()
        except ValueError:
            return {}

    # -------------------------------------------------------------------------
    # Public: Auth
    # -------------------------------------------------------------------------

    def is_logged_in(self):
        return bool(self._token) and time.time() < self._token_expiry

    def login(self, username, password):
        """
        POST /v1/InsysGoAccount/Authenticate

        Confirmed request body from HAR:
          { platformCodename, login, password, longExpiration }

        Response fields used:
          token, userId, tokenExpirationTime,
          profile.availableChannels[].{id, canPlay, catchupEnabled, npvrEnabled}
        """
        body = {
            "platformCodename": self._platform,
            "login": username,
            "password": password,
            "longExpiration": False,
        }
        try:
            data = self._post("/v1/InsysGoAccount/Authenticate", body)
        except Exception as ex:
            log_error("Login error: {}".format(ex))
            return False

        token = data.get("Token", "")
        if not token:
            log_error("Login: no token in response")
            return False

        # Parse expiry — "tokenExpirationTime": "2026-06-05T23:29:44+02:00"
        expiry_str = data.get("TokenExpirationTime", "")
        expiry_ts = self._parse_expiry(expiry_str)

        self._token = token
        self._token_expiry = expiry_ts
        self._user_id = data.get("UserId", 0)
        self._device_registered = False  # force re-register on new login

        # Cache available channel capabilities from profile
        profile = data.get("Profile", {})
        avail = {str(c["id"]): c for c in profile.get("availableChannels", [])}

        _SessionCache.save(
            {
                "token": self._token,
                "token_expiry": self._token_expiry,
                "user_id": self._user_id,
                "device_registered": False,
                "available_channels": avail,
            }
        )
        log_info("Login OK, userId={}, token={}...".format(self._user_id, token[:8]))
        return True

    def logout(self):
        self._token = ""
        self._token_expiry = 0
        self._user_id = 0
        self._device_registered = False
        _SessionCache.clear()

    def register_device(self):
        """
        POST /v1/Devices/RegisterDevice

        Must be called after login and before AcquireContent.
        First call: isNew=true. Subsequent calls for same deviceKey: isNew=false.

        Confirmed request body from HAR:
          { platformCodename, deviceKey, userToken, pushToken,
            generalDeviceType, deviceName, userAgent, operatingSystem, versionOs }
        """
        if self._device_registered:
            return True

        body = {
            "platformCodename": self._platform,
            "deviceKey": self._device_key,
            "userToken": self._token,
            "pushToken": "",
            "generalDeviceType": "2",  # 2 = STB/Smart TV in InsysGO
            "deviceName": ADDON.getSetting("device_name") or "Kodi",
            "userAgent": "Kodi/{}".format(ADDON.getAddonInfo("version")),
            "operatingSystem": "Linux",
            "versionOs": "x86_64",
        }
        try:
            data = self._post("/v1/Devices/RegisterDevice", body)
        except Exception as ex:
            log_error("RegisterDevice error: {}".format(ex))
            return False

        result = data.get("Result", {})
        if not result.get("Success", False):
            code = result.get("Code", -1)
            if code == 9120:  # device_limit_exceeded
                raise DeviceError("device_limit_exceeded", code)
            log_error("RegisterDevice failed: code={}".format(code))
            return False

        self._device_registered = True
        cache = _SessionCache.load()
        cache["device_registered"] = True
        _SessionCache.save(cache)
        debug("RegisterDevice OK, isNew={}".format(data.get("isNew")))
        return True

    # -------------------------------------------------------------------------
    # Public: Channels
    # -------------------------------------------------------------------------

    def get_channel_list(self):
        """
        Two-step channel fetch confirmed from HAR:

        Step 1: POST /v1/EpgTile/FilterChannelTiles
          Body: { platformCodename, token, isParentalControlEnabled }
          Returns: { channels: [ { tiles: [ {id, type, codename, originEntityId} ] } ] }

        Step 2: POST /v2/Tile/GetTiles
          Body: { platformCodename, requestedTiles: [ {id: "chn.XXXX"} ] }
          Returns: { tiles: [ { title, images, categories, orderNumber,
                                isCatchupEnabled, isNpvrEnabled, ... } ] }

        Returns merged list of channel dicts.
        """
        # Step 1 – get tile IDs
        body1 = {
            "platformCodename": self._platform,
            "token": self._token,
            "isParentalControlEnabled": False,
        }
        data1 = self._post("/v1/EpgTile/FilterChannelTiles", body1)
        channel_groups = data1.get("Channels", [])

        tile_ids = []
        codename_map = {}
        for group in channel_groups:
            for tile in group.get("Tiles", []):
                tid = tile.get("Id", "")
                codename = tile.get("Codename", "")
                if tid:
                    tile_ids.append(tid)
                    codename_map[tid] = codename

        if not tile_ids:
            return []

        # Step 2 – get full tile metadata in batches of 50
        tiles = []
        for i in range(0, len(tile_ids), 50):
            batch = tile_ids[i : i + 50]
            body2 = {
                "platformCodename": self._platform,
                "requestedTiles": [{"id": tid} for tid in batch],
            }
            if self._token:
                body2["Token"] = self._token
            data2 = self._post("/v2/Tile/GetTiles", body2)
            tiles.extend(data2.get("Tiles", []))

        # Merge codenames back in and normalise
        result = []
        for tile in tiles:
            tile_id = tile.get("Id", "")
            codename = tile.get("Codename", "")
            logo_url = ""
            for img in tile.get("Images", []):
                if img.get("Role") == "icon-on-dark":
                    logo_url = img.get("Url", "")
                    break
            if not logo_url:
                for img in tile.get("Images", []):
                    if img.get("Role") == "icon":
                        logo_url = img.get("Url", "")
                        break

            result.append(
                {
                    "id": tile_id,
                    "codename": codename,
                    "title": tile.get("Title", codename),
                    "logo": logo_url,
                    "order": tile.get("OrderNumber", 999),
                    "category": (tile.get("ChannelCategory") or {}).get("Name", ""),
                    "has_catchup": tile.get("IsCatchupEnabled", False),
                    "catchup_days": tile.get("CatchupDays", 0),
                    "has_npvr": tile.get("IsNpvrEnabled", False),
                    "is_adult": tile.get("IsAdultContent", False),
                }
            )

        result.sort(key=lambda c: c["order"])
        return result

    # -------------------------------------------------------------------------
    # Public: EPG
    # -------------------------------------------------------------------------

    def get_epg_now(self, channel_tile_ids=None):
        """
        GET /v1/EpgTile/FilterNowOnTvTiles

        Returns: { channels: [ { id, codename, programs: [{id, codename, from, to}] } ] }

        If channel_tile_ids is given, passes channelTilesIds param (comma-separated).
        """
        params = {}
        if channel_tile_ids:
            params["channelTilesIds"] = ",".join(channel_tile_ids)
        data = self._get("/v1/EpgTile/FilterNowOnTvTiles", params)
        return data.get("Channels", [])

    def get_epg_schedule(self, channel_codenames, from_iso, to_iso):
        """
        POST /v1/EpgTile/FilterProgramTiles

        Confirmed body from HAR:
          { platformCodename, from, to, orChannelCodenames: [...] }

        Returns: { programs: { "channel-codename": [ {id, codename, from, to} ] } }
        Note: program tiles only have id/codename/from/to — use get_tile_details for metadata.
        """
        body = {
            "platformCodename": self._platform,
            "from": from_iso,
            "to": to_iso,
            "orChannelCodenames": channel_codenames,
        }
        if self._token:
            body["token"] = self._token
        data = self._post("/v1/EpgTile/FilterProgramTiles", body)
        return data.get("Programs", {})

    def get_tile_details(self, tile_ids):
        """
        POST /v2/Tile/GetTiles – fetch full metadata for program/channel tiles.
        tile_ids: list of strings like "prg.8578586"
        """
        body = {
            "platformCodename": self._platform,
            "requestedTiles": [{"id": tid} for tid in tile_ids],
        }
        if self._token:
            body["token"] = self._token
        data = self._post("/v2/Tile/GetTiles", body)
        return data.get("Tiles", [])

    # -------------------------------------------------------------------------
    # Public: Streaming
    # -------------------------------------------------------------------------

    def acquire_content(self, channel_codename):
        """
        GET /v1/Player/AcquireContent

        Confirmed query params from HAR:
          platformCodename, deviceKey, token, codename, t (unix ms timestamp)

        First call with unregistered deviceKey returns:
          { Result: { Success: false, Code: 9122, MessageCodename: "invalid_device_key" } }
        After RegisterDevice call succeeds, returns full response.

        Response structure (confirmed):
        {
          "DrmInfo": [
            { "DrmSystem": "Widevine",
              "LicenseServerUrl": "https://wigo.la.drm.cloud/acquire-license/widevine",
              "DrmChallengeCustomData": "<base64 XML>" },
            { "DrmSystem": "PlayReady", ... },
            { "DrmSystem": "FairPlay",  ... },
          ],
          "MediaFiles": [
            { "RoleCodename": "main",
              "Formats": [
                { "Type": 9, "Protection": 4,
                  "Url": "https://supermedia3.cf.insyscd.net/<JWT>/...fhd.mpd" },
                { "Type": 2, "Protection": 5,
                  "Url": "https://supermedia3.cf.insyscd.net/<JWT>/...fhd.m3u8" },
              ]
            }
          ],
          "Cap": {
            "SessionId": "...",
            "CAPPublicUrl": "https://cap-ha.app.insysgo.pl/v1/CAP/Ping",
            "CAPIntervalSeconds": 60,
            "SessionTimeoutSeconds": 181,
          },
          "Signature": "...",
          "Result": { "Success": true, "Code": 0 }
        }
        """
        if not self._device_registered:
            if not self.register_device():
                raise DeviceError("Device registration failed")

        params = {
            "deviceKey": self._device_key,
            "codename": channel_codename,
            "t": str(int(time.time() * 1000)),
        }
        data = self._get("/v1/Player/AcquireContent", params)

        result = data.get("Result", {})
        if not result.get("Success", False):
            code = result.get("Code", -1)
            msg_code = result.get("MessageCodename", "")
            log_error("AcquireContent failed: code={} msg={}".format(code, msg_code))
            if code in (9122, 9120):
                # 9122 = invalid_device_key → need RegisterDevice
                # 9120 = device_limit_exceeded
                self._device_registered = False
                raise DeviceError(msg_code, code)
            raise APIError(msg_code, code)

        return data

    def pick_stream(self, acquire_data, prefer_dash=True):
        """
        Extract the best stream URL from an AcquireContent response.

        Format types confirmed from HAR:
          Type 9 = MPEG-DASH .mpd
          Type 2 = HLS .m3u8

        Returns dict: { url, type ("dash"|"hls"), drm_info }
        """
        prefer_type = self.FORMAT_TYPE_DASH if prefer_dash else self.FORMAT_TYPE_HLS
        fallback_type = self.FORMAT_TYPE_HLS if prefer_dash else self.FORMAT_TYPE_DASH

        best_url = ""
        best_type = ""

        for mf in acquire_data.get("MediaFiles", []):
            if mf.get("RoleCodename") != "main":
                continue
            formats = {f["Type"]: f for f in mf.get("Formats", []) if "Url" in f}
            if prefer_type in formats:
                best_url = formats[prefer_type]["Url"]
                best_type = "dash" if prefer_type == self.FORMAT_TYPE_DASH else "hls"
                break
            if fallback_type in formats:
                best_url = formats[fallback_type]["Url"]
                best_type = "hls" if fallback_type == self.FORMAT_TYPE_HLS else "dash"
                break

        if not best_url:
            raise APIError("No playable stream format found")

        # Find Widevine DRM info (Kodi only supports Widevine, not PlayReady/FairPlay)
        drm_info = None
        for drm in acquire_data.get("DrmInfo", []):
            if drm.get("DrmSystem", "").lower() == "widevine":
                drm_info = {
                    "license_url": drm["LicenseServerUrl"],
                    "challenge_data": drm.get("DrmChallengeCustomData", ""),
                }
                break

        return {
            "url": best_url,
            "type": best_type,
            "drm_info": drm_info,
            "cap": acquire_data.get("Cap"),
            "signature": acquire_data.get("Signature", ""),
        }

    # -------------------------------------------------------------------------
    # Public: CAP heartbeat (concurrent access protection)
    # -------------------------------------------------------------------------

    def cap_ping(self, cap_session, duration_seconds=0, counter=0):
        """
        POST https://cap-ha.app.insysgo.pl/v1/CAP/Ping

        Must be sent every CAPIntervalSeconds (60s) during playback.
        If it stops, the session is killed after SessionTimeoutSeconds (181s).

        Confirmed body from HAR:
          { SessionId, DurationSeconds, ProgressSeconds, Counter, Status, Signature }
        """
        if not cap_session:
            return None
        body = {
            "SessionId": cap_session.get("SessionId", ""),
            "DurationSeconds": duration_seconds,
            "ProgressSeconds": -1,
            "Counter": counter,
            "Status": "Play",
            "Signature": "",  # server accepts empty on CAP pings from web
        }
        try:
            return self._post(
                "/v1/CAP/Ping",
                body,
                raw_url=cap_session.get("CAPPublicUrl", self._CAP_URL),
            )
        except Exception as ex:
            log_warn("CAP ping failed: {}".format(ex))
            return None

    # -------------------------------------------------------------------------
    # Public: Replay / Catchup
    # -------------------------------------------------------------------------

    def get_catchup_stream(self, program_tile_id, channel_codename):
        """
        Catchup (Replay TV) uses the same AcquireContent endpoint
        but with the program tile ID as codename, e.g. "prg.8578586"
        → codename = "tvn-hd-204199801" (the program codename, not channel)

        In practice: fetch the program tile via GetTiles first to get its codename,
        then call AcquireContent with that codename.
        """
        tiles = self.get_tile_details([program_tile_id])
        if not tiles:
            raise APIError("Programme tile not found: {}".format(program_tile_id))
        codename = tiles[0].get("Codename", "")
        if not codename:
            raise APIError("Programme tile has no codename")
        return self.acquire_content(codename)

    # -------------------------------------------------------------------------
    # Public: Recordings (nPVR)
    # -------------------------------------------------------------------------

    def get_recordings(self):
        """GET /v1/IpottPlaylist/GetUserRecordings (nPVR list)"""
        try:
            data = self._get("/v1/IpottPlaylist/GetUserRecordings")
            return data.get("Recordings", data.get("Items", []))
        except Exception as ex:
            log_error("get_recordings: {}".format(ex))
            return []

    def schedule_recording(self, event_id):
        """Schedule nPVR recording for an EPG event."""
        body = {
            "platformCodename": self._platform,
            "token": self._token,
            "eventId": event_id,
        }
        return self._post("/v1/IpottPlaylist/ScheduleRecording", body)

    def delete_recording(self, recording_id):
        """Delete a completed or scheduled recording."""
        return self._delete("/v1/IpottPlaylist/DeleteRecording", {"recordingId": recording_id})

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _parse_expiry(expiry_str):
        """Parse ISO 8601 timestamp to unix timestamp, fall back to 24h."""
        if not expiry_str:
            return time.time() + 86400
        try:
            # Handle offset format: "2026-06-05T23:29:44+02:00"
            s = expiry_str
            # Python 3.6 fromisoformat doesn't handle timezone offset well
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                dt = datetime.datetime.fromisoformat(s)
            except AttributeError:
                # Python < 3.7 fallback
                s = s[:19]
                dt = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
            return dt.timestamp()
        except Exception:
            return time.time() + 86400

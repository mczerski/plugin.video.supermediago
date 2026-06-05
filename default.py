"""
plugin.video.supermediago – main entry point
=============================================
Kodi calls this with:
  sys.argv[0]  = plugin://plugin.video.supermediago/
  sys.argv[1]  = handle (int)
  sys.argv[2]  = query string, e.g. ?action=play_live&codename=tvn-hd
"""

import sys
import os

# Make resources/lib importable
_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB  = os.path.join(_HERE, "resources", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

from api    import InsysGoAPI, AuthError, DeviceError, NetworkError, APIError
from player import resolve_stream
from ui     import (build_url, parse_params, add_dir, end_dir,
                    add_channel_item, add_replay_date_items,
                    add_program_item, add_recording_item)

ADDON    = xbmcaddon.Addon()
BASE_URL = sys.argv[0]
HANDLE   = int(sys.argv[1])
PARAMS   = parse_params(sys.argv[2] if len(sys.argv) > 2 else "")

_api = None


def _s(n):
    return ADDON.getLocalizedString(n)


def get_api():
    global _api
    if _api is None:
        _api = InsysGoAPI()
    return _api


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def require_login():
    api = get_api()
    if api.is_logged_in():
        return True

    username = ADDON.getSetting("username").strip()
    password = ADDON.getSetting("password").strip()
    if not username or not password:
        xbmcgui.Dialog().ok(_s(32000), _s(32103))
        ADDON.openSettings()
        return False

    pd = xbmcgui.DialogProgress()
    pd.create(_s(32000), _s(32100))
    try:
        ok = api.login(username, password)
    finally:
        pd.close()

    if ok:
        xbmcgui.Dialog().notification(
            _s(32000), _s(32101), xbmcgui.NOTIFICATION_INFO, 3000)
        return True

    xbmcgui.Dialog().ok(_s(32000), _s(32102))
    ADDON.openSettings()
    return False


def _handle_error(ex):
    """Show appropriate notification for API errors and log."""
    xbmc.log("[plugin.video.supermediago] Error: {}".format(ex), xbmc.LOGERROR)
    if isinstance(ex, AuthError):
        msg = _s(32104)
        get_api().logout()
    elif isinstance(ex, DeviceError):
        msg = _s(32125)
    elif isinstance(ex, NetworkError):
        msg = _s(32105)
    else:
        msg = _s(32107)
    xbmcgui.Dialog().notification(
        _s(32000), msg, xbmcgui.NOTIFICATION_ERROR, 5000)


# ---------------------------------------------------------------------------
# Views (directory listings)
# ---------------------------------------------------------------------------

def view_main_menu():
    add_dir(HANDLE, _s(32002),
            build_url(BASE_URL, action="channels"),
            art={"icon": "DefaultTVShows.png"})

    add_dir(HANDLE, _s(32003),
            build_url(BASE_URL, action="replay_channels"),
            art={"icon": "DefaultRecentlyAddedEpisodes.png"})

    add_dir(HANDLE, _s(32004),
            build_url(BASE_URL, action="recordings"),
            art={"icon": "DefaultVideoPlaylists.png"})

    add_dir(HANDLE, _s(32005),
            build_url(BASE_URL, action="settings"),
            is_folder=False,
            art={"icon": "DefaultAddonSettings.png"})

    end_dir(HANDLE, sort_methods=[xbmcplugin.SORT_METHOD_NONE], content="")


def view_channels():
    if not require_login():
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    api = get_api()

    xbmc.executebuiltin("ActivateWindow(busydialognocancel)")
    try:
        channels = api.get_channel_list()
    except Exception as ex:
        xbmc.executebuiltin("Dialog.Close(busydialognocancel)")
        _handle_error(ex)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    # Build EPG-now dict keyed by channel codename
    epg_now_map = {}
    if ADDON.getSettingBool("show_epg_overlay") and channels:
        try:
            now_data = api.get_epg_now()
            for ch_data in now_data:
                cname = ch_data.get("Codename", "")
                progs = ch_data.get("Programs", [])
                if cname and progs:
                    epg_now_map[cname] = progs[0]  # first = currently airing
        except Exception:
            pass  # EPG overlay is nice-to-have, not critical

    xbmc.executebuiltin("Dialog.Close(busydialognocancel)")

    for ch in channels:
        epg = epg_now_map.get(ch.get("Codename", ""))
        add_channel_item(HANDLE, BASE_URL, ch, epg_now=epg)

    end_dir(HANDLE, sort_methods=[xbmcplugin.SORT_METHOD_NONE])


def view_replay_channels():
    """Channel picker for Replay TV."""
    if not require_login():
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    api = get_api()
    try:
        channels = [c for c in api.get_channel_list() if c.get("has_catchup")]
    except Exception as ex:
        _handle_error(ex)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    for ch in channels:
        url = build_url(BASE_URL, action="replay_dates",
                        codename=ch["codename"], title=ch["title"],
                        logo=ch.get("logo", ""),
                        catchup_days=ch.get("catchup_days", 3))
        add_dir(HANDLE, ch["title"], url,
                art={"thumb": ch.get("logo", ""), "icon": ch.get("logo", "")})

    end_dir(HANDLE, sort_methods=[xbmcplugin.SORT_METHOD_NONE])


def view_replay_dates():
    codename    = PARAMS.get("codename", "")
    title       = PARAMS.get("title", codename)
    logo        = PARAMS.get("logo", "")
    catchup_days = int(PARAMS.get("catchup_days", 3))
    add_replay_date_items(HANDLE, BASE_URL, codename, title, logo, catchup_days)
    end_dir(HANDLE, sort_methods=[xbmcplugin.SORT_METHOD_NONE])


def view_replay_list():
    """Programme list for a channel + date."""
    if not require_login():
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    codename = PARAMS.get("codename", "")
    date_str = PARAMS.get("date", "")
    logo     = PARAMS.get("logo", "")
    api      = get_api()

    import datetime
    # EPG schedule for that one day
    try:
        from_iso = "{}T00:00:00.000Z".format(date_str)
        to_iso   = "{}T23:59:59.000Z".format(date_str)
        schedule = api.get_epg_schedule([codename], from_iso, to_iso)
        progs    = schedule.get(codename, [])
    except Exception as ex:
        _handle_error(ex)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    if not progs:
        xbmcgui.Dialog().notification(_s(32000), _s(32109))
        xbmcplugin.endOfDirectory(HANDLE)
        return

    # Fetch full tile metadata for programme titles / thumbnails
    tile_ids = [p["Id"] for p in progs if p.get("Id")]
    tile_map = {}
    if tile_ids:
        try:
            tiles    = api.get_tile_details(tile_ids[:50])  # first 50
            tile_map = {t["Id"]: t for t in tiles}
        except Exception:
            pass

    for prog in sorted(progs, key=lambda p: p.get("From", "")):
        pid  = prog.get("Id", "")
        meta = tile_map.get(pid, prog)
        add_program_item(HANDLE, BASE_URL, meta, channel_logo=logo)

    end_dir(HANDLE,
            sort_methods=[xbmcplugin.SORT_METHOD_NONE],
            content="episodes")


def view_recordings():
    if not require_login():
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    api = get_api()
    try:
        recs = api.get_recordings()
    except Exception as ex:
        _handle_error(ex)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    if not recs:
        xbmcgui.Dialog().notification(_s(32000), _s(32110))
        xbmcplugin.endOfDirectory(HANDLE)
        return

    for rec in recs:
        add_recording_item(HANDLE, BASE_URL, rec)

    end_dir(HANDLE,
            sort_methods=[xbmcplugin.SORT_METHOD_NONE],
            content="episodes")


# ---------------------------------------------------------------------------
# Playback actions
# ---------------------------------------------------------------------------

def _play_acquire(codename, title, logo, is_replay=False, tile_id=None):
    """Shared logic for live + replay playback via AcquireContent."""
    if not require_login():
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    api   = get_api()
    prefer_dash = ADDON.getSetting("stream_type") == "0"

    try:
        if is_replay and tile_id:
            acquire_data = api.get_catchup_stream(tile_id, codename)
        else:
            acquire_data = api.acquire_content(codename)

        stream = api.pick_stream(acquire_data, prefer_dash=prefer_dash)

    except DeviceError as ex:
        # Device not registered yet – register and retry once
        if ex.code == 9122:
            try:
                api.register_device()
                acquire_data = api.acquire_content(codename)
                stream       = api.pick_stream(acquire_data, prefer_dash=prefer_dash)
            except Exception as ex2:
                _handle_error(ex2)
                xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
                return
        else:
            _handle_error(ex)
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
            return
    except Exception as ex:
        _handle_error(ex)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    meta = {"title": title, "thumb": logo}
    resolve_stream(HANDLE, stream, metadata=meta)

    # Start CAP heartbeat monitor in background thread
    cap = stream.get("cap")
    if cap:
        _start_cap_monitor(api, cap)


def action_play_live():
    codename = PARAMS.get("codename", "")
    title    = PARAMS.get("title", codename)
    logo     = PARAMS.get("logo", "")
    _play_acquire(codename, title, logo)


def action_play_replay():
    tile_id  = PARAMS.get("tile_id", "")
    title    = PARAMS.get("title", "")
    logo     = PARAMS.get("logo", "")
    codename = PARAMS.get("codename", "")
    _play_acquire(codename, title, logo, is_replay=True, tile_id=tile_id)


def action_play_recording():
    rec_id = PARAMS.get("rec_id", "")
    title  = PARAMS.get("title", "")
    if not require_login():
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    api = get_api()
    try:
        acquire_data = api.acquire_content(rec_id)
        prefer_dash  = ADDON.getSetting("stream_type") == "0"
        stream       = api.pick_stream(acquire_data, prefer_dash=prefer_dash)
    except Exception as ex:
        _handle_error(ex)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    resolve_stream(HANDLE, stream, metadata={"title": title})


# ---------------------------------------------------------------------------
# CAP heartbeat (background thread)
# ---------------------------------------------------------------------------

def _start_cap_monitor(api, cap_session):
    """
    Sends CAP/Ping every CAPIntervalSeconds while Kodi is playing.
    Runs in a daemon thread so it doesn't block the main Kodi process.

    The CAP (Concurrent Access Protection) system kills the stream
    after SessionTimeoutSeconds (181s) if pings stop arriving.
    """
    import threading

    interval = int(cap_session.get("CAPIntervalSeconds", 60))

    def _monitor():
        counter  = 1
        duration = 0
        player   = xbmc.Player()

        while True:
            # Wait one interval or until playback stops
            xbmc.sleep(interval * 1000)

            if not player.isPlaying():
                xbmc.log("[supermediago] CAP: playback stopped, ending heartbeat",
                         xbmc.LOGDEBUG)
                break

            duration += interval
            api.cap_ping(cap_session, duration_seconds=duration, counter=counter)
            counter += 1

    t = threading.Thread(target=_monitor, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Other actions
# ---------------------------------------------------------------------------

def action_delete_recording():
    rec_id = PARAMS.get("rec_id", "")
    if not xbmcgui.Dialog().yesno(_s(32000), _s(32117)):
        return
    api = get_api()
    if api.delete_recording(rec_id):
        xbmcgui.Dialog().notification(
            _s(32000), _s(32117), xbmcgui.NOTIFICATION_INFO, 2000)
        xbmc.executebuiltin("Container.Refresh")


def action_settings():
    ADDON.openSettings()


def action_logout():
    get_api().logout()
    xbmcgui.Dialog().notification(
        _s(32000), _s(32122), xbmcgui.NOTIFICATION_INFO, 2000)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_ROUTES = {
    None:               view_main_menu,
    "":                 view_main_menu,
    "channels":         view_channels,
    "replay_channels":  view_replay_channels,
    "replay_dates":     view_replay_dates,
    "replay_list":      view_replay_list,
    "recordings":       view_recordings,
    "play_live":        action_play_live,
    "play_replay":      action_play_replay,
    "play_recording":   action_play_recording,
    "delete_recording": action_delete_recording,
    "record_channel":   lambda: None,   # stub – schedule from EPG event needed
    "settings":         action_settings,
    "logout":           action_logout,
}


def run():
    action  = PARAMS.get("action")
    handler = _ROUTES.get(action)
    if handler is None:
        xbmc.log("[supermediago] Unknown action: {}".format(action), xbmc.LOGWARNING)
        view_main_menu()
    else:
        handler()


if __name__ == "__main__":
    run()

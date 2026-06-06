"""
Player – resolves a stream dict into a Kodi ListItem.

DRM details confirmed from HAR analysis of successful wigo.la request:
  License server:  https://wigo.la.drm.cloud/acquire-license/widevine
  POST body:       raw Widevine challenge bytes (binary protobuf)
  Required headers (ALL confirmed from HAR, missing any -> HTTP 401):
    drmchallengecustomdata: <base64-encoded LicenseRequestCustomData XML>
    origin:                 https://www.supermediago.pl   ← causes 401 if absent
    referer:                https://www.supermediago.pl/

  Stream formats:
    DASH (.mpd)  Type=9, Protection=4  → Widevine/CENC
    HLS  (.m3u8) Type=2, Protection=5  → AES-128 encrypted

inputstream.adaptive license_key pipe format (4 fields separated by |):
  <url>|<headers>|<post_body>|<response_format>
  Headers: key=value pairs separated by & (values passed verbatim to curl)
  R{SSM}:  ISA replaces with raw Widevine challenge bytes at runtime
  Empty response field = treat response as raw binary license
"""

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import inputstreamhelper
from urllib.parse import urlencode

ADDON = xbmcaddon.Addon()


def _s(n):
    return ADDON.getLocalizedString(n)


def _use_isa():
    return ADDON.getSettingBool("use_inputstream")


def resolve_stream(handle, stream, metadata=None):
    """
    Build a Kodi ListItem from a stream dict and resolve it for playback.

    stream dict (from InsysGoAPI.pick_stream):
      {
        "url":      "https://supermedia3.cf.insyscd.net/<JWT>/...fhd.mpd",
        "type":     "dash" | "hls",
        "drm_info": {
            "license_url":    "https://wigo.la.drm.cloud/acquire-license/widevine",
            "challenge_data": "<base64-encoded LicenseRequestCustomData XML>"
        } | None,
        "cap":       {...} | None,
        "signature": "..."
      }

    metadata dict (optional):
      { "title": str, "plot": str, "thumb": str }
    """
    url = stream.get("url", "")
    fmt = stream.get("type", "dash")
    drm_info = stream.get("drm_info")

    if not url:
        xbmcgui.Dialog().notification(_s(32000), _s(32107), xbmcgui.NOTIFICATION_ERROR, 4000)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    li = xbmcgui.ListItem(path=url)

    if fmt == "dash":
        li.setMimeType("application/dash+xml")
    else:
        li.setMimeType("application/vnd.apple.mpegurl")

    # Tell Kodi not to try to detect content type (avoids HEAD request to CDN)
    li.setContentLookup(False)

    # Metadata
    meta = metadata or {}
    info_tag = li.getVideoInfoTag()
    info_tag.setTitle(meta.get("title", ""))
    info_tag.setPlot(meta.get("plot", ""))
    info_tag.setMediaType("video")
    if meta.get("thumb"):
        li.setArt({"thumb": meta["thumb"], "icon": meta["thumb"]})

    # inputstream.adaptive
    is_helper = inputstreamhelper.Helper("mpd", drm="com.widevine.alpha")  # TODO: get from drm_info
    if _use_isa():
        if not is_helper.check_inputstream():
            xbmcgui.Dialog().notification(_s(32000), _s(32108), xbmcgui.NOTIFICATION_WARNING, 6000)
            # Fall through — Kodi's native player will try HLS; DASH will fail
        else:
            li.setProperty("inputstream", is_helper.inputstream_addon)

            # Max bitrate limit
            _br_map = {
                "0": 0,  # Auto
                "1": 1000000,
                "2": 2000000,
                "3": 4000000,
                "4": 8000000,
                "5": 0,  # Maximum / no cap
            }
            br = _br_map.get(str(ADDON.getSettingInt("max_bitrate")), 0)
            if br:
                li.setProperty("inputstream.adaptive.chooser_bandwidth_max", str(br))

            # Widevine DRM
            # wigo.la.drm.cloud confirmed from HAR analysis:
            #   POST body     = raw Widevine challenge (binary)
            #   Required headers (from successful browser request):
            #     drmchallengecustomdata: <base64 LicenseRequestCustomData XML>
            #     origin: https://www.supermediago.pl    <- REQUIRED, causes 401 if missing
            #     referer: https://www.supermediago.pl/
            #
            # ISA license_key pipe format: url|headers|post_body|response
            #   headers: key=value pairs separated by &
            #   R{SSM}:  ISA substitutes raw Widevine challenge bytes at runtime
            #   Empty response field = return raw binary license bytes as-is
            #
            # NOTE: Origin header is mandatory - the wigo.la CORS policy enforces it.
            # ISA does not add Origin automatically; we must include it explicitly.
            if drm_info:
                lic_url = drm_info.get("license_url", "")
                challenge_data = drm_info.get("challenge_data", "")

                li.setProperty(
                    "inputstream.adaptive.license_type",
                    "com.widevine.alpha",  # TODO: from drm_info
                )

                headers = {
                    "Origin": "https://www.supermediago.pl",
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
                    "Content-Type": "application/octet-stream",
                }
                if challenge_data:
                    # Build headers string with all required headers.
                    # & is the separator between headers in ISA pipe format.
                    # Header values are passed verbatim to curl (no URL encoding).
                    headers.update(
                        {
                            "drmchallengecustomdata": challenge_data,
                            "Referer": "https://www.supermediago.pl/",
                        }
                    )
                lic_key = "{}|{}|R{{SSM}}|R".format(lic_url, urlencode(headers))

                li.setProperty("inputstream.adaptive.license_key", lic_key)
                xbmc.log(
                    "[supermediago] DRM license_key set, url={} challenge_data_len={}".format(
                        lic_url, len(challenge_data)
                    ),
                    xbmc.LOGINFO,
                )

    xbmcplugin.setResolvedUrl(handle, True, li)

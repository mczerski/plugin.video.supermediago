"""
Player – resolves a stream URL into a Kodi ListItem with inputstream.adaptive.

DRM details confirmed from HAR capture:
  - DRM system:   Widevine
  - License URL:  https://wigo.la.drm.cloud/acquire-license/widevine
  - Custom data:  base64-encoded XML in DrmChallengeCustomData field
  - DASH MPD:     supermedia3.cf.insyscd.net/<JWT>/...fhd.mpd   (Type=9)
  - HLS m3u8:     supermedia3.cf.insyscd.net/<JWT>/...fhd.m3u8  (Type=2)

inputstream.adaptive license_key format (pipe-separated):
  <license_url>|<request_headers>|<post_body>|<response_format>
  For Widevine with custom data header:
    url|Content-Type=application%2Foctet-stream&dt=<b64data>|R{SSM}|
"""

import sys

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

ADDON = xbmcaddon.Addon()


def _s(n):
    return ADDON.getLocalizedString(n)


def _use_isa():
    return ADDON.getSettingBool("use_inputstream")


def _isa_available():
    try:
        import xbmcaddon as _a
        _a.Addon("inputstream.adaptive")
        return True
    except Exception:
        return False


def resolve_stream(handle, stream, metadata=None):
    """
    Build a Kodi ListItem from a stream dict and resolve it.

    stream dict (from api.pick_stream):
      {
        "url":      "https://supermedia3.cf.insyscd.net/<JWT>/...fhd.mpd",
        "type":     "dash" | "hls",
        "drm_info": { "license_url": "...", "challenge_data": "<b64>" } | None,
        "cap":      { "SessionId": ..., "CAPPublicUrl": ..., ... } | None,
      }

    metadata dict (optional):
      { "title": str, "plot": str, "thumb": str }
    """
    url      = stream.get("url", "")
    fmt      = stream.get("type", "dash")
    drm_info = stream.get("drm_info")

    if not url:
        xbmcgui.Dialog().notification(
            _s(32000), _s(32107), xbmcgui.NOTIFICATION_ERROR, 4000
        )
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    li = xbmcgui.ListItem(path=url)

    if fmt == "dash":
        li.setMimeType("application/dash+xml")
    else:
        li.setMimeType("application/vnd.apple.mpegurl")

    li.setContentLookup(False)

    # -- Metadata -----------------------------------------------------------
    meta = metadata or {}
    info_tag = li.getVideoInfoTag()
    info_tag.setTitle(meta.get("title", ""))
    info_tag.setPlot(meta.get("plot", ""))
    info_tag.setMediaType("video")
    if meta.get("thumb"):
        li.setArt({"thumb": meta["thumb"], "icon": meta["thumb"]})

    # -- inputstream.adaptive -----------------------------------------------
    if _use_isa():
        if not _isa_available():
            xbmcgui.Dialog().notification(
                _s(32000), _s(32108), xbmcgui.NOTIFICATION_WARNING, 6000
            )
        else:
            li.setProperty("inputstream", "inputstream.adaptive")
            li.setProperty(
                "inputstream.adaptive.manifest_type",
                "mpd" if fmt == "dash" else "hls"
            )

            # Bitrate cap
            _br_map = {"0": 0, "1": 1000000, "2": 2000000,
                       "3": 4000000, "4": 8000000, "5": 0}
            br = _br_map.get(ADDON.getSetting("max_bitrate"), 0)
            if br:
                li.setProperty("inputstream.adaptive.max_bandwidth", str(br))

            # -- Widevine DRM -----------------------------------------------
            # wigo.la.drm.cloud expects the DrmChallengeCustomData as a custom
            # HTTP header "dt" on the license request. The InsysGO web player
            # sends it as a POST body field; inputstream.adaptive uses the
            # pipe-format license_key to pass it.
            if drm_info:
                lic_url     = drm_info.get("license_url", "")
                custom_data = drm_info.get("challenge_data", "")

                li.setProperty(
                    "inputstream.adaptive.license_type",
                    "com.widevine.alpha"
                )
                # Format: url|request_headers|post_body|response
                # We send the custom data in the "dt" header as the web app does
                # and use R{SSM} so ISA replaces it with the Widevine challenge
                if custom_data:
                    lic_key = "{url}|Content-Type=application%2Foctet-stream&dt={dt}|R{{SSM}}|".format(
                        url=lic_url,
                        dt=custom_data,
                    )
                else:
                    lic_key = "{}||R{{SSM}}|".format(lic_url)

                li.setProperty("inputstream.adaptive.license_key", lic_key)

    xbmcplugin.setResolvedUrl(handle, True, li)

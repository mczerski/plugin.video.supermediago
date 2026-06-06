"""
UI helpers – build Kodi 22 ListItems for channels, EPG, replay, recordings.
Uses the new getVideoInfoTag() API (Kodi 20+) instead of deprecated setInfo().
"""

import datetime
import sys
from urllib.parse import urlencode

import xbmcgui
import xbmcplugin
import xbmcaddon

ADDON = xbmcaddon.Addon()


def _s(n):
    return ADDON.getLocalizedString(n)


def build_url(base, **kw):
    return "{}?{}".format(base, urlencode({k: v for k, v in kw.items() if v is not None}))


def add_dir(handle, label, url, is_folder=True, art=None, info=None, props=None):
    li = xbmcgui.ListItem(label=label)
    if art:
        li.setArt(art)
    if info:
        tag = li.getVideoInfoTag()
        tag.setTitle(info.get("title", label))
        if "plot" in info:
            tag.setPlot(info["plot"])
        tag.setMediaType(info.get("mediatype", "video"))
    if props:
        for k, v in props.items():
            li.setProperty(k, str(v))
    xbmcplugin.addDirectoryItem(handle, url, li, is_folder)


def end_dir(handle, sort_methods=None, content="videos", cache=False):
    if content:
        xbmcplugin.setContent(handle, content)
    if sort_methods:
        for m in sort_methods:
            xbmcplugin.addSortMethod(handle, m)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=cache)


# ---------------------------------------------------------------------------
# Channel list
# ---------------------------------------------------------------------------

def add_channel_item(handle, base_url, channel, epg_now=None):
    """
    channel dict from api.get_channel_list():
      { id, codename, title, logo, order, category, has_catchup, has_npvr }

    epg_now: full program tile dict from GetTiles (after enrichment in view_channels)
             containing: title, description, shortDescription, images[{role,url}]
             OR bare stub from FilterNowOnTvTiles: {id, codename, from, to}
    """
    codename = channel.get("codename", "")
    title    = channel.get("title", codename)
    logo     = channel.get("logo", "")
    order    = channel.get("order", 999)

    # --- Extract EPG data (handles both full tile and bare stub) ---
    epg_title = ""
    epg_plot  = ""
    if epg_now:
        epg_title = (epg_now.get("Title")
                     or _codename_to_display(
                            epg_now.get("Codename", "")))
        epg_plot  = (epg_now.get("Description")
                     or epg_now.get("ShortDescription")
                     or "")

    label = "{} - {}: {}".format(order, title, epg_title)

    play_url = build_url(base_url, action="play_live", codename=codename,
                         title=label, logo=logo)

    li = xbmcgui.ListItem(label=label)

    art = {"icon": logo}
    li.setArt(art)

    # Info tag — drives the info panel shown when the item is selected
    tag = li.getVideoInfoTag()
    tag.setTitle(label)
    tag.setPlot(epg_plot or epg_title)
    tag.setMediaType("video")
    tag.setPlaycount(0)

    li.setProperty("IsPlayable", "true")

    # Context menu
    ctx = []
    if channel.get("has_catchup"):
        ctx.append((
            _s(32003),
            "Container.Update({})".format(
                build_url(base_url, action="replay_dates",
                          codename=codename, title=title, logo=logo)
            )
        ))
    if channel.get("has_npvr"):
        ctx.append((
            _s(32116),
            "RunPlugin({})".format(
                build_url(base_url, action="record_channel", codename=codename)
            )
        ))
    if ctx:
        li.addContextMenuItems(ctx)

    xbmcplugin.addDirectoryItem(handle, play_url, li, False)


# ---------------------------------------------------------------------------
# Replay TV
# ---------------------------------------------------------------------------

def add_replay_date_items(handle, base_url, codename, title, logo, catchup_days=3):
    today = datetime.date.today()
    labels = [_s(32008), _s(32009), _s(32114), _s(32115)]
    for i in range(min(catchup_days, len(labels))):
        d   = today - datetime.timedelta(days=i)
        lbl = labels[i]
        url = build_url(base_url, action="replay_list",
                        codename=codename, title=title, logo=logo,
                        date=d.strftime("%Y-%m-%d"))
        add_dir(handle, lbl, url, is_folder=True,
                art={"thumb": logo, "icon": logo})


def add_program_item(handle, base_url, prog, channel_logo=""):
    """
    prog tile from FilterProgramTiles / GetTiles:
      { id, codename, from, to, title?, images? }
    """
    prog_id  = prog.get("Id", "")
    codename = prog.get("Codename", "")
    title    = prog.get("Title", "") or _codename_to_display(codename)
    short_description = prog.get("ShortDescription", "")
    description = prog.get("Description", "")
    start    = _parse_iso(prog.get("Start", ""))
    end      = _parse_iso(prog.get("Stop", ""))

    # Get thumbnail from images
    thumb = channel_logo
    for img in prog.get("Images", []):
        if img.get("Role") == "thumbnail":
            thumb = img.get("Url", "")
            break

    duration = int((end - start).total_seconds()) if start and end else 0
    label    = "{} {}".format(start.strftime("%H:%M") if start else "", title).strip()

    play_url = build_url(base_url, action="play_replay",
                         tile_id=prog_id, title=title, logo=thumb)

    li = xbmcgui.ListItem(label=label)
    li.setArt({"thumb": thumb})
    li.setInfo("video", {"plot": description, "plotoutline": short_description})

    tag = li.getVideoInfoTag()
    tag.setTitle(label)
    tag.setMediaType("video")
    if duration:
        tag.setDuration(duration)
    if start:
        tag.setFirstAired(start.strftime("%Y-%m-%d"))

    li.setProperty("IsPlayable", "true")
    xbmcplugin.addDirectoryItem(handle, play_url, li, False)


# ---------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------

def add_recording_item(handle, base_url, rec):
    rec_id  = rec.get("id", "")
    title   = rec.get("title", rec_id)
    status  = rec.get("status", "")
    thumb   = rec.get("thumbnail", "")

    status_labels = {
        "scheduled":  "[{}] ".format(_s(32118)),
        "recording":  "[{}] ".format(_s(32119)),
        "completed":  "",
    }
    label = "{}{}".format(status_labels.get(status, ""), title)

    is_playable = (status == "completed")
    play_url    = build_url(base_url, action="play_recording",
                            rec_id=rec_id, title=title) if is_playable else base_url

    li = xbmcgui.ListItem(label=label)
    if thumb:
        li.setArt({"thumb": thumb})
    tag = li.getVideoInfoTag()
    tag.setTitle(title)
    tag.setMediaType("video")

    if is_playable:
        li.setProperty("IsPlayable", "true")
        ctx = [(_s(32117), "RunPlugin({})".format(
            build_url(base_url, action="delete_recording", rec_id=rec_id)
        ))]
        li.addContextMenuItems(ctx)

    xbmcplugin.addDirectoryItem(handle, play_url, li, False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso(s):
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        try:
            return datetime.datetime.fromisoformat(s2)
        except AttributeError:
            # Python < 3.7
            return datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def _codename_to_display(codename):
    """'tvn-7-hd-204094331' → 'tvn-7-hd' (strip trailing numeric episode ID)"""
    if not codename:
        return ""
    parts = codename.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return codename

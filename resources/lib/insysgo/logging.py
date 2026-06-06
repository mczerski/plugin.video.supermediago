import xbmc
import xbmcaddon


ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")


def _log(msg, level=xbmc.LOGDEBUG):
    xbmc.log("[{}] {}".format(ADDON_ID, msg), level=level)


def log_info(msg):
    _log(msg, xbmc.LOGINFO)


def log_error(msg):
    _log(msg, xbmc.LOGERROR)


def log_warn(msg):
    _log(msg, xbmc.LOGWARNING)


def debug(msg):
    if ADDON.getSettingBool("debug_log"):
        _log(msg, xbmc.LOGINFO)

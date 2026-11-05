"""Open the two explicitly configured menu links in the OS default browser."""
import os
import time
import webbrowser

URLS={
    '/game/inweburl/worldcup':'https://www.fo3th.online/',
    '/game/inweburl/webevent':'https://www.fo3th.online/',
}
_last={}

def open_link(path):
    url=URLS.get(path)
    if url is None:return False
    now=time.monotonic()
    if now-_last.get(path,-100)<1.5:return True
    try:
        if os.name=='nt':os.startfile(url)
        elif not webbrowser.open(url,new=2):return False
    except OSError:
        return False
    _last[path]=now
    return True

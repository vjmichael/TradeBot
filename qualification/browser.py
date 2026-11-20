# Test-only adapter. Exercise the candidate's actual audit against installed
# named services; require normal Chromium sandbox (Playwright defaults differ).
import os, sys
from pathlib import Path
from playwright.sync_api import BrowserType
original=BrowserType.launch_persistent_context
def sandboxed(self,*args,**kwargs):
    kwargs["chromium_sandbox"]=True
    return original(self,*args,**kwargs)
BrowserType.launch_persistent_context=sandboxed
sys.path.insert(0,str(Path(sys.argv[1])/"scripts"))
from qualify_account_browser import browser
browser(Path(sys.argv[2]),sys.argv[3],"18.3.1")

"""Shadow-only multi-sport line prediction infrastructure."""

__version__ = "0.6.0"

import subprocess
import sys

if sys.platform == "win32":
    # On Windows, guarantee that all subprocesses run completely hidden without
    # creating interactive console windows or desktop terminal popups.
    _orig_popen_init = subprocess.Popen.__init__

    def _hidden_popen_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        flags = kwargs.get("creationflags", 0)
        flags |= subprocess.CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
        _orig_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _hidden_popen_init

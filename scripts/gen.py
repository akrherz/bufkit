"""Helper script for reprocessing manually."""

import subprocess
from datetime import datetime, timedelta

now = datetime(2019, 2, 6, 12)
delta = timedelta(hours=6)
end = datetime(2019, 2, 7, 0)

while now <= end:
    cmd = now.strftime("python scripts/run_bufkit.py nam %Y %m %d %H")
    subprocess.call(cmd.split())
    now += delta

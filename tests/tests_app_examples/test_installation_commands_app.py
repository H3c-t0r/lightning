import os
from datetime import time

import pytest
from tests_app import _PROJECT_ROOT

from lightning_app.testing.testing import run_app_in_cloud


@pytest.mark.cloud
def test_installation_commands_app_example_cloud() -> None:
    with run_app_in_cloud(
        os.path.join(_PROJECT_ROOT, "examples/app_installation_commands"),
        app_name="app.py",
        extra_args=["--setup"],
        debug=True,
    ) as (_, _, fetch_logs, _):
        has_logs = False
        while not has_logs:
            for log in fetch_logs(["work"]):
                if "lmdb successfully installed" in log:
                    has_logs = True
            time.sleep(1)

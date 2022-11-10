import os
from datetime import time

import pytest
from tests_app import _PROJECT_ROOT

from lightning_app.testing.testing import run_app_in_cloud


@pytest.mark.cloud
def test_installation_commands_app_example_cloud() -> None:
    # This is expected to fail since it is missing the "setup" flag
    with run_app_in_cloud(
        os.path.join(_PROJECT_ROOT, "examples/app_installation_commands"),
        app_name="app.py",
        debug=True,
    ) as (_, _, fetch_logs, _):
        has_logs = False
        while not has_logs:
            for log in fetch_logs(["work"]):
                if "ModuleNotFoundError: No module named 'lmdb'" in log:
                    has_logs = True
            time.sleep(1)

    # This is expected to pass, since the "setup" flag is passed
    with run_app_in_cloud(
        os.path.join(_PROJECT_ROOT, "examples/app_installation_commands"),
        app_name="app.py",
        extra_args=["--setup"],
        debug=True,
    ) as (_, _, fetch_logs, _):
        has_flow_logs = False
        while not has_flow_logs:
            for log in fetch_logs(["work"]):
                if "lmdb successfully installed" in log:
                    has_flow_logs = True
            time.sleep(1)

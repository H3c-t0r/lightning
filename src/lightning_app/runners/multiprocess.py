import multiprocessing
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

from lightning_app.api.http_methods import _add_tags_to_api, _validate_api
from lightning_app.core.api import start_server
from lightning_app.core.constants import APP_SERVER_IN_CLOUD
from lightning_app.runners.backends import Backend
from lightning_app.runners.runtime import Runtime
from lightning_app.storage.orchestrator import StorageOrchestrator
from lightning_app.utilities.app_helpers import is_overridden
from lightning_app.utilities.commands.base import _commands_to_api, _prepare_commands
from lightning_app.utilities.component import _set_flow_context, _set_frontend_context
from lightning_app.utilities.load_app import extract_metadata_from_app
from lightning_app.utilities.network import find_free_network_port
from lightning_app.utilities.port import disable_port


@dataclass
class MultiProcessRuntime(Runtime):

    """Runtime to launch the LightningApp into multiple processes.

    The MultiProcessRuntime will generate 1 process for each :class:`~lightning_app.core.work.LightningWork` and attach
    queues to enable communication between the different processes.
    """

    backend: Union[str, Backend] = "multiprocessing"

    def dispatch(self, *args: Any, on_before_run: Optional[Callable] = None, **kwargs: Any):
        """Method to dispatch and run the LightningApp."""
        try:
            _set_flow_context()

            # Note: In case the runtime is used in the cloud.
            self.host = "0.0.0.0" if APP_SERVER_IN_CLOUD else self.host

            self.app.backend = self.backend
            self.backend._prepare_queues(self.app)
            self.backend.resolve_url(self.app, "http://127.0.0.1")
            self.app._update_index_file()

            # set env variables
            os.environ.update(self.env_vars)

            # refresh the layout with the populated urls.
            self.app._update_layout()

            _set_frontend_context()
            for frontend in self.app.frontends.values():
                host = "localhost"
                port = find_free_network_port()
                frontend.start_server(host="localhost", port=port)
                frontend.flow._layout["target"] = f"http://{host}:{port}/{frontend.flow.name}"

            _set_flow_context()

            storage_orchestrator = StorageOrchestrator(
                self.app,
                self.app.request_queues,
                self.app.response_queues,
                self.app.copy_request_queues,
                self.app.copy_response_queues,
            )
            self.threads.append(storage_orchestrator)
            storage_orchestrator.setDaemon(True)
            storage_orchestrator.start()

            if self.start_server:
                self.app.should_publish_changes_to_api = True
                has_started_queue = self.backend.queues.get_has_server_started_queue()

                apis = []
                if is_overridden("configure_api", self.app.root):
                    apis = self.app.root.configure_api()
                    _validate_api(apis)
                    _add_tags_to_api(apis, ["app_api"])

                if is_overridden("configure_commands", self.app.root):
                    commands = _prepare_commands(self.app)
                    apis += _commands_to_api(commands)

                kwargs = dict(
                    apis=apis,
                    host=self.host,
                    port=self.port,
                    api_response_queue=self.app.api_response_queue,
                    api_publish_state_queue=self.app.api_publish_state_queue,
                    api_delta_queue=self.app.api_delta_queue,
                    has_started_queue=has_started_queue,
                    spec=extract_metadata_from_app(self.app),
                    root_path=self.app.root_path,
                )
                server_proc = multiprocessing.Process(target=start_server, kwargs=kwargs)
                self.processes["server"] = server_proc
                server_proc.start()
                # requires to wait for the UI to be clicked on.

                # wait for server to be ready
                has_started_queue.get()

            if on_before_run:
                on_before_run(self, self.app)

            # Connect the runtime to the application.
            self.app.connect(self)

            # Once the bootstrapping is done, running the rank 0
            # app with all the components inactive
            self.app._run()
        except KeyboardInterrupt:
            self.terminate()
            raise
        finally:
            self.terminate()

    def terminate(self):
        if APP_SERVER_IN_CLOUD:
            # Close all the ports open for the App within the App.
            ports = [self.port] + getattr(self.backend, "ports", [])
            for port in ports:
                disable_port(port)
        super().terminate()

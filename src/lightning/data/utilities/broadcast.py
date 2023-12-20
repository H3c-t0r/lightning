# Copyright The Lightning AI team.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import pickle
from functools import wraps
from typing import Any, Callable, Dict, Optional
from urllib.parse import urljoin

import requests

# for backwards compatibility
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from lightning.app.utilities.app_helpers import Logger

logger = Logger(__name__)

_CONNECTION_RETRY_TOTAL = 2880
_CONNECTION_RETRY_BACKOFF_FACTOR = 0.5
_DEFAULT_REQUEST_TIMEOUT = 30  # seconds


class CustomRetryAdapter(HTTPAdapter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.timeout = kwargs.pop("timeout", _DEFAULT_REQUEST_TIMEOUT)
        super().__init__(*args, **kwargs)

    def send(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        kwargs["timeout"] = kwargs.get("timeout", self.timeout)
        return super().send(request, **kwargs)


def _http_method_logger_wrapper(func: Callable) -> Callable:
    """Returns the function decorated by a wrapper that logs the message using the `log_function` hook."""

    @wraps(func)
    def wrapped(self: "HTTPClient", *args: Any, **kwargs: Any) -> Any:
        message = f"HTTPClient: Method: {func.__name__.upper()}, Path: {args[0]}\n"
        message += f"      Base URL: {self.base_url}\n"
        params = kwargs.get("query_params", {})
        if params:
            message += f"      Params: {params}\n"
        resp: requests.Response = func(self, *args, **kwargs)
        message += f"      Response: {resp.status_code} {resp.reason}"
        self.log_function(message)
        return resp

    return wrapped


def _response(r: Any, *args: Any, **kwargs: Any) -> Any:
    return r.raise_for_status()


class HTTPClient:
    """A wrapper class around the requests library which handles chores like logging, retries, and timeouts
    automatically."""

    def __init__(
        self,
        base_url: str,
        auth_token: Optional[str] = None,
        log_callback: Optional[Callable] = None,
        use_retry: bool = True,
    ) -> None:
        self.base_url = base_url
        retry_strategy = Retry(
            # wait time between retries increases exponentially according to: backoff_factor * (2 ** (retry - 1))
            # but the the maximum wait time is 120 secs. By setting a large value (2880), we'll make sure clients
            # are going to be alive for a very long time (~ 4 days) but retries every 120 seconds
            total=_CONNECTION_RETRY_TOTAL,
            backoff_factor=_CONNECTION_RETRY_BACKOFF_FACTOR,
            status_forcelist=[
                408,  # Request Timeout
                429,  # Too Many Requests
                500,  # Internal Server Error
                502,  # Bad Gateway
                503,  # Service Unavailable
                504,  # Gateway Timeout
            ],
        )
        adapter = CustomRetryAdapter(max_retries=retry_strategy, timeout=_DEFAULT_REQUEST_TIMEOUT)
        self.session = requests.Session()

        self.session.hooks = {"response": _response}

        if use_retry:
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

        if auth_token:
            self.session.headers.update({"Authorization": f"Bearer {auth_token}"})

        self.log_function = log_callback or self.log_function  # type: ignore

    @_http_method_logger_wrapper
    def get(self, path: str) -> Any:
        url = urljoin(self.base_url, path)
        return self.session.get(url)

    @_http_method_logger_wrapper
    def post(
        self, path: str, *, query_params: Optional[Dict] = None, data: Optional[bytes] = None, json: Any = None
    ) -> Any:
        url = urljoin(self.base_url, path)
        return self.session.post(url, data=data, params=query_params, json=json)

    @_http_method_logger_wrapper
    def delete(self, path: str) -> Any:
        url = urljoin(self.base_url, path)
        return self.session.delete(url)

    def log_function(self, message: str, *args: Any, **kwargs: Any) -> None:
        """This function is used to log the messages in the client, it can be overridden by caller to customise the
        logging logic.

        We enabled customisation here instead of just using `logger.debug` because HTTP logging can be very noisy, but
        it is crucial for finding bugs when we have them

        """


class ImmutableDistributedMap:
    """The ImmutableDistributedMap enables to create a distributed key value pair in the cloud.

    The first process to perform the set operation defines its value.

    """

    def __init__(self) -> None:
        # Get the token
        payload = {"apiKey": os.getenv("LIGHTNING_API_KEY"), "username": os.getenv("LIGHTNING_USERNAME")}
        url_login = os.getenv("LIGHTNING_CLOUD_URL", "https://lightning.ai") + "/v1/auth/login"
        res = requests.post(url_login, data=json.dumps(payload))
        if "token" not in res.json():
            raise RuntimeError(
                f"You haven't properly setup your environment variables with {url_login} and data: \n{payload}"
            )

        lightning_app_external_url = os.getenv("LIGHTNING_APP_EXTERNAL_URL")
        if lightning_app_external_url is None:
            raise RuntimeError("The `LIGHTNING_APP_EXTERNAL_URL` should be set.")

        self.external_client: HTTPClient = HTTPClient(
            lightning_app_external_url, auth_token=res.json()["token"], use_retry=True
        )

        lightning_app_state_url = os.getenv("LIGHTNING_APP_STATE_URL")
        if lightning_app_state_url is None:
            raise RuntimeError("The `LIGHTNING_APP_STATE_URL` should be set.")

        self.internal_client: HTTPClient = HTTPClient(
            lightning_app_state_url, auth_token=res.json()["token"], use_retry=True
        )

    def set_and_get(self, key: str, value: Any) -> Any:
        resp = self.external_client.post("/broadcast", json={"key": key, "value": pickle.dumps(value, 0).decode()})
        if resp.status_code != 200:
            resp = self.internal_client.post("/broadcast", json={"key": key, "value": pickle.dumps(value, 0).decode()})
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to broadcast the following {key=} {value=}.")
        return pickle.loads(bytes(resp.json()["value"], "utf-8"))


def broadcast_object(key: str, obj: Any) -> Any:
    """This function enables to broadcast object across machines."""
    if os.getenv("LIGHTNING_APP_EXTERNAL_URL") is not None:
        return ImmutableDistributedMap().set_and_get(key, obj)
    return obj

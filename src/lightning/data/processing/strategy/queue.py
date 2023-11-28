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

from abc import ABC, abstractmethod
from typing import Any, Dict


class Queue(ABC):
    def __init__(self):
        pass

    @abstractmethod
    def put(self, input: Dict[int, Any]) -> None:
        pass

    @abstractmethod
    def get(self) -> Dict[int, Any]:
        pass

    @abstractmethod
    def ack(self, index: int) -> None:
        pass

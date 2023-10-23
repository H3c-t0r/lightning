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

from typing import Any, List, Literal, Optional, Union

import numpy as np
from torch.utils.data import IterableDataset

from lightning.data.datasets.env import _DistributedEnv, _WorkerEnv
from lightning.data.streaming import Cache
from lightning.data.streaming.item_loader import BaseItemLoader
from lightning.data.streaming.sampler import ChunkedIndex


class StreamingDataset(IterableDataset):
    """The streaming dataset can be used once your data have been optimised using the DatasetOptimiser class."""

    def __init__(
        self,
        name: str,
        version: Optional[Union[int, Literal["latest"]]] = "latest",
        cache_dir: Optional[str] = None,
        item_loader: Optional[BaseItemLoader] = None,
        shuffle: bool = True,
        seed: int = 42,
    ) -> None:
        """The streaming dataset can be used once your data have been optimised using the DatasetOptimiser class.

        Arguments:
            name: The name of the optimised dataset.
            version: The version of the dataset to use.
            cache_dir: The cache dir where the data would be stored.
            item_loader: The logic to load an item from a chunk.
            shuffle: Whether to shuffle the data.
            seed: Random seed for shuffling.

        """
        super().__init__()
        self.cache = Cache(name=name, version=version, cache_dir=cache_dir, item_loader=item_loader, chunk_bytes=1)

        self.cache._reader._try_load_config()

        if not self.cache.filled:
            raise ValueError(f"The provided dataset `{name}` isn't filled up.")


        self.shuffle = shuffle
        self.distributed_env = _DistributedEnv.detect()
        self.worker_env: Optional[_WorkerEnv] = None

        chunk_intervals = self.cache.get_chunk_interval()
        self.L = sum([(interval[-1] - interval[0]) for interval in chunk_intervals])

        self.worker_chunks: List[int] = []
        self.worker_intervals: List[List[int]] = []
        self.current_indexes: List[int] = []
        self.chunk_index = 0
        self.index = 0
        self.has_triggered_download = False
        self.min_items_per_replica = None
        self.seed = seed
        self.num_iter = 0
        self.random_state = None

    def __len__(self) -> int:
        return self.L

    def __iter__(self) -> "StreamingDataset":
        self.random_state = np.random.RandomState(seed=self.seed + self.num_iter)
        chunk_intervals = self.cache.get_chunk_interval()
        indexes = range(len(chunk_intervals))
        shuffled_indexes = self.random_state.permutation(indexes) if self.shuffle else list(indexes)
        shuffled_chunk_intervals = np.asarray(chunk_intervals)[shuffled_indexes]

        chunks_per_replica: List[List[int]] = [[] for _ in range(self.distributed_env.world_size)]
        intervals_per_replica: List[List[List[int]]] = [[] for _ in range(self.distributed_env.world_size)]
        for index, (chunk_index, chunk_interval) in enumerate(zip(shuffled_indexes, shuffled_chunk_intervals)):
            replica_index = index % self.distributed_env.world_size
            chunks_per_replica[replica_index].append(chunk_index)
            intervals_per_replica[replica_index].append(chunk_interval)

        current_chunks = chunks_per_replica[self.distributed_env.global_rank % self.distributed_env.world_size]
        current_intervals = intervals_per_replica[self.distributed_env.global_rank % self.distributed_env.world_size]

        if self.worker_env is None:
            self.worker_env = _WorkerEnv.detect()

        self.worker_chunks = []
        self.worker_intervals = []

        print(self.worker_env)

        for i, (chunk_index, chunk_interval) in enumerate(zip(current_chunks, current_intervals)):
            if i % self.worker_env.world_size != self.worker_env.rank: continue
            self.worker_chunks.append(chunk_index)
            self.worker_intervals.append(chunk_interval)

        self.current_indexes = []
        self.chunk_index = 0
        self.num_iter += 1

        return self

    def __getitem__(self, index: Union[ChunkedIndex, int]) -> Any:
        if isinstance(index, int):
            index = ChunkedIndex(index, self.cache._get_chunk_index_from_index(index))
        return self.cache[index]

    def __next__(self) -> Any:
        # Lazily re-populate the interval to reduce memory usage.
        if len(self.current_indexes) == 0:
            if self.chunk_index == len(self.worker_intervals):
                raise StopIteration

            interval = self.worker_intervals[self.chunk_index]
            current_indexes = np.arange(0, interval[1] - interval[0])
            if self.shuffle:
                current_indexes = self.random_state.permutation(current_indexes)
            self.current_indexes = current_indexes.tolist()
            self.chunk_index += 1

        # Get the first index
        index = self.current_indexes.pop(0)

        # Call the `__getitem__` method.
        data = self.__getitem__(
            ChunkedIndex(
                index=index,
                chunk_index=self.worker_chunks[self.chunk_index - 1],
                chunk_indexes=None if self.has_triggered_download else self.worker_chunks,
            )
        )

        self.has_triggered_download = True
        self.index += 1

        return data

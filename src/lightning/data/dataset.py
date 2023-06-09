import os
import tempfile
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

from torch.utils.data import Dataset as TorchDataset

from lightning.data.authenticators import _DatasetBackend, LocalDatasetBackend, S3DatasetBackend
from lightning.data.dataset_index import get_index
from lightning.data.fileio import OpenCloudFileObj


class LightningDataset(TorchDataset, ABC):
    """Dataset wrapper for optimized dataloading.

    Arguments:

        data_source: path of data directory.

        backend: current options are "s3" or "local"

        path_to_index_file: path to index file that lists all file contents of the data_source.
    """

    def __init__(self, data_source: str, backend: str = "local", path_to_index_file: Optional[str] = None):
        super().__init__()
        self.data_source = data_source

        if not path_to_index_file:
            tmpdir = tempfile.mkdtemp()
            path_to_index_file = os.path.join(tmpdir, "index.txt")

        self.index_file = os.path.abspath(os.path.expandvars(os.path.expanduser(path_to_index_file)))

        self.files = self.get_index()

        self.authenticator = self._chose_authenticator(backend=backend)

        assert isinstance(self.authenticator, _DatasetBackend)

    def _chose_authenticator(self, backend: str):
        """Picks the correct authenticator for the provided backend."""
        if backend == "s3":
            return S3DatasetBackend()
        if backend == "local":
            return LocalDatasetBackend()
        raise ValueError("no valid backend found")

    def get_index(self) -> Tuple[str, ...]:
        """Gets existing index or triggers an index generation if it doesn't exist for the provided data_source.

        Returns:
            The contents of the index file (all the file paths in the data_source)
        """
        if not os.path.isfile(self.index_file):
            get_index(self.data_source, self.index_file)

        with open(self.index_file) as f:
            index = f.readlines()
        return (line.strip("\n") for line in index)

    def open(self, file: str, mode: str = "r", kwargs_for_open: Optional[Dict] = {}, **kwargs):
        return OpenCloudFileObj(
            file, mode=mode, kwargs_for_open={**self.authenticator.credentials(), **kwargs_for_open}, **kwargs
        )

    def __getitem__(self, idx: int) -> Any:
        """Get's item from the dataset at provided index.

        Returns:
            The loaded item
        """
        file_path = self.files[idx]

        try:
            with self.open(
                file_path,
                "rb",
            ) as stream:
                return self.load_sample(file_path, stream)
        except Exception as exc:
            self.authenticator.handle_error(exc)

    @abstractmethod
    def load_sample(self, file_path: str, stream: OpenCloudFileObj) -> Any:
        pass

    def __len__(self) -> int:
        return len(self.files)

from dataclasses import dataclass
from pathlib import Path
from typing import List

__MOUNT_IDENTIFIER__: str = "__mount__"
__MOUNT_PROTOCOLS__: List[str] = ["s3://"]


@dataclass
class Mount:
    """
    Arguments:
        source: The location which contains the external data which should be mounted in the
            running work. At the moment, only AWS S3 mounts are supported. This must be a full
            `s3` style identifier pointing to a bucket and (optionally) prefix to mount. For
            example: `s3://foo/bar/`.

        root_dir: An absolute directory path in the work where external data source should
            be mounted as a filesystem. This path should not already exist in your codebase.
            If not included, then the root_dir will be set to `/data/<last folder name in the bucket>`
    """

    source: str = ""
    root_dir: str = ""

    def __post_init__(self) -> None:

        for protocol in __MOUNT_PROTOCOLS__:
            if self.source.startswith(protocol):
                protocol = protocol
                break
        else:  # N.B. for-else loop
            raise ValueError(
                f"Unknown protocol for the mount 'source' argument '{self.source}`. The 'source' "
                f"string must start with one of the following prefixes: {__MOUNT_PROTOCOLS__}"
            )

        if protocol == "s3://" and not self.source.endswith("/"):
            raise ValueError(
                "S3 mounts must end in a trailing slash (`/`) to indicate a folder is being mounted. "
                f"Received: '{self.source}'. Mounting a single file is not currently supported."
            )

        if self.root_dir == "":
            self.root_dir = f"/data/{Path(self.source).stem}"

    @property
    def protocol(self) -> str:
        """The backing storage protocol indicated by this drive source."""
        for protocol in __MOUNT_PROTOCOLS__:
            if self.source.startswith(protocol):
                return protocol
        return ""

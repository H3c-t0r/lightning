import os
from typing import Dict, Protocol, runtime_checkable


@runtime_checkable
class _DatasetBackend(Protocol):
    """This class is used to detect if an object implements a valid dataset backend using `isinstance(obj,
    _DatasetBackend)`."""

    def credentials(self) -> Dict[str, str]:
        ...

    def handle_error(self, exc: Exception) -> None:
        ...


class S3DatasetBackend:
    """A backend handler for datasets stored on S3."""

    from botocore.credentials import RefreshableCredentials

    @staticmethod
    def get_aws_credentials() -> RefreshableCredentials:
        """Gets AWS credentials from the current IAM role.

        Returns:
            credentials object to be used for file reading
        """
        from botocore.credentials import InstanceMetadataProvider
        from botocore.utils import InstanceMetadataFetcher

        provider = InstanceMetadataProvider(iam_role_fetcher=InstanceMetadataFetcher(timeout=1000, num_attempts=2))

        credentials = provider.load()

        os.environ["AWS_ACCESS_KEY"] = credentials.access_key
        os.environ["AWS_SECRET_ACCESS_KEY"] = credentials.secret_key
        os.environ["AWS_SESSION_TOKEN"] = credentials.token

        return credentials

    def credentials(self) -> Dict[str, str]:
        if os.getenv("AWS_ACCESS_KEY") and os.getenv("AWS_SECRET_ACCESS_KEY"):
            return {"access_key": os.getenv("AWS_ACCESS_KEY"), "secret_key": os.getenv("AWS_SECRET_ACCESS_KEY")}

        return self.get_aws_credentials()

    def handle_error(self, exc: Exception) -> None:
        from botocore.exceptions import NoCredentialsError

        if isinstance(exc, NoCredentialsError):
            raise ValueError(
                "Unable to locate credentials. \
                Make sure you have set the following environment variables: \nAWS_ACCESS_KEY\\AWS_SECRET_ACCESS_KEY"
            ) from exc

        raise exc


class LocalDatasetBackend:
    """A backend handler for datasets stored locally."""

    def credentials(self) -> Dict:
        return {}

    def handle_error(self, exc: Exception) -> None:
        raise exc

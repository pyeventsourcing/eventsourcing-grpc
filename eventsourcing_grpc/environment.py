from typing import Optional, cast

from eventsourcing.system import System
from eventsourcing.utils import Environment, EnvType, resolve_topic


class GrpcEnvironment:
    def __init__(self, env: EnvType):
        self.env = env

    def get_server_address(self, name: str) -> str:
        env = Environment(name=name, env=self.env)
        address = env.get("GRPC_SERVER_ADDRESS")
        if not address:
            raise ValueError(f"{name} gRPC server address not found in environment")
        else:
            return address

    def get_max_pull_interval(self, name: str) -> float:
        env = Environment(name=name, env=self.env)
        max_pull_interval_str = env.get("MAX_PULL_INTERVAL")
        if not max_pull_interval_str:
            return 0
        try:
            max_pull_interval = float(max_pull_interval_str)
        except ValueError:
            raise ValueError(
                "Could not covert MAX_PULL_INTERVAL to float: {max_pull_interval_str}"
            ) from None
        else:
            return max_pull_interval

    def get_system(self) -> Optional[System]:
        system_topic = self.env.get("SYSTEM_TOPIC")
        if system_topic:
            return cast(System, resolve_topic(system_topic))
        else:
            return None

    def get_ssl_root_certificate_path(self) -> Optional[str]:
        return self.env.get("GRPC_SSL_ROOT_CERTIFICATE_PATH")

    def get_ssl_certificate_path(self) -> Optional[str]:
        return self.env.get("GRPC_SSL_CERTIFICATE_PATH")

    def get_ssl_private_key_path(self) -> Optional[str]:
        return self.env.get("GRPC_SSL_PRIVATE_KEY_PATH")

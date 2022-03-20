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

    def get_poll_interval(self, name: str) -> float:
        env = Environment(name=name, env=self.env)
        poll_interval_str = env.get("POLL_INTERVAL")
        if not poll_interval_str:
            return 0
        try:
            poll_interval = float(poll_interval_str)
        except ValueError:
            raise ValueError(
                f"Could not covert POLL_INTERVAL string to float: {poll_interval_str}"
            ) from None
        else:
            return poll_interval

    def get_system(self) -> Optional[System]:
        system_topic = self.env.get("SYSTEM_TOPIC")
        if system_topic:
            return cast(System, resolve_topic(system_topic))
        else:
            return None

    def get_ssl_root_certificate_path(self) -> Optional[str]:
        return self.env.get("SSL_ROOT_CERTIFICATE_PATH")

    def get_ssl_certificate_path(self) -> Optional[str]:
        return self.env.get("SSL_CERTIFICATE_PATH")

    def get_ssl_private_key_path(self) -> Optional[str]:
        return self.env.get("SSL_PRIVATE_KEY_PATH")

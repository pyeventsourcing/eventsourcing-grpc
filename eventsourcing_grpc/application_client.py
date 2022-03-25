from functools import wraps
from re import fullmatch
from time import time
from typing import Any, Generic, List, Optional, Sequence, Type, cast
from uuid import UUID

import grpc
from eventsourcing.application import NotificationLog, Section, TApplication
from eventsourcing.persistence import Notification, Transcoder
from eventsourcing.utils import EnvType, retry
from grpc import (
    Channel,
    ChannelConnectivity,
    FutureTimeoutError,
    StatusCode,
    channel_ready_future,
    local_channel_credentials,
    ssl_channel_credentials,
)
from grpc._channel import _InactiveRpcError

from eventsourcing_grpc.environment import GrpcEnvironment
from eventsourcing_grpc.protos.application_pb2 import (
    Empty,
    FollowRequest,
    LeadRequest,
    MethodRequest,
    NotificationsReply,
    NotificationsRequest,
    PromptRequest,
)
from eventsourcing_grpc.protos.application_pb2_grpc import ApplicationStub


class GrpcError(Exception):
    pass


class ServiceUnavailable(Exception):
    pass


class DeadlineExceeded(Exception):
    pass


class ChannelConnectTimeout(Exception):
    pass


class ChannelNotRunning(Exception):
    pass


def errors(f: Any) -> Any:
    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return f(*args, **kwargs)
        except _InactiveRpcError as e:
            # print(f"Error from {args[0].client_name} to {args[0].address}
            # calling func:", f, repr(e))

            if isinstance(e, _InactiveRpcError):
                if e._state.code == StatusCode.UNAVAILABLE:
                    raise ServiceUnavailable(repr(e)) from None
                elif e._state.code == StatusCode.DEADLINE_EXCEEDED:
                    raise DeadlineExceeded(repr(e)) from None
            raise GrpcError(repr(e)) from None

    return wrapper


class ApplicationClient(Generic[TApplication]):
    def __init__(
        self,
        owner_name: str,
        address: str,
        transcoder: Transcoder,
        request_deadline: int = 5,
        ssl_root_certificate_path: Optional[str] = None,
        ssl_private_key_path: Optional[str] = None,
        ssl_certificate_path: Optional[str] = None,
    ) -> None:
        self.owner_name = owner_name
        self.address = address
        if fullmatch("localhost:[0-9]+", self.address):
            self.credentials = local_channel_credentials()
        else:
            if ssl_root_certificate_path is None:
                raise ValueError("SSL root certificate path not given")
            if ssl_private_key_path is None:
                raise ValueError("SSL client private key path not given")
            if ssl_certificate_path is None:
                raise ValueError("SSL client certificate path not given")
            with open(ssl_root_certificate_path, "rb") as f:
                ssl_root_certificate = f.read()
            with open(ssl_private_key_path, "rb") as f:
                ssl_private_key = f.read()
            with open(ssl_certificate_path, "rb") as f:
                ssl_certificate = f.read()
            self.credentials = ssl_channel_credentials(
                root_certificates=ssl_root_certificate,
                private_key=ssl_private_key,
                certificate_chain=ssl_certificate,
            )

        self.transcoder = transcoder
        self.channel: Optional[Channel] = None
        self.request_deadline = request_deadline
        self.is_closed = False

    @property
    def app(self) -> TApplication:
        return cast(TApplication, ApplicationProxy(self))

    def connect(self, max_attempts: int = 0) -> None:
        """
        Connect client to server at given address.
        """
        attempts = 0
        while True:
            attempts += 1
            start = time()
            self.close()
            self.channel = grpc.secure_channel(
                self.address, credentials=self.credentials
            )
            self.channel.subscribe(self.handle_channel_state_change)
            future = channel_ready_future(self.channel)
            connect_deadline = min(0.5 * attempts, self.request_deadline)
            try:
                future.result(timeout=connect_deadline)
            except FutureTimeoutError:
                if max_attempts - attempts == 0:
                    print(
                        f"Client {self.owner_name} failed to connect to",
                        f"address {self.address}",
                        f"after {(time() - start):.2f}s",
                        f"(attempt {attempts}).",
                    )
                    raise ChannelConnectTimeout(self.address) from None
                else:
                    print(
                        f"Client {self.owner_name} timed out connecting to",
                        f"address {self.address}",
                        f"after {(time() - start):.2f}s",
                        f"(attempt {attempts}).",
                        "Retrying...",
                    )
                    continue
            self.stub = ApplicationStub(self.channel)
            print(
                f"Client {self.owner_name} connected successfully to",
                f"address {self.address}",
                f"after {(time() - start):.2f}s",
                f"(attempt {attempts})",
            )
            break

    def handle_channel_state_change(
        self, channel_connectivity: ChannelConnectivity
    ) -> None:
        pass
        # print("Channel state change:", channel_connectivity)

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        """
        Closes the client's GPRC channel.
        """
        self.is_closed = True
        if hasattr(self, "channel") and self.channel is not None:
            self.channel.close()
            self.channel = None
        # Todo: Deal with calls when stub is None.
        if hasattr(self, "stub"):
            self.stub = None

    @errors
    def ping(self) -> None:
        """
        Sends a Ping request to the server.
        """
        self.stub.Ping(Empty(), timeout=self.request_deadline)

    @errors
    def get_notifications(
        self,
        start: int,
        limit: int,
        stop: Optional[int] = None,
        topics: Sequence[str] = (),
    ) -> List[Notification]:
        request = NotificationsRequest(
            start=str(start),
            limit=str(limit),
            stop="" if stop is None else str(stop),
            topics=topics,
        )
        notifications_reply = self.stub.GetNotifications(
            request, timeout=self.request_deadline
        )
        assert isinstance(notifications_reply, NotificationsReply)
        return [
            Notification(
                id=int(n.id),
                originator_id=UUID(n.originator_id),
                originator_version=int(n.originator_version),
                topic=n.topic,
                state=n.state,
            )
            for n in notifications_reply.notifications
        ]

    @retry((DeadlineExceeded, ServiceUnavailable), max_attempts=10, wait=1)
    @errors
    def follow(self, name: str, address: str) -> None:
        self.stub.Follow(
            FollowRequest(name=name, address=address), timeout=self.request_deadline
        )

    @retry((DeadlineExceeded, ServiceUnavailable), max_attempts=10, wait=1)
    @errors
    def lead(self, name: str, address: str) -> None:
        self.stub.Lead(
            LeadRequest(name=name, address=address), timeout=self.request_deadline
        )

    @errors
    def prompt(self, name: str) -> None:
        self.stub.Prompt(
            PromptRequest(upstream_name=name), timeout=self.request_deadline
        )

    @retry((DeadlineExceeded, ServiceUnavailable), max_attempts=10, wait=1)
    @errors
    def call_application_method(self, method_name: str, args: Any, kwargs: Any) -> Any:
        request = MethodRequest(
            method_name=method_name,
            args=self.transcoder.encode(args),
            kwargs=self.transcoder.encode(kwargs),
        )
        response = self.stub.CallApplicationMethod(
            request, timeout=self.request_deadline
        )
        return self.transcoder.decode(response.data)


class ApplicationMethodProxy:
    def __init__(self, client: ApplicationClient[TApplication], method_name: str):
        self.client = client
        self.method_name = method_name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """
        Calls named method on server's application with given args.
        """
        return self.client.call_application_method(self.method_name, args, kwargs)


class ApplicationProxy:
    def __init__(self, client: ApplicationClient[TApplication]) -> None:
        self.client = client

    @property
    def notification_log(self) -> NotificationLog:
        return NotificationLogProxy(self)

    def __getattr__(self, item: str) -> ApplicationMethodProxy:
        return ApplicationMethodProxy(self.client, item)


class NotificationLogProxy(NotificationLog):
    def __init__(self, application_proxy: ApplicationProxy):
        self.application_proxy = application_proxy

    def __getitem__(self, section_id: str) -> Section:
        raise NotImplementedError()

    def select(
        self,
        start: int,
        limit: int,
        stop: Optional[int] = None,
        topics: Sequence[str] = (),
    ) -> List[Notification]:
        return self.application_proxy.client.get_notifications(
            start=start, limit=limit, stop=stop, topics=topics
        )


def create_client(
    owner_name: str, app_class: Type[TApplication], env: EnvType
) -> ApplicationClient[TApplication]:
    grpc_env = GrpcEnvironment(env=env)
    application = app_class(env=env)
    client: ApplicationClient[TApplication] = ApplicationClient(
        owner_name=owner_name,
        address=grpc_env.get_server_address(app_class.name),
        transcoder=application.construct_transcoder(),
        ssl_root_certificate_path=grpc_env.get_ssl_root_certificate_path(),
        ssl_private_key_path=grpc_env.get_ssl_private_key_path(),
        ssl_certificate_path=grpc_env.get_ssl_certificate_path(),
    )
    return client

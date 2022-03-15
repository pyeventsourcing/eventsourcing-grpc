from functools import wraps
from time import sleep
from typing import Any, Generic, List, cast
from uuid import UUID

import grpc
from eventsourcing.application import TApplication
from eventsourcing.persistence import Notification, Transcoder
from eventsourcing.utils import retry
from grpc import StatusCode
from grpc._channel import _InactiveRpcError

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


class ApplicationClient(Generic[TApplication]):
    def __init__(
        self,
        address: str,
        transcoder: Transcoder,
        request_deadline: int = 1,
    ) -> None:
        self.address = address
        self.transcoder = transcoder
        self.channel = None
        self.request_deadline = request_deadline

    @staticmethod
    def errors(f: Any) -> Any:
        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return f(*args, **kwargs)
            except _InactiveRpcError as e:
                if isinstance(e, _InactiveRpcError):
                    if e._state.code == StatusCode.UNAVAILABLE:
                        raise ServiceUnavailable(repr(e)) from None
                    elif e._state.code == StatusCode.DEADLINE_EXCEEDED:
                        raise DeadlineExceeded(repr(e)) from None
                raise GrpcError(repr(e)) from None

        return wrapper

    @property
    def app(self) -> TApplication:
        return cast(TApplication, ApplicationProxy(self))

    def connect(self) -> None:
        """
        Connect client to server at given address.

        Calls ping() until it gets a response, or timeout is reached.
        """
        self.close()
        self.channel = grpc.insecure_channel(self.address)
        # print("Created insecure channel ")
        self.stub = ApplicationStub(self.channel)
        sleep(0.1)
        self.ping(timeout=self.request_deadline)

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        """
        Closes the client's GPRC channel.
        """
        if self.channel is not None:
            self.channel.close()
            self.channel = None

    @retry((DeadlineExceeded, ServiceUnavailable), max_attempts=10, wait=1)
    @errors
    def ping(self, timeout: int = 5) -> None:
        """
        Sends a Ping request to the server.
        """
        self.stub.Ping(Empty(), timeout=timeout)

    @retry((DeadlineExceeded, ServiceUnavailable), max_attempts=10, wait=1)
    @errors
    def get_notifications(
        self, start: int, limit: int, topics: List[str]
    ) -> List[Notification]:
        request = NotificationsRequest(
            start=str(start), limit=str(limit), topics=topics
        )
        notifications_reply = self.stub.GetNotifications(request, timeout=5)
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

    @retry((DeadlineExceeded, ServiceUnavailable), max_attempts=10, wait=1)
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


class MethodProxy:
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

    def __getattr__(self, item: str) -> MethodProxy:
        return MethodProxy(self.client, item)

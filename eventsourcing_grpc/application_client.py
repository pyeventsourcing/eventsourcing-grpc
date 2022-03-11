from datetime import datetime
from time import sleep
from typing import Any, Generic, List, cast
from uuid import UUID

import grpc
from eventsourcing.application import TApplication
from eventsourcing.persistence import Notification, Transcoder
from grpc import RpcError

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


class ApplicationClient(Generic[TApplication]):
    def __init__(self, address: str, transcoder: Transcoder) -> None:
        self.address = address
        self.transcoder = transcoder
        self.channel = None
        # self.json_encoder = ObjectJSONEncoder()
        # self.json_decoder = ObjectJSONDecoder()

    @property
    def app(self) -> TApplication:
        return cast(TApplication, ApplicationProxy(self))

    def connect(self, timeout: float = 5) -> None:
        """
        Connect to client to server at given address.

        Calls ping() until it gets a response, or timeout is reached.
        """
        self.close()
        self.channel = grpc.insecure_channel(self.address)
        self.stub = ApplicationStub(self.channel)

        timer_started = datetime.now()
        while True:
            # Ping until get a response.
            try:
                self.ping()
            except RpcError as e:
                if timeout is not None:
                    timer_duration = (datetime.now() - timer_started).total_seconds()
                    if timer_duration > timeout:
                        err_msg = f"RPC error from '{self.address}'"
                        raise TimeoutError(err_msg) from e
                sleep(0.1)
                continue
            else:
                break

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        """
        Closes the client's GPRC channel.
        """
        if self.channel is not None:
            self.channel.close()

    def ping(self, timeout: int = 5) -> None:
        """
        Sends a Ping request to the server.
        """
        self.stub.Ping(Empty(), timeout=timeout)

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

    def follow(self, name: str, address: str) -> None:
        self.stub.Follow(FollowRequest(name=name, address=address), timeout=5)

    def lead(self, name: str, address: str) -> None:
        self.stub.Lead(LeadRequest(name=name, address=address), timeout=5)

    def prompt(self, name: str) -> None:
        self.stub.Prompt(PromptRequest(upstream_name=name), timeout=5)


class MethodProxy:
    def __init__(self, client: ApplicationClient[TApplication], method_name: str):
        self.client = client
        self.method_name = method_name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """
        Calls named method on server's application with given args.
        """
        request = MethodRequest(
            method_name=self.method_name,
            args=self.client.transcoder.encode(args),
            kwargs=self.client.transcoder.encode(kwargs),
        )
        response = self.client.stub.CallApplicationMethod(request, timeout=5)
        return self.client.transcoder.decode(response.data)


class ApplicationProxy:
    def __init__(self, client: ApplicationClient[TApplication]) -> None:
        self.client = client

    def __getattr__(self, item: str) -> MethodProxy:
        return MethodProxy(self.client, item)

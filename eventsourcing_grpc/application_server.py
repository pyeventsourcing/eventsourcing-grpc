from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from typing import Dict, List, Optional, Sequence

import grpc
from eventsourcing.application import (
    Application,
    NotificationLog,
    RecordingEvent,
    Section,
)
from eventsourcing.system import (
    EventProcessingError,
    Follower,
    Leader,
    RecordingEventReceiver,
)
from grpc._server import _Context as Context

from eventsourcing_grpc.application_client import ApplicationClient
from eventsourcing_grpc.protos.application_pb2 import (
    Empty,
    FollowRequest,
    LeadRequest,
    MethodReply,
    MethodRequest,
    Notification,
    NotificationsReply,
    NotificationsRequest,
    PromptRequest,
)
from eventsourcing_grpc.protos.application_pb2_grpc import (
    ApplicationServicer,
    add_ApplicationServicer_to_server,
)


class GRPCRemoteNotificationLog(NotificationLog):
    def __init__(self, client: ApplicationClient[Application]):
        self.client = client

    def __getitem__(self, section_id: str) -> Section:
        raise NotImplementedError()

    def select(
        self,
        start: int,
        limit: int,
        stop: Optional[int] = None,
        topics: Sequence[str] = (),
    ) -> List[Notification]:
        if stop is not None:
            raise NotImplementedError()
        return self.client.get_notifications(
            start=start, limit=limit, topics=list(topics)
        )


class GRPCRecordingEventReceiver(RecordingEventReceiver):
    def __init__(self, client: ApplicationClient[Application]):
        self.client = client

    def receive_recording_event(self, recording_event: RecordingEvent) -> None:
        self.client.prompt(name=recording_event.application_name)


class ApplicationService(ApplicationServicer):
    def __init__(self, application: Application) -> None:
        self.clients_lock = Lock()
        self.clients: Dict[str, ApplicationClient[Application]] = {}
        self.application = application
        self.transcoder = application.construct_transcoder()
        self.is_stopping = Event()
        self.has_started = Event()
        self.is_prompted = Event()
        self.prompted_names: List[str] = []
        self.prompted_names_lock = Lock()

    def Ping(self, request: Empty, context: Context) -> Empty:
        return Empty()

    def CallApplicationMethod(
        self, request: MethodRequest, context: Context
    ) -> MethodReply:
        method_name = request.method_name
        args = self.transcoder.decode(request.args)
        kwargs = self.transcoder.decode(request.kwargs)
        method = getattr(self.application, method_name)
        response = method(*args, **kwargs)
        reply = MethodReply()
        reply.data = self.transcoder.encode(response)
        return reply

    def GetNotifications(
        self, request: NotificationsRequest, context: Context
    ) -> NotificationsReply:
        start = int(request.start)
        limit = int(request.limit)
        topics = request.topics
        notifications = self.application.notification_log.select(
            start=start, limit=limit, topics=topics
        )
        return NotificationsReply(
            notifications=[
                Notification(
                    id=str(n.id),
                    originator_id=n.originator_id.hex,
                    originator_version=str(n.originator_version),
                    topic=n.topic,
                    state=n.state,
                )
                for n in notifications
            ]
        )

    def Follow(self, request: FollowRequest, context: Context) -> Empty:
        self.follow(request.name, request.address)
        return Empty()

    def follow(self, leader_name: str, leader_address: str) -> None:
        client = self.get_client(leader_address)
        notification_log = GRPCRemoteNotificationLog(client=client)
        assert isinstance(self.application, Follower)
        self.application.follow(name=leader_name, log=notification_log)

    def get_client(self, address: str) -> ApplicationClient[Application]:
        with self.clients_lock:
            try:
                client = self.clients[address]
            except KeyError:
                client = ApplicationClient(
                    address=address,
                    transcoder=self.application.construct_transcoder(),
                )
                client.connect(timeout=5)
                self.clients[address] = client
            return client

    def Lead(self, request: LeadRequest, context: Context) -> Empty:
        address = request.address
        self.lead(address)
        return Empty()

    def lead(self, address: str) -> None:
        client = self.get_client(address)
        follower = GRPCRecordingEventReceiver(client=client)
        assert isinstance(self.application, Leader)
        self.application.lead(follower=follower)

    def Prompt(self, request: PromptRequest, context: Context) -> Empty:
        leader_name = request.upstream_name
        with self.prompted_names_lock:
            if leader_name not in self.prompted_names:
                self.prompted_names.append(leader_name)
            self.is_prompted.set()
        return Empty()

    def run(self) -> None:
        assert isinstance(self.application, Follower)
        while not self.is_stopping.is_set():
            self.is_prompted.wait()

            with self.prompted_names_lock:
                prompted_names = self.prompted_names
                self.prompted_names = []
                self.is_prompted.clear()
            for name in prompted_names:
                try:
                    self.application.pull_and_process(name)
                except Exception as e:
                    error = EventProcessingError(str(e))
                    error.__cause__ = e
                    # Todo: Log the error.
                    print(error)

    def stop(self) -> None:
        self.is_stopping.set()
        self.is_prompted.set()


class ApplicationServer:
    def __init__(self, application: Application, address: str) -> None:
        self.application = application
        self.address = address

    def start(self) -> None:
        """
        Starts gRPC server.
        """
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.server = grpc.server(self.executor)
        self.service = ApplicationService(self.application)
        self.executor.submit(self.service.run)
        add_ApplicationServicer_to_server(self.service, self.server)
        self.server.add_insecure_port(self.address)
        self.server.start()

    def stop(self, grace: int = 1) -> None:
        self.service.stop()
        self.server.stop(grace=grace)

    def __del__(self) -> None:
        self.stop()

import traceback
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import sleep, time
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

from eventsourcing_grpc.application_client import ApplicationClient, GrpcError
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
        try:
            self.client.prompt(name=recording_event.application_name)
        except GrpcError:
            # Todo: Log failure to prompt downstream application.
            pass


class ApplicationService(ApplicationServicer):
    def __init__(self, application: Application, poll_interval: float) -> None:
        self.last_pull_times: Dict[str, float] = {}
        self.application = application
        self.poll_interval = poll_interval
        self.clients_lock = Lock()
        self.clients: Dict[str, ApplicationClient[Application]] = {}
        self.transcoder = application.construct_transcoder()
        self.is_prompted = Event()
        self.prompted_names: List[str] = []
        self.prompted_names_lock = Lock()
        self.is_stopping = Event()
        self.has_started = Event()

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
                    client_name=self.application.name,
                    address=address,
                    transcoder=self.application.construct_transcoder(),
                )
                client.connect()
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
        self.prompt(leader_name)
        return Empty()

    def prompt(self, leader_name: str) -> None:
        with self.prompted_names_lock:
            if leader_name not in self.prompted_names:
                self.prompted_names.append(leader_name)
                self.is_prompted.set()

    def run(self) -> None:
        while not self.is_stopping.is_set():
            self.is_prompted.wait()

            with self.prompted_names_lock:
                prompted_names = self.prompted_names
                self.prompted_names = []
                self.is_prompted.clear()
            for name in prompted_names:
                try:
                    assert isinstance(self.application, Follower)
                    if name not in self.application.readers:
                        # print(f"{self.application.name} can't pull
                        # from {name} without reader")
                        self.prompt(name)
                        sleep(1)
                    else:
                        self.application.pull_and_process(name)
                        self.last_pull_times[name] = time()
                except Exception as e:
                    error = EventProcessingError(str(e))
                    error.__cause__ = e
                    # Todo: Log the error.
                    print(
                        f"Error in {self.application.name} processing {name} events:",
                        traceback.format_exc(),
                    )
                    self.prompt(name)
                    # print("Sleeping after error....")
                    sleep(1)

    def self_prompt(self) -> None:
        if isinstance(self.application, Follower):
            while not self.is_stopping.wait(timeout=self.poll_interval):
                for leader_name in self.application.readers:
                    last_pull_time = self.last_pull_times.get(leader_name, 0)
                    if time() - last_pull_time > self.poll_interval:
                        # print(f"{time()} {self.application.name} polling {leader_name}")
                        self.prompt(leader_name)

    def stop(self) -> None:
        self.is_stopping.set()
        self.is_prompted.set()
        for client in self.clients.values():
            client.close()


class ApplicationServer:
    def __init__(
        self, application: Application, address: str, poll_interval: float
    ) -> None:
        self.has_stopped = Event()
        self.application = application
        self.address = address
        self.poll_interval = poll_interval
        self.maximum_concurrent_rpcs = None
        self.compression = None

    def start(self) -> None:
        """
        Starts gRPC server.
        """
        # print("Starting application server:", self.application.name)
        self.executor = ThreadPoolExecutor()
        self.server = grpc.server(
            thread_pool=self.executor,
            maximum_concurrent_rpcs=self.maximum_concurrent_rpcs,
            compression=self.compression,
        )
        self.service = ApplicationService(
            self.application, poll_interval=self.poll_interval
        )
        self.executor.submit(self.service.run)
        self.executor.submit(self.service.self_prompt)
        add_ApplicationServicer_to_server(self.service, self.server)
        self.server.add_insecure_port(self.address)
        self.server.start()
        # print("Started application server:", self.application.name)

    def stop(self, grace: int = 30) -> None:
        self.has_stopped.set()
        # print("Stopping application server:", self.application.name)
        self.service.stop()
        self.server.stop(grace=grace)
        # print("Stopped application server:", self.application.name)

    def __del__(self) -> None:
        if not self.has_stopped.is_set():
            self.stop()

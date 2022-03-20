import traceback
from concurrent.futures import ThreadPoolExecutor
from re import fullmatch
from threading import Event, Lock
from time import sleep, time
from typing import Dict, List, Optional, Sequence, Type

import grpc
from eventsourcing.application import (
    Application,
    NotificationLog,
    RecordingEvent,
    Section,
    TApplication,
)
from eventsourcing.system import (
    EventProcessingError,
    Follower,
    Leader,
    RecordingEventReceiver,
)
from eventsourcing.utils import EnvType
from grpc import ServicerContext, local_server_credentials

from eventsourcing_grpc.application_client import (
    ApplicationClient,
    GrpcError,
    create_client,
)
from eventsourcing_grpc.environment import GrpcEnvironment
from eventsourcing_grpc.protos.application_pb2 import (
    Empty,
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
        self.transcoder = application.construct_transcoder()
        self.is_prompted = Event()
        self.prompted_names: List[str] = []
        self.prompted_names_lock = Lock()
        self.is_stopping = Event()
        self.has_started = Event()

    def Ping(self, request: Empty, context: ServicerContext) -> Empty:
        return Empty()

    def CallApplicationMethod(
        self, request: MethodRequest, context: ServicerContext
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
        self, request: NotificationsRequest, context: ServicerContext
    ) -> NotificationsReply:
        start = int(request.start)
        limit = int(request.limit)
        stop = int(request.stop) if request.stop else None
        topics = request.topics
        notifications = self.application.notification_log.select(
            start=start, limit=limit, stop=stop, topics=topics
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

    def Prompt(self, request: PromptRequest, context: ServicerContext) -> Empty:
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
            # print(self.application.name, "prompted...")

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
        if self.poll_interval > 0 and isinstance(self.application, Follower):
            while not self.is_stopping.wait(timeout=self.poll_interval):
                for leader_name in self.application.readers:
                    last_pull_time = self.last_pull_times.get(leader_name, 0)
                    if time() - last_pull_time > self.poll_interval:
                        print(
                            self.application.name,
                            "self-prompted to pull from",
                            leader_name,
                        )
                        self.prompt(leader_name)

    def stop(self) -> None:
        self.is_stopping.set()
        self.is_prompted.set()


class ApplicationServer:
    def __init__(self, app_class: Type[Application], env: EnvType) -> None:
        self.grpc_env = GrpcEnvironment(env=env)
        self.application = app_class(env=env)
        self.start_stop_lock = Lock()
        self.address = self.grpc_env.get_server_address(self.application.name)
        if fullmatch("localhost:[0-9]+", self.address):
            self.server_credentials = local_server_credentials()
        else:
            ssl_private_key_path = self.grpc_env.get_ssl_private_key_path()
            ssl_certificate_path = self.grpc_env.get_ssl_certificate_path()
            ssl_root_certificate_path = self.grpc_env.get_ssl_root_certificate_path()
            if ssl_private_key_path is None:
                raise ValueError("SSL server private key path not given")
            if ssl_certificate_path is None:
                raise ValueError("SSL server certificate path not given")
            if ssl_root_certificate_path is None:
                raise ValueError("SSL root certificate path not given")
            with open(ssl_private_key_path, "rb") as f:
                ssl_private_key = f.read()
            with open(ssl_certificate_path, "rb") as f:
                ssl_certificate = f.read()
            with open(ssl_root_certificate_path, "rb") as f:
                ssl_root_certificate = f.read()

            # create server credentials
            private_key_certificate_chain_pairs = ((ssl_private_key, ssl_certificate),)
            self.server_credentials = grpc.ssl_server_credentials(
                private_key_certificate_chain_pairs=private_key_certificate_chain_pairs,
                root_certificates=ssl_root_certificate,
                require_client_auth=True,
            )

        self.clients_lock = Lock()
        self.clients: Dict[Type[Application], ApplicationClient[Application]] = {}
        self.poll_interval = self.grpc_env.get_poll_interval(self.application.name)
        self.maximum_concurrent_rpcs = None
        self.compression = None
        self.has_started = Event()
        self.has_stopped = Event()

    def start(self) -> None:
        """
        Starts gRPC server.
        """
        with self.start_stop_lock:
            if self.has_started.is_set():
                return
            self.has_started.set()
            self.has_stopped.clear()

            # print("Starting application server:", self.application.name)
            self.executor = ThreadPoolExecutor()
            self.grpc_server = grpc.server(
                thread_pool=self.executor,
                maximum_concurrent_rpcs=self.maximum_concurrent_rpcs,
                compression=self.compression,
            )
            self.service = ApplicationService(
                self.application, poll_interval=self.poll_interval
            )
            add_ApplicationServicer_to_server(self.service, self.grpc_server)
            self.grpc_server.add_secure_port(self.address, self.server_credentials)
            self.grpc_server.start()
            self.executor.submit(self.lead_and_follow)
            self.executor.submit(self.service.run)
            self.executor.submit(self.service.self_prompt)

    def lead_and_follow(self) -> None:
        system = self.grpc_env.get_system()
        if system is not None:
            for follower_name in system.leads[self.application.name]:
                follower_cls = system.follower_cls(follower_name)
                client = self.get_client(
                    owner_name=self.application.name,
                    app_class=follower_cls,
                    env=self.grpc_env.env,
                )
                recording_event_receiver = GRPCRecordingEventReceiver(client=client)
                assert isinstance(self.application, Leader)
                self.application.lead(follower=recording_event_receiver)

            for leader_name in system.follows[self.application.name]:
                leader_cls = system.follower_cls(leader_name)
                client = self.get_client(
                    owner_name=self.application.name,
                    app_class=leader_cls,
                    env=self.grpc_env.env,
                )
                notification_log = GRPCRemoteNotificationLog(client=client)
                assert isinstance(self.application, Follower)
                self.application.follow(name=leader_name, log=notification_log)

    def get_client(
        self, owner_name: str, app_class: Type[TApplication], env: EnvType
    ) -> ApplicationClient[Application]:
        with self.clients_lock:
            try:
                client = self.clients[app_class]
            except KeyError:
                client = create_client(
                    owner_name=owner_name, app_class=app_class, env=env
                )
                client.connect()
                self.clients[app_class] = client
            return client

    def wait_for_termination(self) -> None:
        self.grpc_server.wait_for_termination()

    def stop(self, grace: int = 30) -> None:
        with self.start_stop_lock:
            if not self.has_started.is_set():
                return
            self.has_started.clear()
            self.has_stopped.set()
            for client in self.clients.values():
                client.close()
            # print("Stopping application server:", self.application.name)
            self.service.stop()
            self.grpc_server.stop(grace=grace)
            # print("Stopped application server:", self.application.name)

    def __del__(self) -> None:
        if hasattr(self, "has_stopped") and not self.has_stopped.is_set():
            self.stop()

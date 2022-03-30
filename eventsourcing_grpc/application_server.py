from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from re import fullmatch
from subprocess import Popen, TimeoutExpired
from threading import Event, Lock, RLock, Thread, Timer
from time import monotonic, sleep
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
from eventsourcing.utils import EnvType, get_topic
from grpc import ServicerContext, local_server_credentials

from eventsourcing_grpc.application_client import (
    ApplicationClient,
    ClientClosedError,
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
    def __init__(self, leader_client: ApplicationClient[Application]):
        self.leader_client = leader_client

    def __getitem__(self, section_id: str) -> Section:
        raise NotImplementedError()

    def select(
        self,
        start: int,
        limit: int,
        stop: Optional[int] = None,
        topics: Sequence[str] = (),
    ) -> List[Notification]:
        return self.leader_client.get_notifications(
            start=start, limit=limit, stop=stop, topics=list(topics)
        )


class GRPCRecordingEventReceiver(RecordingEventReceiver):
    def __init__(
        self,
        leader_name: str,
        follower_client: ApplicationClient[Application],
        min_interval: float = 0.05,  # Todo: Make this configurable.
    ):
        self.leader_name = leader_name
        self.follower_client = follower_client
        self.last_prompt = monotonic()
        self.timer_lock = RLock()
        self.timer: Optional[Timer] = None
        self.min_interval = min_interval

    def receive_recording_event(self, recording_event: RecordingEvent) -> None:
        with self.timer_lock:
            time_since_last_prompt = monotonic() - self.last_prompt
            if time_since_last_prompt < self.min_interval:
                wait_to_prompt = self.min_interval - time_since_last_prompt
                # print("Wait to prompt:", wait_to_prompt)
                if self.timer is None:
                    self.start_timer(wait_to_prompt)
            else:
                if self.timer is None:
                    self.prompt_follower()

    def start_timer(self, wait_to_prompt: float) -> None:
        timer = Timer(interval=wait_to_prompt, function=self.prompt_follower)
        timer.daemon = False
        timer.start()
        self.timer = timer

    def prompt_follower(self) -> None:
        with self.timer_lock:
            try:
                self.follower_client.prompt(name=self.leader_name)
            except (GrpcError, ValueError, AttributeError, ClientClosedError):
                # Todo: Log failure to prompt downstream application,
                #  possibly the connection is already closed.
                pass
            finally:
                self.last_prompt = monotonic()
                self.stop_timer()

    def stop_timer(self) -> None:
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def __del__(self) -> None:
        with self.timer_lock:
            self.stop_timer()


class ApplicationService(ApplicationServicer):
    def __init__(
        self, application: Application, max_pull_interval: float, is_stopping: Event
    ) -> None:
        self.application = application
        self.max_pull_interval = max_pull_interval
        self.is_stopping = is_stopping
        self.transcoder = application.construct_transcoder()
        self.has_started = Event()
        self.prompted_names: List[str] = []
        self.prompted_names_lock = Lock()
        self.is_prompted = Event()
        self.last_prompt_times: Dict[str, float] = {}
        self.pull_and_process_loop_has_stopped = Event()
        self.self_prompt_loop_has_stopped = Event()

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
                self.last_prompt_times[leader_name] = monotonic()

    def pull_and_process_loop(self) -> None:
        try:
            while not self.is_stopping.is_set():
                # Keep checking if is stopped.
                if not self.is_prompted.wait(timeout=1):
                    continue

                with self.prompted_names_lock:
                    prompted_names = self.prompted_names
                    self.prompted_names = []
                    self.is_prompted.clear()
                for name in prompted_names:
                    if self.is_stopping.is_set():
                        break
                    try:
                        assert isinstance(self.application, Follower)
                        if name not in self.application.readers:
                            # print(f"{self.application.name} can't pull
                            # from {name} without reader")
                            self.prompt(name)
                            sleep(1)
                        else:
                            self.application.pull_and_process(name)
                    except Exception as e:
                        if self.is_stopping.is_set():
                            raise
                        error = EventProcessingError(str(e))
                        error.__cause__ = e
                        # Todo: Log the error.
                        print(
                            f"Error in {self.application.name} processing"
                            f" {name} events:",
                            traceback.format_exc(),
                        )
                        self.prompt(name)
                        # print("Sleeping after error....")
                        sleep(1)
        except BaseException:
            if self.is_stopping.is_set():
                pass
            else:
                raise
        finally:
            self.pull_and_process_loop_has_stopped.set()

    def self_prompt_loop(self) -> None:
        try:
            if self.max_pull_interval > 0 and isinstance(self.application, Follower):
                wait_timeout = self.max_pull_interval
                while not self.is_stopping.wait(timeout=wait_timeout):
                    wait_timeout = self.max_pull_interval
                    for leader_name in self.application.readers:
                        last_time = self.last_prompt_times.get(leader_name, 0)
                        time_remaining = (
                            last_time + self.max_pull_interval - monotonic()
                        )
                        if (
                            time_remaining < 0.1
                        ):  # wait(timeout) sometimes returns early
                            self.prompt(leader_name)
                        else:
                            wait_timeout = min(time_remaining, wait_timeout)
        except BaseException:
            if self.is_stopping.is_set():
                pass
            else:
                raise
        finally:
            self.self_prompt_loop_has_stopped.set()

    def stop(self) -> None:
        self.is_stopping.set()
        self.is_prompted.set()
        self.self_prompt_loop_has_stopped.wait()
        self.pull_and_process_loop_has_stopped.wait()


class ApplicationServer:
    def __init__(
        self,
        app_class: Type[Application],
        env: EnvType,
        is_stopping: Optional[Event] = None,
        max_workers: Optional[int] = None,
    ) -> None:
        self.max_workers = max_workers
        self.lock = Lock()
        self.is_starting = Event()
        self.has_started = Event()
        self.is_stopping = is_stopping or Event()
        self.init_lead_and_follow_has_stopped = Event()
        self.has_stopped = Event()
        self.grpc_env = GrpcEnvironment(env=env)
        self.application = app_class(env=env)
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
        self.max_pull_interval = self.grpc_env.get_max_pull_interval(
            self.application.name
        )
        self.maximum_concurrent_rpcs = None
        self.compression = None

    def start(self) -> None:
        """
        Starts gRPC server.
        """
        with self.lock:
            if self.has_started.is_set() or self.is_starting.is_set():
                # print(self.application.name, "already running")
                return
            self.has_stopped.clear()
            self.init_lead_and_follow_has_stopped.clear()
            self.is_starting.set()
        try:
            # print("Starting application server:", self.application.name)
            # print(self.application.name, "starting threadpool executor")
            self.executor: ThreadPoolExecutor = ThreadPoolExecutor(
                max_workers=self.max_workers
            )
            self.executor.submit(self.stop_on_is_stopping)
            # print(self.application.name, "started threadpool executor")
            # print(self.application.name, "starting grpc server")
            self.grpc_server = grpc.server(
                thread_pool=self.executor,
                maximum_concurrent_rpcs=self.maximum_concurrent_rpcs,
                compression=self.compression,
            )
            # print(self.application.name, "creating application service")
            self.service = ApplicationService(
                self.application,
                max_pull_interval=self.max_pull_interval,
                is_stopping=self.is_stopping,
            )
            add_ApplicationServicer_to_server(self.service, self.grpc_server)
            # print(self.application.name, "adding secure port")
            self.grpc_server.add_secure_port(self.address, self.server_credentials)
            # print(self.application.name, "starting grpc server")
            self.grpc_server.start()

            # print(self.application.name, "submitting jobs to executor")
            self.executor.submit(self.init_lead_and_follow)
            self.executor.submit(self.service.pull_and_process_loop)
            self.executor.submit(self.service.self_prompt_loop)
            print("Started application server", self.application.name)
        finally:
            with self.lock:
                self.has_started.set()
                self.is_starting.clear()

    def stop_on_is_stopping(self) -> None:
        self.has_started.wait()
        self.is_stopping.wait()
        self._stop()

    def init_lead_and_follow(self) -> None:
        try:
            system = self.grpc_env.get_system()
            if system is not None:
                for follower_name in system.leads[self.application.name]:
                    follower_cls = system.follower_cls(follower_name)
                    follower_client = self.get_client(
                        owner_name=self.application.name,
                        app_class=follower_cls,
                        env=self.grpc_env.env,
                    )
                    recording_event_receiver = GRPCRecordingEventReceiver(
                        leader_name=self.application.name,
                        follower_client=follower_client,
                    )
                    assert isinstance(self.application, Leader)
                    self.application.lead(follower=recording_event_receiver)

                for leader_name in system.follows[self.application.name]:
                    leader_cls = system.follower_cls(leader_name)
                    leader_client = self.get_client(
                        owner_name=self.application.name,
                        app_class=leader_cls,
                        env=self.grpc_env.env,
                    )
                    notification_log = GRPCRemoteNotificationLog(
                        leader_client=leader_client
                    )
                    assert isinstance(self.application, Follower)
                    self.application.follow(name=leader_name, log=notification_log)
                    # Prompt to catch up on anything new.
                    self.service.prompt(leader_name)
        except BaseException:
            if self.is_stopping.is_set():
                pass
            else:
                raise
        finally:
            self.init_lead_and_follow_has_stopped.set()

    def get_client(
        self, owner_name: str, app_class: Type[TApplication], env: EnvType
    ) -> ApplicationClient[Application]:
        with self.clients_lock:
            if self.is_stopping.is_set() or self.has_stopped.is_set():
                raise AssertionError("Server has been stopped already")

            try:
                client = self.clients[app_class]
            except KeyError:
                client = create_client(
                    owner_name=owner_name,
                    app_class=app_class,
                    env=env,
                    is_stopping=self.is_stopping,
                )
                client.connect()
                if self.is_stopping.is_set() or self.has_stopped.is_set():
                    client.close()
                    client.assert_client_not_closed()
                else:
                    self.clients[app_class] = client
            return client

    def wait_for_termination(self) -> None:
        if not self.is_stopping.is_set():
            self.grpc_server.wait_for_termination()

    def stop(self, wait: bool = False) -> None:
        self.is_stopping.set()
        if wait:
            self.has_stopped.wait()

    def _stop(self) -> None:
        try:
            self.service.self_prompt_loop_has_stopped.wait()
            self.service.pull_and_process_loop_has_stopped.wait()
            self.init_lead_and_follow_has_stopped.wait()
            if not self.has_started.is_set():
                # print("stop() is_running not set, returning")
                return

            self.grpc_server.stop(grace=5)

            # print("stop() calling stop() on service...")
            self.service.stop()
            # print("stop() called stop() on service")
            # print("stop() shutting down executor")
            # Todo: Wait or not wait? that is the question (here).
            # self.executor.shutdown(wait=True, cancel_futures=True)  # type: ignore
            # print("stop() shut down executor")
            # print("stop() closing clients...")
            for client in self.clients.values():
                client.close()
            # print("stop() closed clients")

        finally:
            print("Stopped application server", self.application.name)

            with self.lock:
                self.has_started.clear()
                self.is_stopping.clear()
                self.has_stopped.set()

    def __del__(self) -> None:
        if hasattr(self, "has_stopped") and not self.has_stopped.is_set():
            self.stop()


def start_server(
    app_class: Type[Application], env: EnvType, is_stopping: Optional[Event] = None
) -> ApplicationServer:
    server = ApplicationServer(app_class=app_class, env=env, is_stopping=is_stopping)
    server.start()
    return server


class ServerSubprocess(Thread):
    def __init__(
        self, app_class: Type[Application], env: EnvType, has_errored: Event
    ) -> None:
        super().__init__(daemon=True)
        self.app_class = app_class
        self.env = env
        self.has_errored = has_errored
        self.has_started = Event()
        self.is_terminating = Event()
        self.has_stopped = Event()
        self.proc: Optional[Popen[bytes]] = None
        self.command_queue: "Queue[str]" = Queue()
        self.command_thread = Thread(target=self.command_loop, daemon=True)
        self.command_lock = Lock()

    def command_loop(self) -> None:
        while True:
            command = self.command_queue.get()
            if command == "TERMINATE":
                with self.command_lock:
                    if self.is_terminating.is_set():
                        return
                    else:
                        self.is_terminating.set()

                if self.proc is not None and self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=10)
                    except TimeoutExpired:
                        print("Timed out waiting for process to terminate. Killing....")
                        self.proc.kill()
                        self.proc.wait(timeout=1)
                        # print("Processor exit code: %s" % process.poll())
                self.has_stopped.set()
                break
            else:
                raise NotImplementedError(f"Command not supported: {command}")

    def run(self) -> None:
        self.command_thread.start()
        try:
            env = dict(self.env)
            env["APPLICATION_TOPIC"] = get_topic(self.app_class)
            self.proc = Popen(
                ["eventsourcing_grpc_server"],
                close_fds=True,
                env=env,
            )
        except BaseException:
            self.stop()
            self.has_errored.set()
            self.has_started.set()
            raise
        else:
            self.has_started.set()
            if self.proc.wait():
                self.stop()
                self.has_errored.set()
        # print("Subprocess exited, status code", self.process.returncode)

    def stop(self) -> None:
        """
        Stops given gRPC process.
        """
        self.command_queue.put("TERMINATE")


def start_server_subprocess(
    app_class: Type[Application], env: EnvType, has_errored: Event
) -> ServerSubprocess:
    thread = ServerSubprocess(app_class, env, has_errored)
    thread.start()
    return thread

from itertools import count
from threading import Event
from typing import Dict, Optional, Type, cast

from eventsourcing.application import Application, TApplication
from eventsourcing.system import Runner, System
from eventsourcing.utils import EnvType

from eventsourcing_grpc.application_client import ApplicationClient
from eventsourcing_grpc.application_server import ApplicationServer


class GrpcRunner(Runner):
    """
    System runner that uses gRPC to communicate between process applications.
    """

    def __init__(self, system: System, env: Optional[EnvType] = None):
        """
        Initialises runner with the given :class:`System`.
        """
        super().__init__(system=system, env=env)
        self.apps: Dict[str, Application] = {}
        self.servers: Dict[str, ApplicationServer] = {}
        self.clients: Dict[str, ApplicationClient[Application]] = {}
        self.addresses: Dict[str, str] = {}
        self.port_generator = count(start=50051)
        self.has_errored = Event()

        # Construct followers.
        for follower_name in self.system.followers:
            follower_class = self.system.follower_cls(follower_name)
            self.start_application(follower_class)

        # Construct non-follower leaders.
        for leader_name in self.system.leaders_only:
            leader_cls = self.system.leader_cls(leader_name)
            self.start_application(leader_cls)

        # Construct singles.
        for name in self.system.singles:
            single_cls = self.system.get_app_cls(name)
            self.start_application(single_cls)

    def start_application(self, app_class: Type[Application]) -> None:
        application = app_class(env=self.env)
        self.apps[app_class.name] = application
        address = self.create_address()
        self.addresses[app_class.name] = address
        server = ApplicationServer(application=application, address=address)
        server.start()
        self.servers[app_class.name] = server
        client: ApplicationClient[Application] = ApplicationClient(
            address=address, transcoder=application.construct_transcoder()
        )
        self.clients[app_class.name] = client
        client.connect(timeout=1)

    def create_address(self) -> str:
        """
        Creates a new address for a gRPC server.
        """
        return "localhost:%s" % next(self.port_generator)

    def start(self) -> None:
        super().start()

        # Lead and follow.
        for edge in self.system.edges:
            leader_name = edge[0]
            follower_name = edge[1]
            leader_client = self.clients[leader_name]
            leader_address = self.addresses[leader_name]
            follower_client = self.clients[follower_name]
            follower_address = self.addresses[follower_name]
            follower_client.follow(leader_name, leader_address)
            leader_client.lead(follower_name, follower_address)

    def get_client(self, cls: Type[TApplication]) -> ApplicationClient[TApplication]:
        return cast(ApplicationClient[TApplication], self.clients[cls.name])

    def get(self, cls: Type[TApplication]) -> TApplication:
        return cast(ApplicationClient[TApplication], self.clients[cls.name]).app

    def get_app(self, cls: Type[TApplication]) -> TApplication:
        return cast(TApplication, self.apps[cls.name])

    def stop(self) -> None:
        for client in self.clients.values():
            client.close()
        self.clients.clear()
        for server in self.servers.values():
            server.stop()
        self.servers.clear()
        for app in self.apps.values():
            app.close()
        self.apps.clear()
        self.addresses.clear()

    def __del__(self) -> None:
        print("runner deleted")
        self.stop()


#     def start_processor(
#         self,
#         application_topic,
#         pipeline_id,
#         infrastructure_topic,
#         setup_table,
#         address,
#         upstreams,
#         downstreams,
#     ):
#         """
#         Starts a gRPC process.
#         """
#         # os.environ["DB_URI"] = (
#         #     "mysql+pymysql://{}:{}@{}/eventsourcing{
#         #     }?charset=utf8mb4&binary_prefix=true"
#         # ).format(
#         #     os.getenv("MYSQL_USER", "eventsourcing"),
#         #     os.getenv("MYSQL_PASSWORD", "eventsourcing"),
#         #     os.getenv("MYSQL_HOST", "127.0.0.1"),
#         #     resolve_topic(application_topic).create_name()
#         #     if self.use_individual_databases
#         #     else "",
#         # )
#
#         process = Popen(
#             [
#                 sys.executable,
#                 processor.__file__,
#                 application_topic,
#                 json.dumps(pipeline_id),
#                 infrastructure_topic,
#                 json.dumps(setup_table),
#                 address,
#                 json.dumps(upstreams),
#                 json.dumps(downstreams),
#                 json.dumps(self.push_prompt_interval),
#             ],
#             stderr=subprocess.STDOUT,
#             close_fds=True,
#         )
#         self.processors.append(process)
#
#     def close(self) -> None:
#         """
#         Stops all gRPC processes started by the runner.
#         """
#         for process in self.processors:
#             self.stop_process(process)
#         for process in self.processors:
#             self.kill_process(process)
#
#     def stop_process(self, process):
#         """
#         Stops given gRPC process.
#         """
#         exit_status_code = process.poll()
#         if exit_status_code is None:
#             process.send_signal(SIGINT)
#
#     def kill_process(self, process):
#         """
#         Kills given gRPC process, if it still running.
#         """
#         try:
#             process.wait(timeout=1)
#         except TimeoutExpired:
#             print("Timed out waiting for process to stop. Terminating....")
#             process.terminate()
#             try:
#                 process.wait(timeout=1)
#             except TimeoutExpired:
#                 print("Timed out waiting for process to terminate. Killing....")
#                 process.kill()
#             print("Processor exit code: %s" % process.poll())
#
#     def _construct_app_by_class(
#         self, process_class: Type[TProcessApplication], pipeline_id: int
#     ) -> TProcessApplication:
#         client = ProcessorClient()
#         client.connect(self.addresses[(process_class.create_name(), pipeline_id)])
#         return ClientWrapper(client)
#
#     def listen(self, name, processor_clients):
#         """
#         Constructs a listener using the given clients.
#         """
#         processor_clients: List[ProcessorClient]
#         return ProcessorListener(
#             name=name, address=self.create_address(), clients=processor_clients
#         )
#
#
# class ClientWrapper:
#     """
#     Wraps a gRPC client, and returns a MethodWrapper when attributes are accessed.
#     """
#
#     def __init__(self, client: ProcessorClient):
#         self.client = client
#
#     def __getattr__(self, item):
#         return MethodWrapper(self.client, item)
#
#
# class MethodWrapper:
#     """
#     Wraps a gRPC client, and invokes application method name when called.
#     """
#
#     def __init__(self, client: ProcessorClient, method_name: str):
#         self.client = client
#         self.method_name = method_name
#
#     def __call__(self, *args, **kwargs):
#         return self.client.call_application(self.method_name, *args, **kwargs)
#
#
# class ProcessorListener(ProcessorServicer):
#     """
#     Starts server and uses clients to request prompts from connected servers.
#     """
#
#     def __init__(self, name, address, clients: List[ProcessorClient]):
#         super().__init__()
#         self.name = name
#         self.address = address
#         self.clients = clients
#         self.prompt_events = {}
#         self.pull_notification_threads = {}
#         self.serve()
#         for client in self.clients:
#             client.lead(self.name, self.address)
#
#     def serve(self):
#         """
#         Starts server.
#         """
#         self.executor = futures.ThreadPoolExecutor(max_workers=10)
#         self.server = grpc.server(self.executor)
#         add_ProcessorServicer_to_server(self, self.server)
#         self.server.add_insecure_port(self.address)
#         self.server.start()
#
#     def Ping(self, request, context):
#         return Empty()
#
#     def Prompt(self, request, context):
#         upstream_name = request.upstream_name
#         self.prompt(upstream_name)
#         return Empty()
#
#     def prompt(self, upstream_name):
#         """
#         Sets prompt events for given upstream process.
#         """
#         # logging.info(
#         #     "Application %s received prompt from %s"
#         #     % (self.application_name, upstream_name)
#         # )
#         if upstream_name not in self.prompt_events:
#             self.prompt_events[upstream_name] = Event()
#         self.prompt_events[upstream_name].set()

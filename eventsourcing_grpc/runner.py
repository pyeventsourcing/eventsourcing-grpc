import sys
from itertools import count
from signal import SIGINT
from subprocess import STDOUT, Popen, TimeoutExpired
from threading import Event
from typing import Dict, List, Optional, Type, cast

from eventsourcing.application import Application, TApplication
from eventsourcing.system import Runner, System
from eventsourcing.utils import EnvType, get_topic, resolve_topic

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
        self.subprocesses: List[Popen[bytes]] = []
        self.servers: Dict[str, ApplicationServer] = {}
        self.clients: Dict[str, ApplicationClient[Application]] = {}
        self.addresses: Dict[str, str] = {}
        self.port_generator = count(start=50051)
        self.has_errored = Event()

    def start(self, subprocess: bool = True) -> None:
        super().start()

        # Construct followers.
        for follower_name in self.system.followers:
            follower_class = self.system.follower_cls(follower_name)
            self.start_application(follower_class, subprocess=subprocess)

        # Construct non-follower leaders.
        for leader_name in self.system.leaders_only:
            leader_cls = self.system.leader_cls(leader_name)
            self.start_application(leader_cls, subprocess=subprocess)

        # Construct singles.
        for name in self.system.singles:
            single_cls = self.system.get_app_cls(name)
            self.start_application(single_cls, subprocess=subprocess)

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

    def start_application(
        self, app_class: Type[Application], subprocess: bool = True
    ) -> None:
        address = self.create_address()
        self.addresses[app_class.name] = address
        application = app_class(env=self.env)
        self.apps[app_class.name] = application
        if subprocess is True:
            self.start_subprocess(get_topic(app_class), address)
        else:
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
        for process in self.subprocesses:
            self.stop_subprocess(process)
        for process in self.subprocesses:
            self.kill_subprocess(process)
        self.subprocesses.clear()
        for app in self.apps.values():
            app.close()
        self.apps.clear()
        self.addresses.clear()

    def start_subprocess(
        self,
        application_topic: str,
        address: str,
    ) -> None:
        process = Popen(
            [
                sys.executable,
                __file__,
                application_topic,
                address,
            ],
            stderr=STDOUT,
            close_fds=True,
            env=self.env,
        )
        self.subprocesses.append(process)

    def stop_subprocess(self, process: Popen[bytes]) -> None:
        """
        Stops given gRPC process.
        """
        exit_status_code = process.poll()
        if exit_status_code is None:
            process.send_signal(SIGINT)

    def kill_subprocess(self, process: Popen[bytes]) -> None:
        """
        Kills given gRPC process, if it still running.
        """
        try:
            process.wait(timeout=1)
        except TimeoutExpired:
            print("Timed out waiting for process to stop. Terminating....")
            process.terminate()
            try:
                process.wait(timeout=1)
            except TimeoutExpired:
                print("Timed out waiting for process to terminate. Killing....")
                process.kill()
            print("Processor exit code: %s" % process.poll())

    def __del__(self) -> None:
        self.stop()


if __name__ == "__main__":
    # logging.basicConfig(level=DEBUG)
    application_topic = sys.argv[1]
    address = sys.argv[2]

    app_class = resolve_topic(application_topic)
    app_server = ApplicationServer(
        app_class(),
        address,
    )
    app_server.start()
    try:
        app_server.server.wait_for_termination()
    except KeyboardInterrupt:
        pass
    finally:
        app_server.stop()

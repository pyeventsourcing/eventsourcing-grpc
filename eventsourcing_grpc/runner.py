from __future__ import annotations

import os
import sys
from itertools import count
from signal import SIGINT
from subprocess import STDOUT, Popen, TimeoutExpired
from threading import Event
from typing import Dict, List, Optional, Type, cast

from eventsourcing.application import Application, TApplication
from eventsourcing.system import Runner, System
from eventsourcing.utils import Environment, EnvType, get_topic, resolve_topic

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
        self.port_generator = count(start=50051)
        self.has_errored = Event()

    def start(self, subprocess: bool = False) -> None:
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

        # Construct clients.
        for application in self.apps.values():
            address = get_grpc_address(application.name, self.env)
            client: ApplicationClient[Application] = ApplicationClient(
                address=address, transcoder=application.construct_transcoder()
            )
            self.clients[application.name] = client
            client.connect(timeout=1)

        if self.env and "SYSTEM_TOPIC" not in self.env:
            # Lead and follow.
            for edge in self.system.edges:
                leader_name = edge[0]
                follower_name = edge[1]
                leader_client = self.clients[leader_name]
                leader_address = get_grpc_address(leader_name, self.env)
                follower_client = self.clients[follower_name]
                follower_address = get_grpc_address(follower_name, self.env)
                follower_client.follow(leader_name, leader_address)
                leader_client.lead(follower_name, follower_address)

    def start_application(
        self, app_class: Type[Application], subprocess: bool = False
    ) -> None:
        application = app_class(env=self.env)
        self.apps[app_class.name] = application
        if subprocess is True:
            application_topic = get_topic(app_class)
            self.start_subprocess(application_topic)
        else:
            address = get_grpc_address(application.name, self.env)
            server = ApplicationServer(application=application, address=address)
            server.start()
            self.servers[app_class.name] = server

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

    def start_subprocess(self, application_topic: str) -> None:
        process = Popen(
            [sys.executable, __file__, application_topic],
            stderr=STDOUT,
            close_fds=True,
            env=self.env,
        )
        # print("Started process:", process)
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


def get_grpc_address(name: str, env: Optional[EnvType]) -> str:
    env = Environment(name=name, env=env)
    address = env.get("GRPC_ADDRESS")
    if not address:
        raise ValueError("GRPC address not found in environment")
    else:
        return address


if __name__ == "__main__":
    # logging.basicConfig(level=DEBUG)
    application_topic = sys.argv[1]

    # sys.stdout.write(f"Starting subprocess {application_topic}\n")
    # sys.stdout.flush()

    app_class = resolve_topic(application_topic)
    application: Application = app_class()
    app_server = ApplicationServer(
        application,
        address=get_grpc_address(application.name, os.environ),
    )
    app_server.start()

    system_topic = os.environ.get("SYSTEM_TOPIC", "")
    if system_topic:
        system = cast(System, resolve_topic(system_topic))
        for leader_name in system.follows[application.name]:
            leader_address = get_grpc_address(leader_name, os.environ)
            app_server.service.follow(leader_name, leader_address)
        for follower_name in system.leads[application.name]:
            follower_address = get_grpc_address(follower_name, os.environ)
            app_server.service.lead(follower_address)

    try:
        app_server.server.wait_for_termination()
    except KeyboardInterrupt:
        pass
    finally:
        app_server.stop()

from __future__ import annotations

import os
import sys
import traceback
from itertools import count
from signal import SIGINT
from subprocess import Popen, TimeoutExpired
from threading import Event, Thread
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
        self.port_generator = count(start=50051)
        self.servers: Dict[str, ApplicationServer] = {}
        self.clients: Dict[str, ApplicationClient[Application]] = {}
        self.subprocesses: List[Popen[bytes]] = []
        self.threads: List[SubprocessManager] = []
        self.has_errored = Event()

    def start(self, with_subprocesses: bool = False) -> None:
        super().start()

        # Construct followers.
        for follower_name in self.system.followers:
            follower_class = self.system.follower_cls(follower_name)
            self.start_application(follower_class, with_subprocess=with_subprocesses)

        # Construct non-follower leaders.
        for leader_name in self.system.leaders_only:
            leader_cls = self.system.leader_cls(leader_name)
            self.start_application(leader_cls, with_subprocess=with_subprocesses)

        # Construct singles.
        for name in self.system.singles:
            single_cls = self.system.get_app_cls(name)
            self.start_application(single_cls, with_subprocess=with_subprocesses)

        if with_subprocesses:
            self.has_errored.wait(timeout=1)
            for t in self.threads:
                if (not t.has_started.wait(timeout=0)) or t.has_errored.is_set():
                    self.stop()
                    raise Exception("Failed to start sub processes")

        else:
            # Lead and follow.
            # sleep(0.5)
            for edge in self.system.edges:
                leader_name = edge[0]
                follower_name = edge[1]
                leader_address = get_grpc_address(leader_name, self.env)
                follower_address = get_grpc_address(follower_name, self.env)
                leader_client = self.get_client(self.system.get_app_cls(leader_name))
                follower_client = self.get_client(
                    self.system.get_app_cls(follower_name)
                )
                leader_client.lead(follower_name, follower_address)
                follower_client.follow(leader_name, leader_address)

    def start_application(
        self, app_class: Type[Application], with_subprocess: bool = False
    ) -> None:
        if self.has_errored.is_set():
            return
        if with_subprocess is False:
            self.start_server_inprocess(app_class)
        else:
            self.start_server_subprocess(app_class)

    def start_server_inprocess(self, app_class: Type[Application]) -> None:
        server = ApplicationServer(
            application=app_class(env=self.env),
            address=get_grpc_address(app_class.name, self.env),
            poll_interval=get_poll_interval(app_class.name, self.env),
        )
        server.start()
        self.servers[app_class.name] = server

    def start_server_subprocess(self, app_class: Type[Application]) -> None:
        # print("Starting server", app_class)
        thread = SubprocessManager(app_class, self.env or {}, self.has_errored)
        self.threads.append(thread)
        thread.start()
        thread.has_started.wait()
        self.subprocesses.append(thread.process)

    def get_client(self, cls: Type[TApplication]) -> ApplicationClient[TApplication]:
        try:
            client = self.clients[cls.name]
        except KeyError:
            env = dict(self.env or {})
            env.pop("INFRASTRUCTURE_FACTORY", None)
            env.pop("FACTORY_TOPIC", None)
            env.pop("PERSISTENCE_MODULE", None)
            transcoder = cls(env=env).construct_transcoder()
            address = get_grpc_address(cls(env=env).name, self.env)
            client = ApplicationClient(
                client_name="runner", address=address, transcoder=transcoder
            )
            client.connect()
            self.clients[cls.name] = client
        return cast(ApplicationClient[TApplication], client)

    def get(self, cls: Type[TApplication]) -> TApplication:
        return cast(ApplicationClient[TApplication], self.clients[cls.name]).app

    def get_app(self, cls: Type[TApplication]) -> TApplication:
        return cast(TApplication, self.apps[cls.name])

    def __del__(self) -> None:
        self.stop()

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
            # print("Processor exit code: %s" % process.poll())


class SubprocessManager(Thread):
    def __init__(
        self, app_class: Type[Application], env: EnvType, has_errored: Event
    ) -> None:
        super().__init__(daemon=True)
        self.app_class = app_class
        self.env = env
        self.has_errored = has_errored
        self.has_started = Event()

    def run(self) -> None:
        try:
            env = dict(self.env)
            env["APPLICATION_TOPIC"] = get_topic(self.app_class)
            self.process = Popen(
                [sys.executable, __file__],
                close_fds=True,
                env=env,
            )
        except BaseException:
            self.has_errored.set()
        finally:
            self.has_started.set()
        if self.process.wait():
            self.has_errored.set()
        # print("Subprocess exited, status code", self.process.returncode)


def start_application_server() -> None:
    application_topic = os.environ["APPLICATION_TOPIC"]
    # sys.stdout.write(f"Starting subprocess {application_topic}\n")
    sys.stdout.flush()

    system_topic = os.environ["SYSTEM_TOPIC"]
    system = cast(System, resolve_topic(system_topic))
    app_class = resolve_topic(application_topic)

    # Make sure we have a leader class if leading.
    if app_class.name in system.leads:
        app_class = system.leader_cls(app_class.name)

    # Make sure we have a follower class if following.
    if app_class.name in system.follows:
        app_class = system.follower_cls(app_class.name)

    # Construct the application object.
    application = app_class()
    poll_interval = get_poll_interval(app_class.name, os.environ)

    # Get the address and start the application server.
    address = get_grpc_address(application.name, os.environ)
    app_server = ApplicationServer(application, address, poll_interval)
    app_server.start()

    # Lead and follow.
    for follower_name in system.leads[application.name]:
        follower_address = get_grpc_address(follower_name, os.environ)
        app_server.service.lead(follower_address)
        # print(application.name, "is leading", follower_name)
    for leader_name in system.follows[application.name]:
        leader_address = get_grpc_address(leader_name, os.environ)
        app_server.service.follow(leader_name, leader_address)
        # print(application.name, "is following", leader_name)

    # Wait for termination.
    try:
        app_server.server.wait_for_termination()
    except KeyboardInterrupt:
        pass
    finally:
        app_server.stop()


def get_grpc_address(name: str, env: Optional[EnvType]) -> str:
    env = Environment(name=name, env=env)
    address = env.get("GRPC_ADDRESS")
    if not address:
        raise ValueError("GRPC address not found in environment")
    else:
        return address


def get_poll_interval(name: str, env: Optional[EnvType]) -> float:
    env = Environment(name=name, env=env)
    poll_interval_str = env.get("POLL_INTERVAL")
    if not poll_interval_str:
        raise ValueError("POLL_INTERVAL not found in environment")
    try:
        poll_interval = float(poll_interval_str)
    except ValueError:
        raise ValueError(
            f"Could not covert POLL_INTERVAL string to float: {poll_interval_str}"
        ) from None
    else:
        return poll_interval


if __name__ == "__main__":
    try:
        start_application_server()
    except BaseException:
        print(traceback.format_exc())
        # print("Exiting with status code 1 after error")
        sys.exit(1)

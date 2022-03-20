from __future__ import annotations

import os
from itertools import count
from signal import SIGINT
from subprocess import Popen, TimeoutExpired
from threading import Event, Thread
from typing import Dict, List, Optional, Type, cast

from eventsourcing.application import Application, TApplication
from eventsourcing.system import Runner, System
from eventsourcing.utils import EnvType, get_topic

from eventsourcing_grpc.application_client import ApplicationClient
from eventsourcing_grpc.application_server import ApplicationServer
from eventsourcing_grpc.environment import GrpcEnvironment


class GrpcRunner(Runner):
    """
    System runner that uses gRPC to communicate between process applications.
    """

    def __init__(self, system: System, env: Optional[EnvType] = None):
        """
        Initialises runner with the given :class:`System`.
        """
        super().__init__(system=system, env=env)

        self._env = dict()
        if system.topic:
            self._env["SYSTEM_TOPIC"] = system.topic
        self._env.update(os.environ)
        if env is not None:
            self._env.update(env)

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
        server = ApplicationServer(app_class=app_class, env=self._env)
        server.start()
        self.servers[app_class.name] = server

    def start_server_subprocess(self, app_class: Type[Application]) -> None:
        # print("Starting server", app_class)
        thread = SubprocessManager(
            app_class=app_class, env=self._env, has_errored=self.has_errored
        )
        self.threads.append(thread)
        thread.start()
        thread.has_started.wait()
        self.subprocesses.append(thread.process)

    def get_client(self, cls: Type[TApplication]) -> ApplicationClient[TApplication]:
        try:
            client = self.clients[cls.name]
        except KeyError:
            # Construct application transcoder.
            # Make sure we don't try to connect to actual database.
            env = dict(self.env or {})
            env["PERSISTENCE_MODULE"] = "eventsourcing.popo"
            transcoder = cls(env=env).construct_transcoder()
            address = GrpcEnvironment(self._env).get_server_address(cls.name)
            client = ApplicationClient(
                owner_name="runner", address=address, transcoder=transcoder
            )
            client.connect()
            self.clients[cls.name] = client
        return cast(ApplicationClient[TApplication], client)

    def get(self, cls: Type[TApplication]) -> TApplication:
        return self.get_client(cls).app

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
                ["eventsourcing_grpc_server"],
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

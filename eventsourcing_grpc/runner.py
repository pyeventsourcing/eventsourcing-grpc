from __future__ import annotations

import os
from itertools import count
from threading import Event, Lock
from typing import Dict, List, Optional, Type, cast

from eventsourcing.application import Application, TApplication
from eventsourcing.system import Runner, System
from eventsourcing.utils import EnvType

from eventsourcing_grpc.application_client import GrpcApplicationClient
from eventsourcing_grpc.application_server import (
    GrpcApplicationServer,
    ServerSubprocess,
    start_server,
    start_server_subprocess,
)
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

        self.is_stopping = Event()
        self._env = os.environ.copy()
        if system.topic:
            self._env["SYSTEM_TOPIC"] = system.topic
        if env is not None:
            self._env.update(env)

        self.apps: Dict[str, Application] = {}
        self.port_generator = count(start=50051)
        self.clients: Dict[str, GrpcApplicationClient[Application]] = {}
        self.servers: Dict[str, GrpcApplicationServer] = {}
        self.server_subprocesses: List[ServerSubprocess] = []
        self.lock = Lock()
        self.has_errored = Event()
        self.has_stopped = Event()
        self.has_started = Event()

    def start(self, with_subprocesses: bool = False) -> None:
        super().start()

        try:
            # Construct followers.
            for follower_name in self.system.followers:
                follower_class = self.system.follower_cls(follower_name)
                self.start_application(
                    follower_class, with_subprocess=with_subprocesses
                )

            # Construct non-follower leaders.
            for leader_name in self.system.leaders_only:
                leader_cls = self.system.leader_cls(leader_name)
                self.start_application(leader_cls, with_subprocess=with_subprocesses)

            # Construct singles.
            for name in self.system.singles:
                single_cls = self.system.get_app_cls(name)
                self.start_application(single_cls, with_subprocess=with_subprocesses)

            if with_subprocesses:
                # Wait for subprocesses to start.
                for server_subprocess in self.server_subprocesses:
                    server_subprocess.has_started.wait()
                self.has_errored.wait(timeout=1)
                for t in self.server_subprocesses:
                    if (not t.has_started.wait(timeout=0)) or t.has_errored.is_set():
                        self.stop()
                        raise Exception("Failed to start sub processes")
        finally:
            self.has_started.set()

    def start_application(
        self, app_class: Type[Application], with_subprocess: bool = False
    ) -> None:
        if self.has_errored.is_set():
            return
        if with_subprocess is False:
            self.servers[app_class.name] = start_server(
                app_class=app_class, env=self._env
            )

        else:
            self.server_subprocesses.append(
                start_server_subprocess(
                    app_class=app_class, env=self._env, has_errored=self.has_errored
                )
            )

    def get_client(
        self, cls: Type[TApplication]
    ) -> GrpcApplicationClient[TApplication]:
        try:
            client = self.clients[cls.name]
        except KeyError:
            # Construct application transcoder.
            # Make sure we don't try to connect to actual database.
            env = dict(self.env or {})
            env["PERSISTENCE_MODULE"] = "eventsourcing.popo"
            transcoder = cls(env=env).construct_transcoder()
            address = GrpcEnvironment(self._env).get_server_address(cls.name)
            client = GrpcApplicationClient(
                application_name=cls.name,
                owner_name=type(self).__name__,
                address=address,
                transcoder=transcoder,
                is_stopping=self.is_stopping,
            )
            client.connect()
            self.clients[cls.name] = client
        return cast(GrpcApplicationClient[TApplication], client)

    def get(self, cls: Type[TApplication]) -> TApplication:
        return self.get_client(cls).app

    def __del__(self) -> None:
        self.stop()

    def stop(self) -> None:
        try:
            with self.lock:
                if self.is_stopping.is_set():
                    return
                self.is_stopping.set()
            for client in self.clients.values():
                client.close()
            self.clients.clear()
            for server in self.servers.values():
                server.stop()
            for server in self.servers.values():
                server.has_stopped.wait()
            self.servers.clear()
            for process in self.server_subprocesses:
                process.stop()
            for process in self.server_subprocesses:
                process.has_stopped.wait()
            for app in self.apps.values():
                app.close()
            self.apps.clear()
        finally:
            self.has_stopped.set()

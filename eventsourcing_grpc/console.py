from __future__ import annotations

import os
import sys
import traceback
from itertools import count
from signal import SIGINT, SIGTERM, signal
from threading import Event, Lock
from typing import Any, cast

from eventsourcing.system import System
from eventsourcing.utils import TopicError, resolve_topic

from eventsourcing_grpc.application_server import start_server
from eventsourcing_grpc.runner import GrpcRunner


def eventsourcing_grpc_server() -> None:
    try:
        application_topic = os.environ["APPLICATION_TOPIC"]
        # print(f"Application topic found in env: {application_topic}")
    except KeyError:
        if len(sys.argv) > 1:
            application_topic = sys.argv[1]
            # print(f"Application topic found in args: {application_topic}")
        else:
            print("Error: application topic not set in env or args")
            raise SystemExit(1) from None

    is_interrupted = Event()
    lock = Lock()

    # Register signal handlers.
    def signal_handler(*args: Any) -> None:
        # print(
        #     f"Application server process received signal '{strsignal(signum)}'"
        # )
        with lock:
            if not is_interrupted.is_set():
                print(f"Stopping gRPC server: {application_topic}")
                is_interrupted.set()

    signal(SIGINT, signal_handler)
    signal(SIGTERM, signal_handler)

    try:
        print(
            f"Starting gRPC server: {application_topic}",
        )
        # sys.stdout.write(f"Starting subprocess {application_topic}\n")
        app_class = resolve_topic(application_topic)

        if "SYSTEM_TOPIC" in os.environ:
            system_topic = os.environ["SYSTEM_TOPIC"]
            system = cast(System, resolve_topic(system_topic))
            print(app_class.name, "system topic:", system_topic)
            # Make sure we have a leader class if leading.
            if app_class.name in system.leads:
                app_class = system.leader_cls(app_class.name)

        # Get the address and start the application server.
        server = start_server(
            app_class=app_class, env=os.environ, is_stopping=is_interrupted
        )

        # Wait for termination.
        server.wait_for_termination()
    except BaseException:
        print(traceback.format_exc())
        # print("Exiting with status code 1 after error")
        sys.exit(1)


def eventsourcing_grpc_runner() -> None:  # noqa: C901
    try:
        system_topic = os.environ["SYSTEM_TOPIC"]
        # print(f"System topic found in env: {system_topic}")
    except KeyError:
        if len(sys.argv) > 1:
            system_topic = sys.argv[1]
            # print(f"System topic found in args: {system_topic}")
        else:
            print("Error: system topic not set in env or args")
            raise SystemExit(1) from None

    is_interrupted = Event()
    lock = Lock()

    # Register signal handlers.
    def signal_handler(*args: Any) -> None:
        # print(
        #     f"Application server process received signal '{strsignal(signum)}'"
        # )
        with lock:
            if not is_interrupted.is_set():
                print(f"Stopping gRPC runner: {system_topic}")
                is_interrupted.set()

    signal(SIGINT, signal_handler)
    signal(SIGTERM, signal_handler)

    try:
        # Todo: If topic resolves to a module, search for system object in the module.
        try:
            system = cast(System, resolve_topic(system_topic))
        except TopicError:
            print(f"Error: system not found: {system_topic}")
            raise SystemExit(1) from None

        print(
            f"Starting gRPC runner: {system_topic}",
        )

        env = os.environ.copy()
        counter = count(50051)
        for app_name in system.nodes.keys():
            var_name = f"{app_name.upper()}_GRPC_SERVER_ADDRESS"
            port = next(counter)
            var_value = f"localhost:{port}"
            print(f"Setting {var_name}={var_value}")
            env[var_name] = var_value

        # Get the address and start the application server.
        with lock:
            if not is_interrupted.is_set():
                runner = GrpcRunner(system=system, env=env)

                runner.start(with_subprocesses=True)
                if not runner.has_started.wait(timeout=5):
                    is_interrupted.set()

        # Wait for termination.
        try:
            if runner.has_errored.is_set():
                runner.stop()
                runner.has_stopped.wait()
                raise RuntimeError("Couldn't start runner")
            is_interrupted.wait()
            runner.stop()
            runner.has_stopped.wait()
        except NameError:
            pass

    except SystemExit:
        raise
    except BaseException:
        try:
            runner.has_stopped.wait()
        except NameError:
            pass
        print(traceback.format_exc())
        # print("Exiting with status code 1 after error")
        sys.exit(1)

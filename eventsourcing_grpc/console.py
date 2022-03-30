from __future__ import annotations

import os
import socket
import sys
import traceback
from signal import SIGINT, SIGTERM, signal
from threading import Event, Lock
from typing import Any, cast

from eventsourcing.system import System
from eventsourcing.utils import resolve_topic

from eventsourcing_grpc.application_server import start_server


def eventsourcing_grpc_server() -> None:
    application_topic = os.environ["APPLICATION_TOPIC"]
    is_stopping = Event()
    lock = Lock()

    # Register signal handlers.
    def signal_handler(*args: Any) -> None:
        # print(
        #     f"Application server process received signal '{strsignal(signum)}'"
        # )
        with lock:
            if not is_stopping.is_set():
                print(f"Stopping application server for topic '{application_topic}'")
                is_stopping.set()

    signal(SIGINT, signal_handler)
    signal(SIGTERM, signal_handler)

    try:
        print(
            f"Starting application server for topic '{application_topic}':",
            f"hostname: '{socket.gethostname()}'",
        )
        # sys.stdout.write(f"Starting subprocess {application_topic}\n")
        app_class = resolve_topic(application_topic)

        if "SYSTEM_TOPIC" in os.environ:
            system_topic = os.environ["SYSTEM_TOPIC"]
            system = cast(System, resolve_topic(system_topic))
            # Make sure we have a leader class if leading.
            if app_class.name in system.leads:
                app_class = system.leader_cls(app_class.name)

        # Get the address and start the application server.
        server = start_server(
            app_class=app_class, env=os.environ, is_stopping=is_stopping
        )

        # Wait for termination.
        server.wait_for_termination()
    except BaseException:
        print(traceback.format_exc())
        # print("Exiting with status code 1 after error")
        sys.exit(1)

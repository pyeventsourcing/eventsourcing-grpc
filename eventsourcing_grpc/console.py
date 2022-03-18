from __future__ import annotations

import os
import sys
import traceback
from typing import cast

from eventsourcing.system import System
from eventsourcing.utils import resolve_topic

from eventsourcing_grpc.application_server import ApplicationServer


def run_application_server() -> None:
    try:
        application_topic = os.environ["APPLICATION_TOPIC"]
        print("Starting", application_topic)
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
        # Get the address and start the application server.
        app_server = ApplicationServer(app_class=app_class, env=os.environ)
        app_server.start()
        # Wait for termination.
        try:
            app_server.wait_for_termination()
        except KeyboardInterrupt:
            pass
        finally:
            app_server.stop()
    except BaseException:
        print(traceback.format_exc())
        # print("Exiting with status code 1 after error")
        sys.exit(1)

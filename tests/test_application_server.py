import os
import socket
from datetime import datetime
from threading import Event
from time import sleep
from typing import Any, List, Type
from unittest import TestCase
from uuid import UUID

from eventsourcing.application import Application, TApplication
from eventsourcing.system import System
from eventsourcing.utils import EnvType

from eventsourcing_grpc.application_client import (
    ApplicationClient,
    ChannelConnectTimeout,
    connect,
    create_client,
)
from eventsourcing_grpc.application_server import (
    ApplicationServer,
    start_server,
    start_server_subprocess,
)
from eventsourcing_grpc.example import Orders, Reservations

env_orders: EnvType = {
    "ORDERS_GRPC_SERVER_ADDRESS": "localhost:50051",
}

system_orders_and_reservations = System([[Orders, Reservations, Orders]])

env_orders_and_reservations: EnvType = {
    "SYSTEM_TOPIC": system_orders_and_reservations.topic or "",
    "ORDERS_GRPC_SERVER_ADDRESS": "localhost:50051",
    "RESERVATIONS_GRPC_SERVER_ADDRESS": "localhost:50052",
    "PAYMENTS_GRPC_SERVER_ADDRESS": "localhost:50053",
    "MAX_PULL_INTERVAL": "1",
}

hostname = socket.gethostname()


class TestApplicationServer(TestCase):
    def test_start_stop(self) -> None:
        server = ApplicationServer(app_class=Orders, env=env_orders)
        self.assertFalse(server.has_started.is_set())
        self.assertFalse(server.has_stopped.is_set())

        server.start()
        self.assertTrue(server.has_started.is_set())
        self.assertFalse(server.has_stopped.is_set())

        server.stop(wait=True)
        self.assertFalse(server.has_started.is_set())
        self.assertTrue(server.has_stopped.is_set())

        server.start()
        self.assertTrue(server.has_started.is_set())
        self.assertFalse(server.has_stopped.is_set())

        server.start()
        self.assertTrue(server.has_started.is_set())
        self.assertFalse(server.has_stopped.is_set())

        server.stop(wait=True)
        self.assertFalse(server.has_started.is_set())
        self.assertTrue(server.has_stopped.is_set())

        server.stop(wait=True)
        self.assertFalse(server.has_started.is_set())
        self.assertTrue(server.has_stopped.is_set())

    def test_client_connect_failure(self) -> None:
        # Don't start a server.
        with self.assertRaises(ChannelConnectTimeout):
            self.connect(Orders, env_orders, owner_name="test", max_attempts=2)

    def test_client_connect_success(self) -> None:
        self.start_server(Orders, env_orders)
        self.connect(Orders, env_orders, owner_name="test", max_attempts=2)

    def test_ssl_credentials(self) -> None:
        ssl_private_key_path = os.path.join(
            os.path.dirname(__file__), "..", "ssl", hostname, f"{hostname}.key"
        )
        ssl_certificate_path = os.path.join(
            os.path.dirname(__file__), "..", "ssl", hostname, f"{hostname}.crt"
        )
        ssl_root_certificate_path = os.path.join(
            os.path.dirname(__file__), "..", "ssl", hostname, "root.crt"
        )
        self.assertNotEqual(hostname, "localhost")
        env_server: EnvType = {
            "ORDERS_GRPC_SERVER_ADDRESS": f"{hostname}:50051",
            "GRPC_SSL_PRIVATE_KEY_PATH": ssl_private_key_path,
            "GRPC_SSL_CERTIFICATE_PATH": ssl_certificate_path,
            "GRPC_SSL_ROOT_CERTIFICATE_PATH": ssl_root_certificate_path,
        }

        self.start_server(Orders, env_server)
        env_client: EnvType = {
            "ORDERS_GRPC_SERVER_ADDRESS": f"{hostname}:50051",
            "GRPC_SSL_ROOT_CERTIFICATE_PATH": ssl_root_certificate_path,
            "GRPC_SSL_PRIVATE_KEY_PATH": ssl_private_key_path,
            "GRPC_SSL_CERTIFICATE_PATH": ssl_certificate_path,
        }
        self.connect(Orders, env_client, owner_name="test", max_attempts=10)

    def test_call_application_method(self) -> None:
        self.start_server(Orders, env_orders)
        app = self.connect(Orders, env_orders, "test", max_attempts=10).app

        # Create order.
        order_id = app.create_new_order()
        self.assertIsInstance(order_id, UUID)

        # Get order.
        order = app.get_order(order_id)
        self.assertIsInstance(order, dict)
        self.assertEqual(order["id"], order_id)
        self.assertEqual(order["is_reserved"], False)
        self.assertEqual(order["is_paid"], False)

    def _test_repeat_call_application_method(self) -> None:
        self.start_server(Orders, env_orders)
        client = create_client(Orders, env_orders, owner_name="test")
        client.connect(max_attempts=10)

        started = datetime.now()
        for i in range(10000):
            # Create order.
            order_id = client.app.create_new_order()
            self.assertIsInstance(order_id, UUID)

            # Get order.
            order = client.app.get_order(order_id)
            self.assertIsInstance(order, dict)
            self.assertEqual(order["id"], order_id)
            self.assertEqual(order["is_reserved"], False)
            self.assertEqual(order["is_paid"], False)

            rate = (i + 1) / (datetime.now() - started).total_seconds()
            print("Created order", i + 1, f"rate: {rate:.1f}/s")

    def test_get_notifications(self) -> None:
        self.start_server(Orders, env_orders)
        app = self.connect(Orders, env_orders, "test", max_attempts=10).app

        # Create an order.
        order1_id = app.create_new_order()

        # Get the notifications.
        notifications = app.notification_log.select(start=1, limit=10)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].id, 1)
        self.assertEqual(notifications[0].originator_id, order1_id)
        self.assertEqual(notifications[0].originator_version, 1)
        self.assertEqual(
            notifications[0].topic, "eventsourcing_grpc.example:Order.Created"
        )

        # Create another order.
        order2_id = app.create_new_order()

        # Get the notifications.
        notifications = app.notification_log.select(start=1, limit=10)
        self.assertEqual(len(notifications), 2)
        self.assertEqual(notifications[1].id, 2)
        self.assertEqual(notifications[1].originator_id, order2_id)
        self.assertEqual(notifications[1].originator_version, 1)
        self.assertEqual(
            notifications[1].topic, "eventsourcing_grpc.example:Order.Created"
        )

        # Get the notifications start=1, limit=1.
        notifications = app.notification_log.select(start=1, limit=1)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].id, 1)
        self.assertEqual(notifications[0].originator_id, order1_id)

        # Get the notifications from notification ID = 2.
        notifications = app.notification_log.select(start=2, limit=10)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].id, 2)
        self.assertEqual(notifications[0].originator_id, order2_id)

        # Get the notifications from notification ID = 3.
        notifications = app.notification_log.select(start=3, limit=10)
        self.assertEqual(len(notifications), 0)

        # Get the notifications from start=1, with wrong topic.
        notifications = app.notification_log.select(start=1, limit=10, topics=["wrong"])
        self.assertEqual(len(notifications), 0)

        # Get the notifications from start=1, with correct topic.
        notifications = app.notification_log.select(
            start=1,
            limit=10,
            topics=["eventsourcing_grpc.example:Order.Created"],
        )
        self.assertEqual(len(notifications), 2)

    def test_lead_and_follow(self) -> None:
        # Start servers.
        env = env_orders_and_reservations
        self.start_server(Orders, env)
        self.start_server(Reservations, env)

        # Connect.
        app = self.connect(Orders, env, "test", max_attempts=10).app

        # Create an order.
        order1_id = app.create_new_order()

        # Wait for the processing to happen.
        for __ in range(20):
            if app.is_order_reserved(order1_id):
                break
            else:
                sleep(0.1)
        else:
            self.fail("Timed out waiting for order to be reserved")

        # Get the notifications.
        notifications = app.notification_log.select(start=1, limit=10)
        self.assertEqual(len(notifications), 2)
        self.assertEqual(notifications[0].id, 1)
        self.assertEqual(notifications[0].originator_id, order1_id)
        self.assertEqual(notifications[0].originator_version, 1)
        self.assertEqual(
            notifications[0].topic, "eventsourcing_grpc.example:Order.Created"
        )
        self.assertEqual(notifications[1].id, 2)
        self.assertEqual(notifications[1].originator_id, order1_id)
        self.assertEqual(notifications[1].originator_version, 2)
        self.assertEqual(
            notifications[1].topic, "eventsourcing_grpc.example:Order.Reserved"
        )

        # Let everything finish processing.
        sleep(0.1)

    def start_server(
        self, app_class: Type[Application], env: EnvType
    ) -> ApplicationServer:
        server = start_server(app_class, env)
        self.servers.append(server)
        return server

    def connect(
        self,
        app_class: Type[TApplication],
        env: EnvType,
        owner_name: str,
        max_attempts: int,
    ) -> ApplicationClient[TApplication]:
        client = connect(app_class, env, owner_name, max_attempts)
        self.clients.append(client)
        return client

    def setUp(self) -> None:
        self.servers: List[ApplicationServer] = []
        self.clients: List[ApplicationClient[Any]] = []

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()
        for server in self.servers:
            server.stop(wait=True)


class TestServerSubprocess(TestCase):
    def test_start_and_stop_server_subprocess(self) -> None:
        # Copy and update OS environment.
        env = os.environ.copy()
        env.update(env_orders)

        # Start server subprocess.
        server_subprocess = start_server_subprocess(
            app_class=Orders, env=env, has_errored=Event()
        )
        self.assertTrue(server_subprocess.has_started.wait(timeout=1))
        self.assertFalse(server_subprocess.has_errored.is_set())

        # Stop server subprocess.
        sleep(1)
        server_subprocess.stop()
        sleep(1)

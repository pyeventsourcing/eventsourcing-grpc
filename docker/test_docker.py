import os
import socket
from threading import Thread
from time import sleep
from typing import Type
from unittest import TestCase
from uuid import UUID

from eventsourcing.application import TApplication
from eventsourcing.utils import EnvType

from eventsourcing_grpc.application_client import ApplicationClient, create_client
from eventsourcing_grpc.example import Orders

hostname = socket.gethostname()

# NB depends on '127.0.0.1 orders' in '/etc/hosts' or equivalent.
system_env = {
    "ORDERS_GRPC_SERVER_ADDRESS": f"orders:50051",  # hostname must match cert CN
    "SSL_ROOT_CERTIFICATE_PATH": (
        os.path.join(os.path.dirname(__file__), "..", "ssl", "orders", "root.crt")
    ),
    "SSL_PRIVATE_KEY_PATH": (
        os.path.join(
            os.path.dirname(__file__), "..", "ssl", hostname, f"{hostname}.key"
        )
    ),
    "SSL_CERTIFICATE_PATH": (
        os.path.join(
            os.path.dirname(__file__), "..", "ssl", hostname, f"{hostname}.crt"
        )
    ),
}


class TestDocker(TestCase):
    def test_order(self) -> None:
        # Connect to server.
        client = self._connect(Orders, system_env)

        # Create an order.
        order1_id = client.app.create_new_order()
        # print("Created order...")
        self.assertIsInstance(order1_id, UUID)

        # Wait for the processing to happen.
        for _ in range(100):
            client.app.get_order(order1_id)
            sleep(0.1)
            if client.app.is_order_paid(order1_id):
                break
        else:
            self.fail("Timeout waiting for order to be paid")

    def test_many_orders(self):
        # Connect to server.
        client = self._connect(Orders, system_env)

        # Create orders.
        order_ids = []

        def create_orders() -> None:
            for _ in range(10000):
                order_ids.append(client.app.create_new_order())
                sleep(0.1)
                # self.assertIsInstance(order1_id, UUID)

        def check_orders() -> None:
            for i in range(10000):
                # Wait for the processing to happen.
                for _ in range(100):
                    try:
                        order_id = order_ids[i]
                    except IndexError:
                        sleep(0.01)
                        continue
                    order = client.app.get_order(order_id)
                    if order["is_paid"]:
                        duration = (
                            order["modified_on"] - order["created_on"]
                        ).total_seconds()
                        print("Done order", i, duration)
                        break
                    else:
                        sleep(0.1)
                else:
                    self.fail("Timeout waiting for order to be paid")

        thread1 = Thread(target=create_orders)
        thread1.start()
        thread2 = Thread(target=check_orders)
        thread2.start()
        thread2.join()

    def _connect(
        self, app_class: Type[TApplication], env: EnvType
    ) -> ApplicationClient[TApplication]:
        client = create_client(owner_name="test", app_class=app_class, env=env)
        client.connect(max_attempts=10)
        return client

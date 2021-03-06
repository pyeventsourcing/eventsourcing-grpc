import os
import socket
from threading import Thread
from time import monotonic, sleep
from typing import Type
from unittest import TestCase
from uuid import UUID

from eventsourcing.application import TApplication
from eventsourcing.utils import EnvType

from eventsourcing_grpc.application_client import GrpcApplicationClient, create_client
from eventsourcing_grpc.example import Orders

hostname = socket.gethostname()

ssl_path = os.path.join(os.path.dirname(__file__), "..", "ssl")

system_env = {
    # Hostname must match cert CN, depends on '127.0.0.1 orders' in '/etc/hosts'
    "ORDERS_GRPC_SERVER_ADDRESS": "orders:50051",  #
    "GRPC_SSL_ROOT_CERTIFICATE_PATH": os.path.join(ssl_path, hostname, "root.crt"),
    "GRPC_SSL_PRIVATE_KEY_PATH": os.path.join(ssl_path, hostname, f"{hostname}.key"),
    "GRPC_SSL_CERTIFICATE_PATH": os.path.join(ssl_path, hostname, f"{hostname}.crt"),
}


class TestDocker(TestCase):
    def test_order(self) -> None:
        # Connect to server.
        client = self._connect(Orders, system_env)

        # Create an order.
        order1_id = client.app.create_new_order()
        print("Order created")
        self.assertIsInstance(order1_id, UUID)

        # Wait for the processing to happen.
        for _ in range(100):
            client.app.get_order(order1_id)
            sleep(0.1)
            if client.app.is_order_paid(order1_id):
                print("Order reserved and paid")
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
                sleep(0.01)
                # self.assertIsInstance(order1_id, UUID)

        def check_orders() -> None:

            started = None
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
                        if started is None:
                            started = monotonic()
                        else:
                            run_duration = monotonic() - started
                            rate = f"{(i / run_duration):.1f}/s"
                            if i % 50 == 0:
                                order_duration = (
                                    order["modified_on"] - order["created_on"]
                                ).total_seconds()
                                print("Done order", i, f"{order_duration:.4f}s", rate)
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
    ) -> GrpcApplicationClient[TApplication]:
        client = create_client(owner_name="test", app_class=app_class, env=env)
        client.connect(max_attempts=10)
        return client

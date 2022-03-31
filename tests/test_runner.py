from datetime import datetime
from itertools import count
from signal import SIGINT, SIGTERM, getsignal, signal, strsignal
from threading import Event, Lock, Thread
from time import sleep
from typing import Any
from unittest import TestCase
from uuid import UUID

from eventsourcing.utils import EnvType

from eventsourcing_grpc.application_client import ClientClosedError, GrpcError, connect
from eventsourcing_grpc.example import Orders, system
from eventsourcing_grpc.runner import GrpcRunner

# NB, uses "localhost" so client and server don't authenticate with certificates.

system_env = {
    "ORDERS_GRPC_SERVER_ADDRESS": "localhost:50051",
    "RESERVATIONS_GRPC_SERVER_ADDRESS": "localhost:50052",
    "PAYMENTS_GRPC_SERVER_ADDRESS": "localhost:50053",
    "MAX_PULL_INTERVAL": "1",
}


class TestRunner(TestCase):
    def test_runner_with_in_process_servers(self) -> None:
        self._test_runner(with_subprocesses=False)

    def test_runner_with_subprocess_servers(self) -> None:
        self._test_runner(with_subprocesses=True)

    def _test_infinite_runner_with_subprocess_servers(self) -> None:
        c = count()
        while True:
            print("Test number:", next(c))
            self._test_runner(with_subprocesses=True)

    def _test_runner(self, with_subprocesses: bool = False) -> None:
        # Set up.
        self.start_runner(system_env, with_subprocesses)

        # Create an order.
        orders = self.runner.get(Orders)
        order1_id = orders.create_new_order()
        self.assertIsInstance(order1_id, UUID)

        # Wait for the processing to happen.
        for _ in range(100):
            sleep(0.1)
            if orders.is_order_paid(order1_id):
                break
            elif self.runner.has_errored.is_set():
                self.fail("Runner error")
        else:
            self.fail("Timeout waiting for order to be paid")

        # Get the notifications.
        notifications = orders.notification_log.select(start=1, limit=10)
        self.assertEqual(len(notifications), 3)
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
        self.assertEqual(notifications[2].id, 3)
        self.assertEqual(notifications[2].originator_id, order1_id)
        self.assertEqual(notifications[2].originator_version, 3)
        self.assertEqual(
            notifications[2].topic, "eventsourcing_grpc.example:Order.Paid"
        )

        orders_app = Orders()
        first_event = orders_app.mapper.to_domain_event(notifications[0])
        last_event = orders_app.mapper.to_domain_event(notifications[-1])
        duration = last_event.timestamp - first_event.timestamp
        print("Duration:", duration)

    def _test_long_runner_with_inprocess_servers(self) -> None:
        self._long_runner(with_subprocesses=False)

    def _test_long_runner_with_subprocess_servers(self) -> None:
        self._long_runner(with_subprocesses=True)

    def _test_long_runner_with_runner_console_cmd(self) -> None:
        env = {
            "ORDERS_GRPC_SERVER_ADDRESS": "localhost:50051",
        }

        orders_client = connect(Orders, env=env, owner_name="test", max_attempts=10)
        self._do_orders(orders_client.app)

    def _long_runner(self, with_subprocesses: bool = False) -> None:  # noqa: C901
        # Set up.
        env = {
            "ORDERS_GRPC_SERVER_ADDRESS": "localhost:50051",
            "RESERVATIONS_GRPC_SERVER_ADDRESS": "localhost:50052",
            "PAYMENTS_GRPC_SERVER_ADDRESS": "localhost:50053",
            "MAX_PULL_INTERVAL": "1",
        }
        if with_subprocesses:
            env["SYSTEM_TOPIC"] = "eventsourcing_grpc.example:system"

        self.start_runner(env, with_subprocesses)

        # Get orders client.
        orders = self.runner.get(Orders)

        # Do orders.
        self._do_orders(orders)

    def _do_orders(self, orders: Orders) -> None:  # noqa: C901
        order_ids = []
        started = datetime.now()

        def create_order() -> None:
            try:
                for _ in range(10000):
                    # if self.test_errored.wait(0.0):
                    #     break
                    order_ids.append(orders.create_new_order())
            except ClientClosedError:
                if not self.is_terminated.is_set():
                    print("Client closed in 'create_order()'")
                    self.test_errored.set()
                return
            except GrpcError as e:
                if not self.is_terminated.is_set():
                    print("GRPC error in 'create_order()':", e)
                    self.test_errored.set()
                return

        def check_order() -> None:
            try:
                for i in range(10000):
                    # Wait for the processing to happen.
                    # if i > 500:
                    #     self.runner.stop()
                    #     return
                    for _ in range(100):
                        try:
                            order_id = order_ids[i]
                        except IndexError:
                            if self.test_errored.wait(0.1):
                                break
                            continue
                        try:
                            order = orders.get_order(order_id)
                        except ClientClosedError:
                            if not self.is_terminated.is_set():
                                print("Client closed in 'check_order()'")
                                self.test_errored.set()
                            return
                        except GrpcError as e:
                            if not self.is_terminated.is_set():
                                print("GRPC error in 'check_order()':", e)
                                self.test_errored.set()
                            return
                        if order["is_paid"]:
                            if i > 0 and i % 100 == 0:
                                order_duration = (
                                    order["modified_on"] - order["created_on"]
                                ).total_seconds()
                                rate = (i + 1) / (
                                    datetime.now() - started
                                ).total_seconds()

                                print(
                                    f"Done order {i}",
                                    f"duration: {order_duration:.4f}s",
                                    f"rate: {rate:.0f}/s",
                                )
                            break
                        elif (
                            hasattr(self, "runner") and self.runner.has_errored.is_set()
                        ):
                            self.fail("Runner error")
                        else:
                            sleep(0.1)
                    else:
                        self.fail("Timeout waiting for order to be paid")
                    if self.test_errored.is_set():
                        break
            except KeyboardInterrupt:
                self.test_errored.set()
                raise

        thread1 = Thread(target=create_order)
        thread1.start()
        thread2 = Thread(target=check_order)
        thread2.start()
        thread2.join()
        if self.test_errored.is_set():
            if self.is_terminated.is_set():
                self.fail("Test terminated by signal")
            else:
                self.fail("Test errored (see above)")

    def setUp(self) -> None:
        self.test_errored = Event()
        self.orig_sigint_handler = getsignal(SIGINT)
        self.orig_sigterm_handler = getsignal(SIGTERM)
        self.lock = Lock()
        self.is_terminated = Event()

        def signal_handler(signum: int, _: Any) -> None:
            self.is_terminated.set()
            print(
                f"Test process received signal {signum} ",
                f"('{strsignal(signum)}'), stopping runner...",
            )
            self.test_errored.set()
            with self.lock:
                if hasattr(self, "runner"):
                    self.runner.stop()

        signal(SIGINT, signal_handler)
        signal(SIGTERM, signal_handler)

    def tearDown(self) -> None:
        if hasattr(self, "runner"):
            self.runner.stop()
        signal(SIGINT, self.orig_sigint_handler)
        signal(SIGTERM, self.orig_sigterm_handler)

    def start_runner(self, env: EnvType, with_subprocesses: bool) -> None:
        with self.lock:
            if not self.is_terminated.is_set():
                self.runner = GrpcRunner(system=system, env=env)
        if hasattr(self, "runner"):
            self.runner.start(with_subprocesses=with_subprocesses)
            if self.runner.has_errored.is_set():
                self.fail("Couldn't start runner")

from threading import Event, Thread
from typing import Iterator
from unittest import TestCase

from eventsourcing_grpc.application_client import ApplicationClient
from eventsourcing_grpc.application_server import ApplicationServer


class ApplicationThread(Thread):
    def __init__(self, address: str) -> None:
        super().__init__(daemon=True)
        self.address = address
        self.is_started = Event()

    def run(self) -> None:
        self.server = ApplicationServer(address=self.address)
        self.server.start()
        self.is_started.set()
        print("Thread waiting for termination")
        self.server.server.wait_for_termination()
        print("Thread exited")


class TestApplicationServer(TestCase):
    def setUp(self) -> None:
        self.port_generator = self.generate_ports()
        self.address = self.create_address()
        self.thread = ApplicationThread(self.address)
        self.thread.start()
        self.thread.is_started.wait()

    def tearDown(self) -> None:
        self.thread.server.stop(grace=1)

    def test_client(self) -> None:
        self.client = ApplicationClient(address=self.address)
        self.client.connect(timeout=1)
        # self.client.ping()

    def create_address(self) -> str:
        """
        Creates a new address for a gRPC server.
        """
        return "localhost:%s" % next(self.port_generator)

    def generate_ports(self, start: int = 50051) -> Iterator[int]:
        """
        Generator that yields a sequence of ports from given start number.
        """
        i = 0
        while True:
            yield start + i
            i += 1

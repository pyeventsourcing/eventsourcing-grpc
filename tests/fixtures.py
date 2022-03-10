import logging
from typing import Any, Dict, Optional, cast
from uuid import NAMESPACE_OID, UUID, uuid5

from eventsourcing.application import ProcessingEvent
from eventsourcing.domain import Aggregate, DomainEvent, event
from eventsourcing.persistence import (
    OperationalError,
    RecordConflictError,
    Transcoder,
    Transcoding,
)
from eventsourcing.system import ProcessApplication, System
from eventsourcing.utils import retry

# def set_db_uri():
#     raise Exception("Use PostgreSQL here instead of MySQL..........")
#     host = os.getenv("MYSQL_HOST", "127.0.0.1")
#     user = os.getenv("MYSQL_USER", "eventsourcing")
#     password = os.getenv("MYSQL_PASSWORD", "eventsourcing")
#     db_uri = (
#         "mysql+pymysql://{}:{}@{}/eventsourcing?charset=utf8mb4&binary_prefix=true"
#     ).format(user, password, host)
#     os.environ["DB_URI"] = db_uri


class Order(Aggregate):
    def __init__(self) -> None:
        self.is_reserved = False
        self.is_paid = False
        self.reservation_id: Optional[UUID] = None
        self.payment_id: Optional[UUID] = None

    class Reserved(Aggregate.Event["Order"]):
        reservation_id: UUID

    @event(Reserved)
    def set_is_reserved(self, reservation_id: UUID) -> None:
        assert not self.is_reserved, "Order {} already reserved.".format(self.id)
        self.is_reserved = True
        self.reservation_id = reservation_id

    @event("Paid")
    def set_is_paid(self, payment_id: UUID) -> None:
        assert not self.is_paid, "Order {} already paid.".format(self.id)
        self.is_paid = True
        self.payment_id = payment_id


class Reservation(Aggregate):
    class Created(Aggregate.Created["Payment"]):
        order_id: UUID

    @event(Created)
    def __init__(self, order_id: UUID) -> None:
        self.order_id = order_id

    @classmethod
    def create_id(cls, order_id: UUID) -> UUID:
        return uuid5(NAMESPACE_OID, str(order_id))


class Payment(Aggregate):
    __slots__ = ["order_id"]

    class Created(Aggregate.Created["Payment"]):
        order_id: UUID

    @event(Created)
    def __init__(self, order_id: UUID):
        self.order_id = order_id


logger = logging.getLogger()


class OrderAsDict(Transcoding):
    type = Order
    name = "order_as_dict"

    def encode(self, obj: Order) -> Dict[str, Any]:
        return obj.__dict__

    def decode(self, data: Dict[str, Any]) -> Order:
        aggregate = object.__new__(Order)
        aggregate.__dict__.update(data)
        return aggregate


class Orders(ProcessApplication):
    def register_transcodings(self, transcoder: Transcoder) -> None:
        super(Orders, self).register_transcodings(transcoder)
        transcoder.register(OrderAsDict())

    def policy(
        self,
        domain_event: DomainEvent[Any],
        processing_event: ProcessingEvent,
    ) -> None:
        if isinstance(domain_event, Reservation.Created):
            # Set the order as reserved.
            order = self._get_order(order_id=domain_event.order_id)
            assert not order.is_reserved
            order.set_is_reserved(domain_event.originator_id)
            processing_event.collect_events(order)

        elif isinstance(domain_event, Payment.Created):
            # Set the order as paid.
            order = self._get_order(domain_event.order_id)
            assert not order.is_paid
            order.set_is_paid(domain_event.originator_id)
            processing_event.collect_events(order)

    @retry((OperationalError, RecordConflictError), max_attempts=10, wait=0.01)
    def create_new_order(self) -> UUID:
        order = Order()
        self.save(order)
        return order.id

    def is_order_reserved(self, order_id: UUID) -> bool:
        order = self._get_order(order_id)
        return order is not None

    def is_order_paid(self, order_id: UUID) -> bool:
        order = self._get_order(order_id)
        return order is not None and order.is_paid

    def _get_order(self, order_id: UUID) -> Order:
        return cast(Order, self.repository.get(order_id))

    def get_order(self, order_id: UUID) -> Dict[str, Any]:
        order = self._get_order(order_id)
        return {
            "id": order.id,
            "is_reserved": order.is_reserved,
            "is_paid": order.is_paid,
            "reservation_id": order.reservation_id,
            "payment_id": order.payment_id,
        }


class Reservations(ProcessApplication):
    def policy(
        self,
        domain_event: DomainEvent[Any],
        processing_event: ProcessingEvent,
    ) -> None:
        if isinstance(domain_event, Order.Created):
            # Create a reservation.
            reservation = Reservation(order_id=domain_event.originator_id)
            processing_event.collect_events(reservation)


class Payments(ProcessApplication):
    def policy(
        self,
        domain_event: DomainEvent[Any],
        processing_event: ProcessingEvent,
    ) -> None:
        if isinstance(domain_event, Order.Reserved):
            # Make a payment.
            payment = Payment(order_id=domain_event.originator_id)
            processing_event.collect_events(payment)


system = System([[Orders, Reservations, Orders, Payments, Orders]])

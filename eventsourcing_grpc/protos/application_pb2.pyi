"""
@generated by mypy-protobuf.  Do not edit manually!
isort:skip_file
"""
import builtins
import google.protobuf.descriptor
import google.protobuf.internal.containers
import google.protobuf.message
import typing
import typing_extensions

DESCRIPTOR: google.protobuf.descriptor.FileDescriptor

class Empty(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor
    def __init__(
        self,
    ) -> None: ...

global___Empty = Empty

class MethodRequest(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor
    METHOD_NAME_FIELD_NUMBER: builtins.int
    ARGS_FIELD_NUMBER: builtins.int
    KWARGS_FIELD_NUMBER: builtins.int
    method_name: typing.Text
    args: builtins.bytes
    kwargs: builtins.bytes
    def __init__(
        self,
        *,
        method_name: typing.Text = ...,
        args: builtins.bytes = ...,
        kwargs: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "args", b"args", "kwargs", b"kwargs", "method_name", b"method_name"
        ],
    ) -> None: ...

global___MethodRequest = MethodRequest

class MethodReply(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor
    DATA_FIELD_NUMBER: builtins.int
    data: builtins.bytes
    def __init__(
        self,
        *,
        data: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["data", b"data"]
    ) -> None: ...

global___MethodReply = MethodReply

class NotificationsRequest(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor
    START_FIELD_NUMBER: builtins.int
    LIMIT_FIELD_NUMBER: builtins.int
    STOP_FIELD_NUMBER: builtins.int
    TOPICS_FIELD_NUMBER: builtins.int
    start: typing.Text
    limit: typing.Text
    stop: typing.Text
    @property
    def topics(
        self,
    ) -> google.protobuf.internal.containers.RepeatedScalarFieldContainer[
        typing.Text
    ]: ...
    def __init__(
        self,
        *,
        start: typing.Text = ...,
        limit: typing.Text = ...,
        stop: typing.Text = ...,
        topics: typing.Optional[typing.Iterable[typing.Text]] = ...,
    ) -> None: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "limit", b"limit", "start", b"start", "stop", b"stop", "topics", b"topics"
        ],
    ) -> None: ...

global___NotificationsRequest = NotificationsRequest

class Notification(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor
    ID_FIELD_NUMBER: builtins.int
    ORIGINATOR_ID_FIELD_NUMBER: builtins.int
    ORIGINATOR_VERSION_FIELD_NUMBER: builtins.int
    TOPIC_FIELD_NUMBER: builtins.int
    STATE_FIELD_NUMBER: builtins.int
    id: typing.Text
    originator_id: typing.Text
    originator_version: typing.Text
    topic: typing.Text
    state: builtins.bytes
    def __init__(
        self,
        *,
        id: typing.Text = ...,
        originator_id: typing.Text = ...,
        originator_version: typing.Text = ...,
        topic: typing.Text = ...,
        state: builtins.bytes = ...,
    ) -> None: ...
    def ClearField(
        self,
        field_name: typing_extensions.Literal[
            "id",
            b"id",
            "originator_id",
            b"originator_id",
            "originator_version",
            b"originator_version",
            "state",
            b"state",
            "topic",
            b"topic",
        ],
    ) -> None: ...

global___Notification = Notification

class NotificationsReply(google.protobuf.message.Message):
    DESCRIPTOR: google.protobuf.descriptor.Descriptor
    NOTIFICATIONS_FIELD_NUMBER: builtins.int
    @property
    def notifications(
        self,
    ) -> google.protobuf.internal.containers.RepeatedCompositeFieldContainer[
        global___Notification
    ]: ...
    def __init__(
        self,
        *,
        notifications: typing.Optional[typing.Iterable[global___Notification]] = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["notifications", b"notifications"]
    ) -> None: ...

global___NotificationsReply = NotificationsReply

class PromptRequest(google.protobuf.message.Message):
    """message FollowRequest {
     string name = 1;
     string address = 2;
    }

    message LeadRequest {
     string name = 1;
     string address = 2;
    }

    """

    DESCRIPTOR: google.protobuf.descriptor.Descriptor
    UPSTREAM_NAME_FIELD_NUMBER: builtins.int
    upstream_name: typing.Text
    def __init__(
        self,
        *,
        upstream_name: typing.Text = ...,
    ) -> None: ...
    def ClearField(
        self, field_name: typing_extensions.Literal["upstream_name", b"upstream_name"]
    ) -> None: ...

global___PromptRequest = PromptRequest

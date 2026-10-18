"""Apply CLI configuration to SDK tool schemas and preserve operational errors."""

from collections.abc import Awaitable, Callable
from copy import deepcopy
from functools import wraps
from inspect import Parameter, signature
from typing import Annotated, Any, ParamSpec, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, create_model
from sqlalchemy.exc import SQLAlchemyError

from gh_maintainer.config import Config

P = ParamSpec("P")
R = TypeVar("R")


def configured_tool(server: MCPServer[None], config: Config) -> Callable[..., Any]:
    """Register explicit tools with the selected CLI command defaults.

    Signatures drive the SDK's validation and discovery together. Nested request
    models retain configured defaults when callers supply only some fields.
    """
    defaults = config.command_defaults()

    def tool(
        *,
        annotations: ToolAnnotations,
        nested: tuple[str, type[BaseModel]] | None = None,
    ) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
        def register(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
            values = defaults.get(fn.__name__.replace("_", "-"), {})
            sig = signature(fn, eval_str=True)
            nested_model = None
            if nested is not None:
                name, model = nested
                fields: dict[str, Any] = {}
                for key, value in values.items():
                    if key in model.model_fields and value is not None:
                        field = deepcopy(model.model_fields[key])
                        field.default, field.default_factory = value, None
                        field.validate_default = True
                        fields[key] = (field.annotation, field)
                if fields:
                    nested_model = create_model(
                        f"{model.__name__}Defaults", __base__=model, **fields
                    )
            parameters = []
            for name, original in sig.parameters.items():
                # SDK calls use keyword arguments; configured required arguments
                # can become optional ahead of other required arguments.
                parameter = original.replace(kind=Parameter.KEYWORD_ONLY)
                key = (
                    "policy_file"
                    if fn.__name__ == "policy" and name == "file"
                    else name
                )
                if nested is not None and name == nested[0] and nested_model:
                    parameter = parameter.replace(
                        annotation=nested_model | None, default=nested_model()
                    )
                elif key in values and values[key] is not None:
                    parameter = parameter.replace(
                        annotation=Annotated[
                            parameter.annotation, Field(validate_default=True)
                        ],
                        default=values[key],
                    )
                parameters.append(parameter)

            @wraps(fn)
            async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
                if nested_model and nested and kwargs.get(nested[0]) is None:
                    kwargs[nested[0]] = nested_model()
                try:
                    return await fn(*args, **kwargs)
                except (RuntimeError, ValueError, OSError, SQLAlchemyError) as error:
                    raise ToolError(str(error)) from error

            # inspect.signature is the SDK's public tool-registration contract.
            wrapped.__signature__ = sig.replace(parameters=parameters)  # type: ignore[attr-defined]
            return server.tool(annotations=annotations)(wrapped)

        return register

    return tool

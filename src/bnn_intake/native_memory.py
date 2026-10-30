"""In-process qualification transport for existing native read interfaces.

Same schemas, signed RPC admission and Broker as the official stdio server.
No second Host, memory store, source permission or model executor.
Production profiles with an existing context MCP transport don't install this.
"""
import copy
from .storage import canonical

READ_TOOLS = {"memory_read", "memory_status", "memory_working", "memory_recovery",
              "document_read", "document_status", "document_describe"}


def register(host, ctx):
    from bn_memory.schemas import SCHEMAS
    from agent.tool_attempt import current_tool_attempt
    from bn_memory.write_origin import rpc_origin
    handles = []
    for name in sorted(READ_TOOLS & set(host.rpc.tools())):
        description, properties, required = SCHEMAS[name]
        fields = copy.deepcopy(properties)
        fields.update(_host={"type": "object", "description": "Trusted native authority; the model does not construct this."},
                      _runtime={"type": "object", "description": "Native execution metadata only."})
        full_name = "mcp__context__" + name
        def handler(args, _name=name, _full_name=full_name, **kw):
            attempt = current_tool_attempt()
            if attempt is None or not attempt.active.is_set() or attempt.tool_name != _full_name or attempt.profile_home != host.admission.profile_home:
                raise PermissionError("intake_native_memory_attempt_required")
            sid, nonce = host.rpc.claim(_name, args)
            try:
                with rpc_origin(host, sid, nonce):
                    value = host.broker.dispatch(sid, _name, {k: v for k, v in args.items() if k != "_host"})
                host.rpc.finish(nonce, value)
                return canonical(value)
            except BaseException:
                host.rpc.finish(nonce, {"status": "blocked"}, failed=True)
                raise
        handles.append(ctx.register_tool(name=full_name, toolset="context", schema={"name": full_name, "description": description,
            "parameters": {"type": "object", "properties": fields, "required": required, "additionalProperties": False}}, handler=handler))
    if any(h is None for h in handles) or not handles:
        for handle in reversed(handles):
            if handle is not None:
                handle.dispose()
        raise RuntimeError("intake_native_memory_transport_registration_incomplete")
    return handles

"""Candidate attachment to the existing common-memory plugin, not a second Host."""
from .contracts import ANALYSIS_ACTIONS, DELEGATION_ACTIONS, DOCUMENT_ACTIONS, METHOD_ACTIONS, QUESTION_ACTIONS, RESEARCH_ACTIONS, WORK_ACTIONS, tool_schema, validate
from .engine import Intake
from .storage import canonical


def attach(host, ctx):
    """Call only from an admitted common-memory release with the matching tool policy.

    This does not change mandatory controls, migrate a DB or authorize a native tool.
    A3 must qualify the common-memory action policy and full native dispatch route.
    """
    from .common_policy import install_runtime_adapters
    install_runtime_adapters(host)
    engine = Intake(host.store)
    from .documents import DocumentBridge
    engine.documents = DocumentBridge(engine, host.broker, role_resolver=host.document_role)
    from .analysis import Analysis
    engine.analysis = Analysis(engine)
    from .research import Research
    engine.research = Research(engine, host)
    from .delegation import Delegation
    engine.delegation = Delegation(engine, host)

    def active_identity(name):
        from agent.tool_attempt import current_tool_attempt
        attempt = current_tool_attempt()
        if (attempt is None or not attempt.active.is_set() or attempt.tool_name != name
                or attempt.profile_home != host.admission.profile_home):
            raise PermissionError("intake_native_tool_attempt_required")
        sid = attempt.identity["session_id"]
        host.admission.ready(sid, attempt.identity["turn_id"])
        return sid

    def work(args, **kw):
        sid = active_identity("intake_work")
        return canonical(engine.call(sid, args))

    def methods(args, **kw):
        sid = active_identity("intake_methods")
        host.store.writable(sid)
        if engine.mode(sid)["mode"] == "off":
            raise PermissionError("intake_disabled_for_session")
        validate(args, METHOD_ACTIONS)
        return canonical(engine.catalogue.call(args))

    def questions(args, **kw):
        from .interview import Interview
        sid = active_identity("intake_questions")
        return canonical(Interview(engine).call(sid, args))

    def documents(args, **kw):
        sid = active_identity("intake_documents")
        return canonical(engine.documents.call(sid, args))

    def analysis(args, **kw):
        sid = active_identity("intake_analysis")
        return canonical(engine.analysis.call(sid, args))

    def research(args, **kw):
        return canonical(engine.research.call(active_identity("intake_research"), args))

    def delegation(args, **kw):
        return canonical(engine.delegation.call(active_identity("intake_delegation"), args))

    def preflight(payload, **identity):
        name = payload.get("tool_name")
        if name not in {"intake_work", "intake_methods", "intake_questions", "intake_documents", "intake_analysis", "intake_research", "intake_delegation"}:
            return
        sid = active_identity(name)
        args = payload.get("args")
        if name == "intake_delegation":
            engine.delegation.preflight(sid, args)
        elif name == "intake_research":
            engine.research.preflight(sid, args)
        elif name == "intake_analysis":
            engine.analysis.preflight(sid, args)
        elif name == "intake_documents":
            validate(args, DOCUMENT_ACTIONS)
            engine.documents.state(sid, args["work_ref"], active=True)
        elif name == "intake_questions":
            from .interview import Interview
            Interview(engine).preflight(sid, args)
        elif name == "intake_methods":
            validate(args, METHOD_ACTIONS)
            if engine.mode(sid)["mode"] == "off":
                raise PermissionError("intake_disabled_for_session")
        else:
            from .engine import MUTATIONS
            validate(args)
            if args["action"] in MUTATIONS and engine.mode(sid)["mode"] == "off":
                raise PermissionError("intake_disabled_for_session")
            if args['action']=='assess':
                from .acceptance import precheck
                precheck(engine,sid,args)

    def command(raw):
        from .commands import owner_command
        return owner_command(host, engine, raw)

    handles = [ctx.register_tool(name="intake_work", toolset="bnn_intake",
                   schema=tool_schema("intake_work", "Версионированная подготовка в привязанной работе. Не выполняет поиск или модельный анализ за исполнителя.", WORK_ACTIONS), handler=work),
               ctx.register_tool(name="intake_methods", toolset="bnn_intake",
                   schema=tool_schema("intake_methods", "Адресное чтение открытого методического каталога. Новый подход допустим через локальную карточку.", METHOD_ACTIONS), handler=methods),
               ctx.register_tool(name="intake_questions", toolset="bnn_intake",
                   schema=tool_schema("intake_questions", "План и выдача вопроса с проверкой сохранённых ограничений. Не отправляет сообщение человеку.", QUESTION_ACTIONS), handler=questions),
               ctx.register_tool(name="intake_documents", toolset="bnn_intake",
                   schema=tool_schema("intake_documents", "Привязка штатного корпуса к работе, полный постраничный план фрагментов и покрытие по квитанциям. План не означает чтение. Разбор выполняется штатными document-инструментами.", DOCUMENT_ACTIONS), handler=documents),
               ctx.register_tool(name="intake_analysis", toolset="bnn_intake",
                   schema=tool_schema("intake_analysis", "Конкретное применение метода и типизированный продукт с источниками. Приём продукта требует точного вызова из возвращённого нативного исполнения. Структурная проверка не даёт смысловой приёмки.", ANALYSIS_ACTIONS), handler=analysis),
               ctx.register_tool(name="intake_research", toolset="bnn_intake",
                   schema=tool_schema("intake_research", "Связь выбранного исследовательского пути с реальными допущенными вызовами чтения. Выбор не разрешает инструмент. Результаты читаются порциями; оценки оператора моделью не выставляются.", RESEARCH_ACTIONS), handler=research),
               ctx.register_tool(name="intake_delegation", toolset="bnn_intake",
                   schema=tool_schema("intake_delegation", "Адресное назначение intake существующему нативному исполнителю. Не запускает агента, не выдаёт прав. Возвращённый продукт читается из точной версии нативной истории.", DELEGATION_ACTIONS), handler=delegation)]
    handles.append(ctx.register_command("intake", command,
                    description="Режим смысловой подготовки в текущей беседе",
                    args_hint="on|off|auto|status|interview"))
    if any(h is None for h in handles):
        for handle in reversed(handles):
            if handle is not None:
                handle.dispose()
        raise RuntimeError("intake_registration_incomplete")
    bridge = None
    if host.store.config.get("semantic_intake_bridge", False):
        from .context import ContextBridge
        try:
            bridge = ContextBridge(engine, host, preflight=preflight)
            bridge.install()
            handles.append(ctx.on_unload(bridge.close))
        except BaseException:
            if bridge is not None:
                bridge.close()
            for handle in reversed(handles):
                if handle is not None:
                    handle.dispose()
            raise
    return {"engine": engine, "registrations": handles,
            "context_bridge": bridge,
            "live_activation": False, "native_dispatch_qualified": False}

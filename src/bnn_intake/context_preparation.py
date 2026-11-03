"""Stage native input cheaply; complete preparation on the trusted request path.

No model dispatch, task replay, authority change or new session. The supplier's
signed Admission remains the final authority. A skipped staging hook grants none.
"""


class ContextPreparation:
    def __init__(self, host):
        import threading, time
        self.host=host;self.lock=threading.RLock();self.clock=time.monotonic;self.pending={}
        self.before=host.before_turn;self.request=host.request
        self.timeout=host.config.get('context_preparation_timeout_seconds',180)
        import math
        if type(self.timeout) not in (int,float) or not math.isfinite(self.timeout) or self.timeout<=0:
            raise ValueError('context_preparation_timeout_invalid')

    def stage(self, user_message='', session_id='', parent_session_id='', platform='', turn_id='', **kw):
        import copy
        from bn_memory.admission import anchors_for
        if not session_id or not isinstance(turn_id,str) or not turn_id or len(turn_id)>512:
            raise PermissionError('native_turn_id_required')
        anchors=anchors_for(user_message)
        with self.lock:
            old=self.pending.get(session_id)
            if old and old['turn_id']==turn_id:
                if old['anchors']!=anchors:raise PermissionError('native_context_stage_input_changed')
                return None
            if old and old['state']=='preparing':raise PermissionError('native_context_preparation_in_progress')
            self.pending[session_id]={'turn_id':turn_id,'anchors':anchors,'state':'staged',
                'input':dict(user_message=copy.deepcopy(user_message),session_id=session_id,
                    parent_session_id=parent_session_id,platform=platform,turn_id=turn_id),
                'hint':'','token':None,'error':None}
        return None

    def event(self, entry, phase, outcome, elapsed):
        import logging,hashlib
        turn_hash=hashlib.sha256(entry['turn_id'].encode()).hexdigest()[:16]
        logging.getLogger('bnn.context_preparation').info(
            'BN_CONTEXT_PREPARATION turn=%s phase=%s outcome=%s elapsed_ms=%d',
            turn_hash,phase,outcome,round(elapsed*1000))

    def prepare(self,sid,turn):
        with self.lock:
            entry=self.pending.get(sid)
            if entry is None:
                # Compatibility with a directly invoked, already completed native
                # before_turn. It cannot admit a different/stale turn.
                self.host.admission.row(sid,turn)
                return ''
            if entry['turn_id']!=turn:raise PermissionError('native_context_stage_turn_mismatch')
            if entry['state']=='ready':return entry['hint']
            if entry['state']=='failed':raise PermissionError(entry['error'])
            if entry['state']=='preparing':raise PermissionError('native_context_preparation_reentrant')
            entry['state']='preparing'
        start=self.clock();self.event(entry,'assemble_and_admit','started',0)
        try:
            value=self.before(**entry['input'])
            row=self.host.admission.row(sid,turn);entry['token']=row['token']
            text=value.get('context','') if isinstance(value,dict) else ''
            if not text.startswith(row['envelope']):raise PermissionError('prepared_native_envelope_required')
            elapsed=self.clock()-start
            if elapsed>self.timeout:raise PermissionError('context_preparation_deadline_exceeded')
            with self.lock:
                if self.pending.get(sid) is not entry:raise PermissionError('context_preparation_superseded')
                entry['hint']=text[len(row['envelope']):];entry['state']='ready'
                entry['input']=None
            self.event(entry,'assemble_and_admit','ready',elapsed)
            return entry['hint']
        except BaseException as exc:
            if entry['token'] is None:
                try:entry['token']=self.host.admission.row(sid,turn)['token']
                except PermissionError:pass
            if entry['token']:self.host.admission.fail(sid,entry['token'])
            with self.lock:
                import re
                entry['state']='failed';entry['input']=None
                # No raw user content or untrusted exception text in retained state.
                reason=str(exc)
                entry['error']=reason if re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',reason) else 'native_context_preparation_failed:'+type(exc).__name__
            self.event(entry,'assemble_and_admit','failed',self.clock()-start)
            raise

    def prepare_request(self,request,session_id='',**kw):
        from agent.auxiliary_attempt import current_auxiliary_attempt
        if not session_id or kw.get('execution_kind')=='auxiliary' or current_auxiliary_attempt() is not None:
            return self.request(request,session_id=session_id,**kw)
        hint=self.prepare(session_id,kw.get('turn_id'))
        if hint:
            import copy
            from bn_memory.admission import last_user,text_parts
            request=copy.deepcopy(request);row=last_user(request)
            if hint not in '\n'.join(text_parts(row.get('content'))):
                if isinstance(row.get('content'),str):row['content']+=hint
                elif isinstance(row.get('content'),list):row['content'].append({'type':'input_text' if 'input' in request else 'text','text':hint})
                else:raise PermissionError('native_context_envelope_required')
        return self.request(request,session_id=session_id,**kw)


def install_context_preparation(host):
    if getattr(host,'_native_context_preparation',None) is not None:return
    value=ContextPreparation(host)
    host._native_context_preparation=value
    host.stage_turn=value.stage
    host.request=value.prepare_request

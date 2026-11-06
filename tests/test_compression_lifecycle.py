"""Exact goal continuity across real local admission transitions; no network calls."""
import copy
import json

import pytest
from agent.compression_checkpoint import CheckpointInput
from bn_memory.admission import digest
from conftest import record
from test_native_context import native, turn, model_admit


GOAL = 'Продолжить исследование α. Сохранить точные байты. \n'
TURN = 'compression-lifecycle'


def request(text=GOAL):
    return {'model': 'boundary-test-no-provider', 'instructions': 'Stable system layer',
            'input': [{'role': 'user', 'content': text}]}


def checkpoint(native, messages=None):
    source = CheckpointInput(str(native.home), 'parent', '', 'cli',
                             messages or [{'role': 'user', 'content': GOAL}])
    native.host.checkpoints.save(source, [])
    native.host.checkpoints.native_commit('parent', 'parent', 'cli')


def refreshed(native):
    record(native.host.store, 'changed-after-compression', 'New exact source record.')
    prepared = native.host.request(request(), session_id='parent', turn_id=TURN,
                                   platform='cli')['request']
    assert model_admit(prepared, TURN, 'post-refresh')[0].allow
    return prepared


def test_committed_goal_survives_data_refresh_and_real_final_admission(native):
    turn(native, GOAL, TURN)
    checkpoint(native)
    old = native.host.admission.row('parent', TURN)['token']
    refreshed(native)
    assert native.host.admission.row('parent', TURN)['token'] != old
    wrapped = request('Native compression recovery wrapper\n' + GOAL)
    native.host.checkpoints.restore_goal('parent', TURN, wrapped)
    prepared = native.host.request(wrapped, session_id='parent', turn_id=TURN,
                                   platform='cli')['request']
    assert model_admit(prepared, TURN, 'after-restoration')[0].allow
    assert prepared['input'][0]['content'].startswith(GOAL + '\n')
    again = copy.deepcopy(wrapped)
    native.host.checkpoints.restore_goal('parent', TURN, again)
    assert again == wrapped


def test_second_compression_uses_prior_exact_goal_after_refresh(native):
    turn(native, GOAL, TURN)
    checkpoint(native)
    refreshed(native)
    checkpoint(native, [{'role': 'user', 'content': 'Compressed native history'}])
    wrapped = request('Another native compression wrapper')
    native.host.checkpoints.restore_goal('parent', TURN, wrapped)
    assert wrapped['input'][0]['content'].startswith(GOAL + '\n')


def test_exact_goal_restore_allows_refresh_required_but_not_failed_admission(native):
    turn(native, GOAL, TURN)
    checkpoint(native)
    with native.host.store.db() as db:
        db.execute("UPDATE context_admissions SET status='refresh_required' WHERE sid='parent'")
    native.host.checkpoints.restore_goal('parent', TURN, request('Compressed native history'))
    with native.host.store.db() as db:
        db.execute("UPDATE context_admissions SET status='failed' WHERE sid='parent'")
    with pytest.raises(PermissionError, match='current_turn_context_not_ready'):
        native.host.checkpoints.restore_goal('parent', TURN, request('Compressed native history'))


def test_same_text_in_a_new_turn_cannot_borrow_old_compression_commit(native):
    turn(native, GOAL, TURN)
    checkpoint(native)
    turn(native, GOAL, 'another-turn')
    with pytest.raises(PermissionError, match='native_compression_commit_required_for_goal_restore'):
        native.host.checkpoints.restore_goal('parent', 'another-turn', request('Compressed native history'))


def test_corrupt_checkpoint_is_denied_even_when_goal_hash_is_valid(native):
    turn(native, GOAL, TURN)
    checkpoint(native)
    refreshed(native)
    with native.host.store.db() as db:
        db.execute("UPDATE checkpoints SET payload=payload||' ' WHERE sid='parent'")
    with pytest.raises(PermissionError, match='checkpoint_integrity_failed'):
        native.host.checkpoints.restore_goal('parent', TURN, request('Compressed native history'))


def test_goal_hash_alone_cannot_restore_different_current_input(native):
    turn(native, GOAL, TURN)
    checkpoint(native)
    with native.host.store.db() as db:
        value = 'Different exact goal'
        db.execute('UPDATE compression_commits SET goal=?,goal_sha256=? WHERE sid=?',
                   (value, digest(value), 'parent'))
    with pytest.raises(PermissionError):
        native.host.checkpoints.restore_goal('parent', TURN, request('Compressed native history'))


def test_new_turn_cannot_commit_a_checkpoint_from_another_turn(native):
    turn(native, GOAL, TURN)
    checkpoint(native)
    turn(native, GOAL, 'another-turn')
    with pytest.raises(PermissionError, match='compression_checkpoint_turn_changed'):
        native.host.checkpoints.native_commit('parent', 'parent', 'cli')


def test_intake_revision_refresh_does_not_erase_native_compression_proof(native):
    from bnn_intake.interview import Interview
    turn(native, GOAL, TURN)
    checkpoint(native)
    old = native.host.admission.row('parent', TURN)['token']
    Interview(native.engine).control('parent', {'action': 'stop', 'work_ref': native.work,
        'expected_revision': 3, 'operation_id': 'stop-after-compression'}, 'native-owner')
    prepared = native.host.request(request(), session_id='parent', turn_id=TURN,
                                   platform='cli')['request']
    assert native.host.admission.row('parent', TURN)['token'] != old
    wrapped = request('Compressed native history')
    native.host.checkpoints.restore_goal('parent', TURN, wrapped)
    prepared = native.host.request(wrapped, session_id='parent', turn_id=TURN,
                                   platform='cli')['request']
    assert model_admit(prepared, TURN, 'changed-intake')[0].allow


def test_data_update_while_compression_runs_does_not_destroy_exact_goal(native):
    turn(native, GOAL, TURN)
    source = CheckpointInput(str(native.home), 'parent', '', 'cli',
                             [{'role': 'user', 'content': GOAL}])
    native.host.checkpoints.save(source, [])
    record(native.host.store, 'changed-during-compression', 'New source version.')
    native.host.checkpoints.native_commit('parent', 'parent', 'cli')
    wrapped = request('Compressed native history')
    native.host.checkpoints.restore_goal('parent', TURN, wrapped)
    prepared = native.host.request(wrapped, session_id='parent', turn_id=TURN,
                                   platform='cli')['request']
    assert model_admit(prepared, TURN, 'compression-race')[0].allow


def test_existing_legacy_commit_survives_reload_and_new_admission_token(native):
    from bn_memory.checkpoint import Checkpoints
    from bn_memory.admission import canonical
    turn(native, GOAL, TURN)
    checkpoint(native)
    # Existing installed checkpoints predate native_turn metadata.
    with native.host.store.db() as db:
        cp = db.execute("SELECT id,payload FROM checkpoints WHERE sid='parent'").fetchone()
        payload = json.loads(cp['payload'])
        del payload['native_turn']
        raw = canonical(payload)
        db.execute('UPDATE checkpoints SET payload=?,sha256=? WHERE id=?', (raw, digest(raw), cp['id']))
    refreshed(native)
    reloaded = Checkpoints(native.host)
    wrapped = request('Compressed native history')
    reloaded.restore_goal('parent', TURN, wrapped)
    assert wrapped['input'][0]['content'].startswith(GOAL + '\n')


def test_changed_work_binding_cannot_reuse_another_work_checkpoint(native):
    turn(native, GOAL, TURN)
    checkpoint(native)
    with native.host.store.db() as db:
        db.execute("UPDATE sessions SET task='another-work' WHERE sid='parent'")
    with pytest.raises(PermissionError, match='compression_checkpoint_binding_changed'):
        native.host.checkpoints.restore_goal('parent', TURN, request('Compressed native history'))


def test_restoring_goal_preserves_system_layer_and_does_not_replay_tools(native):
    turn(native, GOAL, TURN)
    checkpoint(native)
    refreshed(native)
    outgoing = request('Compressed native history')
    outgoing['input'] += [{'type': 'function_call', 'name': 'prior-write', 'call_id': 'done'},
                          {'type': 'function_call_output', 'call_id': 'done', 'output': 'saved'}]
    saved_tools = copy.deepcopy(outgoing['input'][1:])
    native.host.checkpoints.restore_goal('parent', TURN, outgoing)
    assert outgoing['instructions'] == 'Stable system layer'
    assert outgoing['input'][1:] == saved_tools


def test_repeated_refresh_keeps_one_current_envelope_without_context_accumulation(native):
    turn(native, GOAL, TURN)
    checkpoint(native)
    sizes = []
    for index in range(8):
        record(native.host.store, 'refresh-' + str(index), 'Small new source ' + str(index))
        outgoing = request('Compressed native history')
        native.host.checkpoints.restore_goal('parent', TURN, outgoing)
        prepared = native.host.request(outgoing, session_id='parent', turn_id=TURN,
                                       platform='cli')['request']
        text = json.dumps(prepared, ensure_ascii=False)
        assert text.count('BN_CONTEXT_ADMISSION signature') == 1
        assert text.count('BN_CONTINUITY signature') == 1
        assert model_admit(prepared, TURN, 'refresh-api-' + str(index))[0].allow
        sizes.append(len(text.encode()))
    # Small source additions must not embed all preceding signed contexts.
    assert max(sizes) < sizes[0] + 32768

"""Regression witnessed in a real CLI before any provider execution."""
import pytest
from bn_memory.admission import Admission,anchors_for,canonical
from agent.turn_request_assembly import normalize_non_user_whitespace

@pytest.mark.parametrize('goal',['Goal\n','  Goal','\tЦель α\r\n','A\nB\n\n'])
def test_native_request_keeps_exact_owner_input(goal):
    row={'anchors':canonical(anchors_for(goal))}
    messages=[{'role':'system','content':' system '},{'role':'user','content':goal},{'role':'assistant','content':' reply '}]
    normalize_non_user_whitespace(messages)
    Admission.check_anchor(row,{'messages':messages})
    assert messages[1]['content']==goal and messages[0]['content']=='system' and messages[2]['content']=='reply'

def test_preserving_input_does_not_admit_an_altered_goal():
    row={'anchors':canonical(anchors_for('Exact owner goal\n'))}
    messages=[{'role':'user','content':'Another goal\n'}]
    normalize_non_user_whitespace(messages)
    with pytest.raises(PermissionError,match='current_input_anchor_mismatch'):
        Admission.check_anchor(row,{'messages':messages})

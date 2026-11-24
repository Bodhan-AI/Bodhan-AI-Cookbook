from decimal import Decimal
import json
import numpy as np
import pytest

from bodhan_dub.fit_recovery import (Budget,borrowed_timing,compact_quiet,english_proposals,
                                    protected_labels,quantities,validate_condensation)
from bodhan_dub.media import MediaError
from bodhan_dub.reconciliation import LLMSettings


def test_compaction_removes_quiet_only_and_preserves_voiced_samples():
    voiced=np.array([1000,-1000]*12000,dtype=np.int16)
    pcm=np.concatenate([voiced,np.zeros(24000,dtype=np.int16),voiced])
    compacted,meta=compact_quiet(pcm)
    assert meta['removed_seconds']==pytest.approx(.85)
    assert np.array_equal(pcm[np.abs(pcm.astype(np.int32))>58],compacted[np.abs(compacted.astype(np.int32))>58])
    assert meta['non_quiet_samples_preserved_in_order']


def test_short_or_nonquiet_pause_is_not_removed():
    pcm=np.concatenate([np.full(24000,1000),np.zeros(2400),np.full(24000,1000)]).astype(np.int16)
    new,meta=compact_quiet(pcm)
    assert np.array_equal(new,pcm) and meta['removed_seconds']==0


def test_silence_borrow_is_bounded_and_retains_long_pause_core():
    rows=[{'id':'a','start_seconds':2,'end_seconds':5}, {'id':'b','start_seconds':8,'end_seconds':10}]
    row=borrowed_timing(rows[0],0,rows,[{'start_seconds':0,'end_seconds':2},{'start_seconds':5,'end_seconds':8}],12)
    assert row['placement_start_bound_seconds']==1.7 and row['placement_end_bound_seconds']==6
    assert all(g['end_seconds']-g['start_seconds']>=1 for g in row['protected_gap_cores'])
    assert row['available_seconds']<=4.3+1e-9


def test_small_long_gap_is_not_collapsed_by_borrowing():
    rows=[{'id':'a','start_seconds':0,'end_seconds':2.5}]
    r=borrowed_timing(rows[0],0,rows,[{'start_seconds':1,'end_seconds':2.1}],3)
    g=r['protected_gap_cores'][0]
    assert g['end_seconds']-g['start_seconds']==pytest.approx(1)


def test_condensation_preserves_number_phrases_and_labels():
    source='Angle AOC is seventy degrees and angle COB is eighty degrees. Please write the answers in your notebook.'
    proposal='AOC is seventy degrees; COB is eighty degrees. Write answers.'
    assert validate_condensation(source,proposal)==proposal
    assert ('seventy',) in quantities(source) and set(protected_labels(source))=={'AOC','COB'}


@pytest.mark.parametrize('candidate',[
    'AOC is sixty degrees; COB is eighty degrees.',
    'AOC is seventy degrees; BOD is eighty degrees.',
    'தமிழ் உரை',
])
def test_changed_facts_or_target_language_rejected(candidate):
    with pytest.raises(MediaError):validate_condensation('Angle AOC is seventy degrees and angle COB is eighty degrees. Write your answers.',candidate)


def test_removed_negation_rejected():
    with pytest.raises(MediaError):validate_condensation('These angles are not a linear pair. Write that explanation.','These angles form a linear pair.')


def test_budget_reserves_before_calls_and_keeps_uncertain_cost_on_reload(tmp_path):
    path=tmp_path/'budget.json';b=Budget(path,Decimal('1'));token=b.reserve('speech','.7')
    with pytest.raises(MediaError):b.reserve('another','.4')
    assert Budget(path,Decimal('1')).spent==Decimal('.7')
    b.settle(token,'.6');assert b.spent==Decimal('.6')
    with pytest.raises(MediaError):b.settle(token,'2')


def test_batch_condensation_requires_separate_factual_judgment(tmp_path):
    source='Angle AOC is seventy degrees and angle COB is eighty degrees. Write answers in your notebook.'
    items=[{'id':'u1','language':'ta','original_english':source,'timing':{'available_seconds':14},'observed_seconds':20}]
    replies=[{'items':[{'id':'u1','language':'ta','english':'AOC is seventy degrees; COB is eighty degrees. Write answers.'}]},
             {'checks':[{'id':'ta/u1','ok':False,'reason':'Test rejection'}]}]
    calls=[]
    def complete(settings,messages):calls.append(messages);return json.dumps(replies.pop(0)),None
    result=english_proposals(items,LLMSettings('https://example.test/v1','test','fake'),tmp_path,1,complete)
    assert len(calls)==2 and not result['accepted'] and result['errors']['ta/u1']=='factual_judge_rejected'
    assert english_proposals(items,LLMSettings('https://example.test/v1','test','fake'),tmp_path,1,lambda *_:pytest.fail('Cache should prevent another call'))==result


def test_neighbors_cannot_both_claim_the_same_unused_time():
    rows=[{'id':'a','start_seconds':1,'end_seconds':5},{'id':'b','start_seconds':5.4,'end_seconds':8}]
    left=borrowed_timing(rows[0],0,rows,[],10)
    right=borrowed_timing(rows[1],1,rows,[],10)
    assert left['placement_end_bound_seconds']<=right['placement_start_bound_seconds']


def test_equivalent_diagram_label_spacing_is_not_treated_as_a_changed_entity():
    source='Lines A B and C D cross at O. Name a linear pair and draw the figure.'
    assert validate_condensation(source,'Lines AB and CD cross at O. Name and draw a linear pair.')


def test_ordinal_numbers_are_protected():
    with pytest.raises(MediaError):validate_condensation('Fifth term: two adjacent angles share one common arm. Write the definition.',
        'Two adjacent angles share one common arm.')


def test_parallel_english_compaction_keeps_categories_labels_and_negation():
    from bodhan_dub.fit_recovery import tighten_parallel_english
    original='Name one narrow pair, one wide pair, and one adjacent pair that is not a straight pair. Angle XOY and angle YOZ share OY.'
    compact=tighten_parallel_english(original)
    assert compact=='Name one pair each: narrow, wide, and adjacent but not straight. XOY and YOZ share OY.'
    assert validate_condensation(original,compact)==compact



def test_range_endpoints_can_be_retained_as_separate_clauses():
    original = 'The angle is between ninety and one hundred and eighty degrees. This is the stated range.'
    concise = 'The angle exceeds ninety degrees and is below one hundred and eighty degrees.'
    assert validate_condensation(original, concise) == concise
    assert ('one','hundred','and','eighty') in quantities(original)
    with pytest.raises(MediaError, match='Protected number'):
        validate_condensation(original, 'The angle is above ninety degrees and below one hundred and seventy degrees.')


def test_condensation_cannot_drop_direct_instruction_even_when_facts_are_preserved():
    original = 'These two angles share a vertex. Write the example and its reasons. Read the next question.'
    with pytest.raises(MediaError, match='Source instruction was removed'):
        validate_condensation(original, 'Two angles share a vertex. Read the next question.')



def test_invalid_planner_identity_fails_only_its_unit(tmp_path):
    source='These two angles share a vertex. Please read the full explanatory sentence aloud.'
    items=[{'id':'u1','language':'ta','original_english':source,'timing':{'available_seconds':10},'observed_seconds':15}]
    result=english_proposals(items,LLMSettings('https://example.test/v1','test','fake'),tmp_path,1,
        lambda *_:(json.dumps({'items':[{'id':'u1','language':'en','english':'Two angles share a vertex.'}]}),None))
    assert result['accepted']=={}
    assert result['errors']=={'ta/u1':'condensation_missing_or_mislabeled_unit'}


def test_concurrent_budget_reservations_cannot_overspend(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    budget = Budget(tmp_path / 'budget.json', Decimal('1'))

    def reserve(index):
        try:
            return budget.reserve(f'clip-{index}', Decimal('.25'))
        except MediaError as error:
            assert error.code == 'fit_budget_exceeded'
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = [index for index in pool.map(reserve, range(12)) if index is not None]

    assert sorted(accepted) == [0, 1, 2, 3]
    assert Decimal(json.loads(budget.path.read_text())['conservative_spent_inr']) == 1


def test_uncapped_run_records_cost_above_reservation(tmp_path):
    path = tmp_path / 'budget.json'
    ledger = Budget(path, None)
    entry = ledger.reserve('fit', Decimal('.01'))
    ledger.settle(entry, Decimal('60'))
    assert Budget(path, None).spent == Decimal('60')

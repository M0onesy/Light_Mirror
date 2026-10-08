"""Real figure 10 endpoint proof and nearby financial/geometry counterexamples."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.layout_noise import (
    _axis_groups, _cells, _inside, _regions, _source_line, _vector_axis_proof,
    chart_layout_exclusions,
)


DATA=json.loads((Path(__file__).parent/'fixtures/product_chart_axis_p8.json').read_text(encoding='utf8'))
VERTICAL=[220.9199981689453,183.47998046875,220.9199981689453,100.32000732421875]
HORIZONTAL=[220.9199981689453,183.47998046875,543.719970703125,183.47998046875]


def page():return deepcopy(DATA['page'])
def axes(p):
    lines=[source for line in p['lines'] if (source:=_source_line(p['text'],line))]
    region=_regions(lines)[0]
    ticks,_=_cells([line for line in lines if _inside(line,region)])
    return region,list(_axis_groups(ticks))


def append_line(p,text,box):
    start=len(p['text']);p['text']+=text+'\n';p['end']=len(p['text'])
    p['lines'].append({'text':text,'bbox':box,'start':start,'end':start+len(text)})


def test_real_six_ticks_have_original_connected_axes_and_bounded_endpoint_receipts():
    p=page()
    assert hashlib.sha256(p['text'].encode('utf8')).hexdigest()==DATA['excerpt_page_text_sha256']
    proofs=chart_layout_exclusions(p['text'],[p])
    assert len(proofs)==6
    assert {p['text'][start:proof['basis']['occurrence_span'][1]] for start,proof in proofs.items()}=={
        '0','10000','20000','30000','40000','50000'}
    for proof in proofs.values():
        basis=proof['basis'];vectors=basis['vector_axes'];extension=basis['figure_region_extension']
        assert vectors['kind']=='source_pdf_perpendicular_axes'
        assert vectors['vertical'] in p['vector_lines'] and vectors['horizontal'] in p['vector_lines']
        assert len(basis['tick_source_spans'])==6
        assert 0<extension['horizontal_extension']<=extension['maximum_extension']<=12
        assert extension['completed_region'][2]==max(vectors['horizontal'][0],vectors['horizontal'][2])
        assert basis['figure_region']==extension['completed_region']
        assert extension['new_source_text_intersections']==[]


def test_original_financial_caption_values_and_all_raw_occurrences_remain_identical():
    p=page();no_vectors=deepcopy(p);no_vectors['vector_lines']=[]
    before,claims_before=CoverageLedger.build(p['text'],[no_vectors],[],'并行科技','2023Y')
    after,claims_after=CoverageLedger.build(p['text'],[p],[],'并行科技','2023Y')
    assert [(e['start'],e['end'],e['text']) for e in before.entries]==[
        (e['start'],e['end'],e['text']) for e in after.entries]
    assert [(c.start,c.end,c.numeric_text) for c in claims_before]==[(c.start,c.end,c.numeric_text) for c in claims_after]
    assert {c.numeric_text for c in claims_after}=={'55%','4.14 亿元'}
    ticks=[e for e in after.entries if e['reason']=='chart_axis_tick']
    assert len(ticks)==6 and all(e['exclusion_basis']['vector_axes']['kind']=='source_pdf_perpendicular_axes' for e in ticks)


def test_real_left_alignment_five_tick_subset_cannot_borrow_complete_perpendicular_axes():
    p=page();region,groups=axes(p)
    incomplete=[ticks for ticks,_,_,_ in groups if len(ticks)==5]
    complete=[ticks for ticks,_,_,_ in groups if len(ticks)==6]
    assert len(incomplete)==len(complete)==1
    assert _vector_axis_proof(p['vector_lines'],incomplete[0],region) is None
    assert _vector_axis_proof(p['vector_lines'],complete[0],region)['kind']=='source_pdf_perpendicular_axes'
    p['lines']=[line for line in p['lines'] if line['text'].strip()!='0']
    assert chart_layout_exclusions(p['text'],[p])=={}


@pytest.mark.parametrize('bad', [
    'disconnected','wrong_height','missing_vertical','missing_horizontal','far_endpoint',
    'string','boolean','nan','infinity','short_axis','outside_top','different_figure',
])
def test_invalid_or_incomplete_paths_cannot_complete_the_original_figure_boundary(bad):
    p=page();region,groups=axes(p);ticks=next(t for t,_,_,_ in groups if len(t)==6)
    vertical,horizontal=VERTICAL[:],HORIZONTAL[:]
    lines=[vertical,horizontal]
    if bad=='disconnected':horizontal[0]+=1
    elif bad=='wrong_height':horizontal[1]+=3;horizontal[3]+=3
    elif bad=='missing_vertical':lines=[horizontal]
    elif bad=='missing_horizontal':lines=[vertical]
    elif bad=='far_endpoint':horizontal[2]=region['right']+50
    elif bad=='string':horizontal[2]=str(horizontal[2])
    elif bad=='boolean':horizontal[0]=True
    elif bad=='nan':horizontal[2]=float('nan')
    elif bad=='infinity':vertical[1]=float('inf')
    elif bad=='short_axis':horizontal[2]=horizontal[0]+60
    elif bad=='outside_top':vertical[3]=region['top']-1
    else:
        lines=[[x+350 if i%2==0 else x for i,x in enumerate(line)] for line in lines]
    assert _vector_axis_proof(lines,ticks,region) is None


@pytest.mark.parametrize('kind', ['table','figure','financial_row','unrecognized_text'])
def test_written_neighbor_in_the_extension_strip_blocks_borrowing_its_space(kind):
    p=page();region,groups=axes(p);ticks=next(t for t,_,_,_ in groups if len(t)==6)
    text={'table':'表7：另一公司财务数据','figure':'图11：另一业务','financial_row':'营业收入',
          'unrecognized_text':'另一材料'}[kind]
    barrier={'start':0,'end':len(text),'text':text,'bbox':[540,125,580,135]}
    region['axis_extent_source_lines']=[*region['axis_extent_source_lines'],barrier]
    assert _vector_axis_proof([VERTICAL,HORIZONTAL],ticks,region) is None


def test_same_row_adjacent_figure_is_an_absolute_limit_despite_a_connected_path():
    p=page();append_line(p,'图11：另一业务图',[542,75,590,87])
    region,groups=axes(p)
    ticks=next(t for t,_,_,_ in groups if len(t)==6)
    assert region['axis_right_limit']==region['right']
    assert _vector_axis_proof([VERTICAL,HORIZONTAL],ticks,region) is None
    proofs=chart_layout_exclusions(p['text'],[p])
    assert all(not value['basis'].get('figure_region_extension') for value in proofs.values())


def test_actual_nearby_financial_rows_remain_candidates_and_never_supply_axis_proof():
    p=page()
    append_line(p,'营业收入',[538,115,580,125])
    append_line(p,'123万元',[540,140,582,150])
    assert chart_layout_exclusions(p['text'],[p])=={}
    ledger,_=CoverageLedger.build(p['text'],[p],[],'并行科技','2023Y')
    entry=next(e for e in ledger.entries if e['text'].strip()=='123万元')
    assert not entry.get('exclusion_basis')


def test_an_unbounded_internal_limit_cannot_authorize_arbitrary_axis_extension():
    p=page();region,groups=axes(p);ticks=next(t for t,_,_,_ in groups if len(t)==6)
    region['axis_right_limit']=10000
    horizontal=HORIZONTAL[:];horizontal[2]=region['right']+13
    assert _vector_axis_proof([VERTICAL,horizontal],ticks,region) is None


def test_a_later_adjacent_figure_cannot_supply_its_footer_or_space():
    p=page();append_line(p,'图11：相邻财务图',[538,110,590,122])
    append_line(p,'100万元',[540,140,585,150])
    assert chart_layout_exclusions(p['text'],[p])=={}


@pytest.mark.parametrize('removed', ['caption','source'])
def test_actual_source_caption_and_footer_are_still_required(removed):
    p=page()
    p['lines']=[line for line in p['lines'] if not line['text'].strip().startswith(
        '图10：' if removed=='caption' else '数据来源：')]
    assert chart_layout_exclusions(p['text'],[p])=={}


def test_original_category_baseline_without_native_vectors_keeps_its_limited_proof():
    p=page();p['vector_lines']=[]
    proofs=chart_layout_exclusions(p['text'],[p])
    assert Counter(x['reason'] for x in proofs.values())=={'chart_axis_tick':6}
    assert all(x['basis']['baseline_kind']=='categories' and not x['basis'].get('vector_axes') for x in proofs.values())

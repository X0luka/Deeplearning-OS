import copy

import pytest

from aos.knowledge_review import build_snapshot, fingerprint


def fixture():
    records=[{'key':'a-1','query':'这个项目有什么用途','answer':'保存的产品说明',
              'thread':'one','updated':'now','kind':'question','citations':[]},
             {'key':'a-2','query':'？','answer':'没有明确问题','thread':'one',
              'updated':'now','kind':'question','citations':[]}]
    review={'topics':[{'id':'broad','title':'产品与应用'}], 'records':[
        {'key':'a-1','fingerprint':fingerprint(records[0]),'primary':'broad','topics':['broad'],
         'rationale':'提问明确涉及产品用途，回答补足对象。','review_state':'classified',
         'evidence':[{'key':'a-1','field':'answer','text':'保存的产品说明'}]},
        {'key':'a-2','fingerprint':fingerprint(records[1]),'primary':None,'topics':[],
         'rationale':'缺少具体问题。','warning':'须明确意图。','review_state':'needs_context'}]}
    return records,review


def test_accounting_and_evidence_preserve_sources():
    records,review=fixture();before=copy.deepcopy(records)
    graph=build_snapshot(records,review,'today')
    assert graph['coverage']=={'reviewed':2,'classified':1,'unclassified':1}
    assert graph['topics'][0]['primary_count']==1
    assert graph['records']['a-1']['proof']['text']=='保存的产品说明'
    assert records==before


@pytest.mark.parametrize('change,error',[
    ('missing','every_source'),('duplicate','every_source'),('source','source_changed'),
    ('eleven','topic_count'),('unknown','unknown_or_duplicate'),('primary','primary_topic'),
    ('fabricated','evidence_not_in_saved'),('cross_thread','same_conversation'),
    ('unresolved','unclassified_reason')])
def test_reject_incomplete_or_untraceable_review(change,error):
    records,review=fixture()
    if change=='missing':review['records'].pop()
    elif change=='duplicate':review['records'].append(review['records'][0])
    elif change=='source':records[0]['answer']='后来改过的回答'
    elif change=='eleven':review['topics']=[{'id':str(n),'title':str(n)} for n in range(11)]
    elif change=='unknown':review['records'][0]['topics']=['unknown']
    elif change=='primary':review['records'][0]['primary']=None
    elif change=='fabricated':review['records'][0]['evidence'][0]['text']='未保存的结论'
    elif change=='cross_thread':
        records[1]['thread']='another'
        review['records'][0]['evidence']=[{'key':'a-2','field':'query','text':'？'}]
    elif change=='unresolved':review['records'][1]['warning']=''
    with pytest.raises(ValueError,match=error):build_snapshot(records,review,'today')

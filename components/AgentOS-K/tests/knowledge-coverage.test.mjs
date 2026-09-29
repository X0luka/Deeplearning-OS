import test from 'node:test';
import assert from 'node:assert/strict';
import {reviewCoverage} from '../aos/host/static/knowledge-coverage.mjs';
const reviewed=(primary='p',evidence=[])=>({reviewed_at:'now',review_state:primary?'classified':'needs_context',primary,topics:primary?[primary]:[],evidence});
test('snapshot entries without review are never counted as classified',()=>{
 const c=reviewCoverage({total:4,records:{a:reviewed(),b:reviewed(null),c:{topics:['p']}},pending:['d'],changed:[]});
 assert.deepEqual([c.reviewed,c.classified,c.unclassified,c.pending],[2,1,1,2]);
 assert.equal(c.primaryCounts.p,1);
});
test('changed or deleted contextual evidence invalidates dependent review',()=>{
 const records={a:reviewed('p',[{key:'b'}]),b:reviewed()};
 const changed=reviewCoverage({total:2,records,changed:['b']});
 assert.equal(changed.reviewed,0);assert.equal(changed.pending,2);
 delete records.b;
 assert.equal(reviewCoverage({total:1,records,changed:[]}).reviewed,0);
});
test('classification count deduplicates secondary topics',()=>{
 const r=reviewed();r.topics=['p','q','r'];
 assert.equal(reviewCoverage({total:1,records:{a:r}}).classified,1);
});

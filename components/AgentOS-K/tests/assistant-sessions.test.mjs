import assert from 'node:assert/strict';
import {contextSessionKey,chooseContextSession} from '../aos/host/static/assistant-sessions.mjs';
const paper={provider:'paper',id:'A'}, other={provider:'paper',id:'B'};
const newest={id:'new',resources:[paper]}, old={id:'old',resources:[paper]}, repository={id:'wiki',resources:[{provider:'wiki',id:'A'}]};
const sessions=[newest,old,repository];
assert.equal(chooseContextSession(sessions,paper,'old'),old);
assert.equal(chooseContextSession(sessions,paper,'wiki'),repository);
assert.equal(chooseContextSession(sessions,paper,'deleted'),newest);
assert.equal(chooseContextSession(sessions,other,null),undefined);
assert.notEqual(contextSessionKey(paper),contextSessionKey(other));
assert.notEqual(contextSessionKey(paper),contextSessionKey({provider:'wiki',id:'A'}));
console.log('conversation restoration tests passed');

assert.equal(chooseContextSession(sessions,paper,null,'old'),old);
assert.equal(chooseContextSession(sessions,paper,null,'wiki'),newest);

const legacy={id:'legacy',legacy:'paper:instance:old-reader-session',resources:[paper]};
assert.equal(chooseContextSession([legacy,...sessions],{...paper,legacy:'old-reader-session'},'old'),old);
assert.equal(chooseContextSession([legacy,...sessions],{...paper,legacy:'old-reader-session'},null),legacy);

import test from 'node:test';
import assert from 'node:assert/strict';
import { allowedRequest, validMutation, encodeArchive } from '../src/lib/analyst-policy.ts';
test('proxy has a finite route surface and rejects paths and mutation tricks', () => {
 assert.equal(allowedRequest('GET', ['workspace']), true);
 assert.equal(allowedRequest('POST', ['imports']), true);
 assert.equal(allowedRequest('GET', ['exports', 'abc', 'download']), true);
 for (const p of [['commands'], ['entities', '..'], ['artifacts','a/b','source'],['workspace','extra']]) assert.equal(allowedRequest('GET', p), false);
 assert.equal(allowedRequest('DELETE',['imports']), false);
});
test('mutations require exact local origin and same-origin metadata', () => {
 const url='http://127.0.0.1:3030/api/analyst/imports';
 assert.equal(validMutation(url,new Headers({origin:'http://127.0.0.1:3030',host:'127.0.0.1:3030','sec-fetch-site':'same-origin'})),true);
 for(const patch of [{origin:'https://evil.test'},{host:'evil.test'},{'sec-fetch-site':'cross-site'}]) assert.equal(validMutation(url,new Headers({origin:'http://127.0.0.1:3030',host:'127.0.0.1:3030','sec-fetch-site':'same-origin',...patch})),false);
});
test('archive encoding is bounded and preserves bytes',()=>{assert.equal(encodeArchive(new Uint8Array([0,255,32])), 'AP8g'); assert.throws(()=>encodeArchive(new Uint8Array(8*1024*1024+1)));});
import { sourceReferences } from '../src/lib/analyst-policy.ts';
test('source inspection derives bounded artifact links from attributed records',()=>{
 assert.deepEqual(sourceReferences({records:[{payload:{spans:[{artifact_id:'artifact:one',start_line:8,end_line:12},{artifact_id:'artifact:one',start_line:8,end_line:12}]}}]}),[{artifact_id:'artifact:one',start_line:8,end_line:12}]);
 assert.deepEqual(sourceReferences({artifact_id:'../private',start_line:1}),[]);
 assert.deepEqual(sourceReferences({artifact_id:'artifact:two',start_line:-3,end_line:9999}),[{artifact_id:'artifact:two',start_line:1,end_line:200}]);
});
import { localRequest } from '../src/lib/analyst-policy.ts';
test('even read-only proxy access requires a local matching host',()=>{
 assert.equal(localRequest('http://127.0.0.1:3030/api/analyst/workspace',new Headers({host:'127.0.0.1:3030'})),true);
 assert.equal(localRequest('http://evil.test/api/analyst/workspace',new Headers({host:'evil.test'})),false);
 assert.equal(localRequest('http://localhost/api/analyst/workspace',new Headers({host:'evil.test'})),false);
});

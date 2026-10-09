const id = /^[A-Za-z0-9:_-]{1,200}$/;
export function allowedRequest(method: string, path: string[]): boolean {
 if (!path.every(segment=>id.test(segment))) return false;
 const route=path.join('/');
 if(method==='GET') return ['workspace','inventory','dependencies','rules','fixtures/analyst'].includes(route) || /^(entities|evidence)\/[^/]+$/.test(route) || /^artifacts\/[^/]+\/source$/.test(route) || /^exports\/[^/]+\/download$/.test(route);
 if(method==='POST') return ['imports','runs','exports','retrieval/query'].includes(route) || /^runs\/[^/]+\/cancel$/.test(route) || /^rules\/[^/]+\/revisions$/.test(route);
 return false;
}
export function localRequest(url:string,headers:Headers):boolean {
 const target=new URL(url);return target.protocol==='http:' && ['localhost','127.0.0.1','[::1]'].includes(target.hostname) && headers.get('host')===target.host;
}
export function validMutation(url:string,headers:Headers):boolean {
 const target=new URL(url);
 return localRequest(url,headers) && headers.get('origin')===target.origin && (!headers.has('sec-fetch-site') || headers.get('sec-fetch-site')==='same-origin');
}
export function encodeArchive(bytes:Uint8Array):string {
 if(bytes.length>8*1024*1024) throw new Error('ZIP must be 8 MB or smaller.');
 let binary=''; for(let i=0;i<bytes.length;i+=8192) binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
 return btoa(binary);
}
export function sourceReferences(data:unknown):{artifact_id:string;start_line:number;end_line:number}[]{
 const found=new Map<string,{artifact_id:string;start_line:number;end_line:number}>();let work=0;
 function visit(node:unknown,depth:number){if(depth>12||++work>10000||found.size>=100)return;if(Array.isArray(node)){for(const entry of node)visit(entry,depth+1);return;}if(!node||typeof node!=='object')return;
 const record=node as Record<string,unknown>;const artifact=record.artifact_id;
 if(typeof artifact==='string'&&id.test(artifact)){const start=typeof record.start_line==='number'&&Number.isSafeInteger(record.start_line)&&record.start_line>0?record.start_line:1;const end=typeof record.end_line==='number'&&Number.isSafeInteger(record.end_line)&&record.end_line>=start?Math.min(record.end_line,start+199):start+199;found.set(`${artifact}:${start}:${end}`,{artifact_id:artifact,start_line:start,end_line:end});}
 for(const entry of Object.values(record))visit(entry,depth+1);
 }
 visit(data,0);return [...found.values()];
}

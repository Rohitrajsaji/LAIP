import { readFile } from 'node:fs/promises';
import { NextRequest } from 'next/server';
import { parseRuntime } from '../../../../lib/runtime';
import { allowedRequest, validMutation, localRequest } from '../../../../lib/analyst-policy';
export const dynamic='force-dynamic';
async function proxy(request:NextRequest,context:{params:Promise<{path:string[]}>}) {
 const {path}=await context.params;
 // Next standalone sees the container address; validate the browser Host directly.
 const host=request.headers.get('host');
 let publicUrl:string;
 try { publicUrl=new URL(request.nextUrl.pathname+request.nextUrl.search,`http://${host??''}`).href; } catch { return Response.json({error:'Local host required.'},{status:403}); }
 if(!localRequest(publicUrl,request.headers)) return Response.json({error:'Local host required.'},{status:403});
 if(!allowedRequest(request.method,path)) return Response.json({error:'Route unavailable.'},{status:404});
 if(request.method==='POST'&&!validMutation(publicUrl,request.headers)) return Response.json({error:'Request origin rejected.'},{status:403});
 try {
 const env={...process.env};
 if(env.LAIP_SERVICE_TOKEN_FILE) { if(env.LAIP_SERVICE_TOKEN) throw new Error(); env.LAIP_SERVICE_TOKEN=(await readFile(env.LAIP_SERVICE_TOKEN_FILE,'utf8')).trim(); }
 const config=parseRuntime(env);
 let body:string|undefined;
 if(request.method==='POST') {
 if(!request.headers.get('content-type')?.startsWith('application/json')) return Response.json({error:'JSON required.'},{status:415});
 if(Number(request.headers.get('content-length')??0)>12*1024*1024) return Response.json({error:'Request too large.'},{status:413});
 const reader=request.body?.getReader(); if(!reader) throw new Error();
 const chunks:Uint8Array[]=[];let size=0;
 while(true){const item=await reader.read();if(item.done)break;size+=item.value.length;if(size>12*1024*1024){await reader.cancel();return Response.json({error:'Request too large.'},{status:413});}chunks.push(item.value);}
 body=Buffer.concat(chunks).toString('utf8');const parsed=JSON.parse(body);if(path.join('/')==='exports' && parsed.source_included!==false) return Response.json({error:'Full source export is unavailable in the analyst workspace.'},{status:403});
 }
 const query=new URLSearchParams();const permitted=new Set(['snapshot_id','schema_version','start_line','end_line']);
 for(const [key,value] of request.nextUrl.searchParams){if(!permitted.has(key)||value.length>200) return Response.json({error:'Invalid query.'},{status:400});query.append(key,value);}
 const upstream=await fetch(`${config.apiUrl}/api/v1/${path.map(encodeURIComponent).join('/')}?${query}`,{method:request.method,body,headers:{Authorization:`Bearer ${config.serviceToken}`,'Content-Type':'application/json'},signal:AbortSignal.timeout(15000),cache:'no-store',redirect:'error'});
 const download=path.at(-1)==='download'; const max=download?32*1024*1024:12*1024*1024;
 const reader=upstream.body?.getReader();const chunks:Uint8Array[]=[];let size=0;
 if(reader)while(true){const item=await reader.read();if(item.done)break;size+=item.value.length;if(size>max){await reader.cancel();throw new Error();}chunks.push(item.value);}
 const data=Buffer.concat(chunks);
 if(!upstream.ok) return Response.json({error:`Request could not be completed (${upstream.status}). Refresh and check the selected snapshot.`},{status:upstream.status});
 return new Response(data,{status:upstream.status,headers:{'Content-Type':download?'application/zip':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...(download?{'Content-Disposition':'attachment; filename="laip-knowledge.zip"'}:{})}});
 }catch{return Response.json({error:'Local service is unavailable. Try again.'},{status:503});}
}
export {proxy as GET,proxy as POST};

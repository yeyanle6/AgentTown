import { env } from 'cloudflare:workers';
import { z } from 'zod';
export const localMode = () => !!(import.meta as unknown as {env?:{DEV?:boolean}}).env?.DEV;
export function rawDb(){if(!env.DB)throw new Error('社区数据库尚未连接');return env.DB;}
export function json(value:unknown,status=200){return Response.json(value,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});}
export async function localRequest(path:string,init?:RequestInit){const response=await fetch('http://127.0.0.1:8766'+path,init);return new Response(response.body,{status:response.status,headers:new Headers(response.headers)});}
export function sameOrigin(request:Request){const origin=request.headers.get('origin');return !origin || origin===new URL(request.url).origin;}
export const CommandSchema=z.object({id:z.string().regex(/^[a-zA-Z0-9_-]{8,100}$/).optional(),kind:z.enum(['pause','resume','tick','create_task','cancel_task','set_employee']),payload:z.record(z.unknown()).optional().default({})});
export async function syncAuthorized(request:Request){
 const expected=(env as Cloudflare.Env & {AGENTTOWN_SYNC_KEY?:string}).AGENTTOWN_SYNC_KEY;
 if(!expected)return false;
 const actual=request.headers.get('x-agenttown-key')||'';
 const enc=new TextEncoder();const a=new Uint8Array(await crypto.subtle.digest('SHA-256',enc.encode(actual)));const b=new Uint8Array(await crypto.subtle.digest('SHA-256',enc.encode(expected)));let diff=0;for(let i=0;i<a.length;i++)diff|=a[i]^b[i];return diff===0;
}

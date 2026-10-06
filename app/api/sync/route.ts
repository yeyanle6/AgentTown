import { json,rawDb,syncAuthorized } from '@/lib/community-server';
import { z } from 'zod';
const Artifact=z.object({id:z.string().max(100),task_id:z.string().max(100),author:z.string(),title:z.string(),kind:z.string(),content:z.string().max(250000),sha256:z.string().length(64),created_at:z.string(),mode:z.enum(['real','replay','demo']),sources:z.string()});
const Source=z.object({id:z.string().max(100),name:z.string(),url:z.string(),observed_at:z.string().nullable(),retrieved_at:z.string(),mode:z.enum(['real','replay','demo']),status:z.string(),delay_seconds:z.number().nullable(),payload:z.string().max(250000),sha256:z.string().length(64),error:z.string().nullable()});
const Sync=z.object({snapshot:z.object({meta:z.object({run_id:z.string(),heartbeat:z.string(),stop_at:z.string(),state:z.string()}).passthrough()}).passthrough(),artifacts:z.array(Artifact).max(500),sources:z.array(Source).max(500).default([]),command_results:z.array(z.object({id:z.string(),kind:z.string(),result:z.string(),created_at:z.string()})).max(200)});
export async function POST(request:Request){
 try{
  if(!await syncAuthorized(request))return json({error:'同步身份验证失败'},403);
  if(Number(request.headers.get('content-length')||0)>5000000)return json({error:'同步数据过大'},413);
  const raw=await request.text();if(raw.length>5000000)return json({error:'同步数据过大'},413);
  const body=Sync.parse(JSON.parse(raw));const db=rawDb();const received=body.snapshot.meta.heartbeat;
  if(!Number.isFinite(Date.parse(received))||Date.parse(received)>Date.now()+60000)return json({error:'无效心跳时间'},400);
  const statements:D1PreparedStatement[]=[];
  for(const artifact of body.artifacts){
   const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(artifact.content)))].map(n=>n.toString(16).padStart(2,'0')).join('');
   if(hash!==artifact.sha256)return json({error:'成果内容指纹不匹配'},400);
   statements.push(db.prepare('INSERT OR IGNORE INTO community_artifacts(id,task_id,payload,sha256,created_at) VALUES(?,?,?,?,?)').bind(artifact.id,artifact.task_id,JSON.stringify(artifact),hash,artifact.created_at));
  }
  for(const source of body.sources){
   const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(source.payload)))].map(n=>n.toString(16).padStart(2,'0')).join('');
   if(hash!==source.sha256)return json({error:'来源内容指纹不匹配'},400);
   statements.push(db.prepare('INSERT OR IGNORE INTO community_sources(id,name,payload,sha256,retrieved_at) VALUES(?,?,?,?,?)').bind(source.id,source.name,JSON.stringify(source),hash,source.retrieved_at));
  }
  for(const result of body.command_results){const parsed=JSON.parse(result.result);statements.push(db.prepare('UPDATE community_commands SET status=?,result=? WHERE id=?').bind(parsed.status==='failed'?'failed':'applied',result.result,result.id));}
  for(let i=0;i<statements.length;i+=50)await db.batch(statements.slice(i,i+50));
  await db.prepare("INSERT INTO community_snapshots(id,run_id,payload,updated_at) VALUES('current',?,?,?) ON CONFLICT(id) DO UPDATE SET run_id=excluded.run_id,payload=excluded.payload,updated_at=excluded.updated_at WHERE excluded.updated_at>community_snapshots.updated_at").bind(body.snapshot.meta.run_id,JSON.stringify(body.snapshot),received).run();
  const commands=await db.prepare("SELECT id,kind,payload FROM community_commands WHERE status='pending' ORDER BY created_at LIMIT 50").all();
  return json({ok:true,sources_accepted_ids:body.sources.map(source=>source.id),commands:commands.results});
 }catch(error){console.error('community-sync',error instanceof Error?error.message:'failed');return json({error:'同步未完成，执行器将安全重试'},500);}
}

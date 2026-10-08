import type { Json } from './types'

let localBearer = ''
export function setLocalBearer(value:string) {localBearer=value}
function headers() {return {...(localBearer?{Authorization:`Bearer ${localBearer}`}:{})}}
export class ApiError extends Error {constructor(message:string, readonly status:number){super(message)}}
export async function api<T>(path:string, method='GET', body?:unknown, signal?:AbortSignal):Promise<T> {
  const response = await fetch(path, {method, signal, cache:'no-store',
    headers: {'Content-Type':'application/json', ...headers()},
    ...(body===undefined?{}:{body:JSON.stringify(body)})})
  const data = await response.json() as T & {error?:string;issues?:string[]}
  if(!response.ok) throw new ApiError(data.error||data.issues?.join('；')||`请求失败（${response.status}）`,response.status)
  return data
}
export async function fetchBlob(path:string, signal?:AbortSignal):Promise<Blob> {
  const response=await fetch(path,{signal,cache:'no-store',headers:headers()})
  if(!response.ok){let message=`请求失败（${response.status}）`;try{message=(await response.json()).error||message}catch{}throw new ApiError(message,response.status)}
  return response.blob()
}
export async function downloadFile(path:string,name:string) {
  const url=URL.createObjectURL(await fetchBlob(path)),link=document.createElement('a')
  link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
export function download(name:string, value:Json|unknown) {
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'}))
  const link=document.createElement('a');link.href=url;link.download=name;link.click();URL.revokeObjectURL(url)
}

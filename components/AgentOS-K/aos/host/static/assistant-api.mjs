// Retry only explicit authentication rejection; never replay ambiguous network failures.
export function createAuthenticatedFetch({fetcher=fetch, getToken, refreshToken}) {
  let refreshing=null;
  return async function authenticatedFetch(url, options={}) {
    const used=getToken();
    const send=token=>fetcher(url,{...options,headers:{...options.headers,'X-AOS-Token':token}});
    let response=await send(used);
    if(response.status!==403)return response;
    const error=await response.clone().json().catch(()=>({}));
    if(error.detail!=='write_token_required')return response;
    if(getToken()===used){
      refreshing ||= Promise.resolve().then(refreshToken).finally(()=>{refreshing=null;});
      await refreshing;
    }
    if(options.signal?.aborted)throw new DOMException('Aborted','AbortError');
    return send(getToken());
  };
}

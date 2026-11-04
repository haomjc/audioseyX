const activeDownloads=new Set();

async function saveCheckedDownload(link){
 const path=link.getAttribute('href');
 if(activeDownloads.has(path))return;
 activeDownloads.add(path);const label=link.textContent;
 try{
  const info=await api('/api/download-info?path='+encodeURIComponent(path));
  if(!Number.isSafeInteger(info.bytes)||info.bytes<=0||info.bytes>256*1024*1024)throw new Error("Invalid download size or size limit exceeded");
  const chunks=[],size=1024*1024;
  for(let start=0;start<info.bytes;start+=size){
   const end=Math.min(start+size,info.bytes)-1;let chunk,lastError;
   for(let attempt=0;attempt<3;attempt++){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
    try{
     const response=await fetch(info.url,{headers:{Range:`bytes=${start}-${end}`,'If-Match':'"'+info.sha256+'"'},signal:controller.signal});
     if(response.status===412)throw new Error("The download changed; restart the download");
     if(response.status!==206){const detail=await response.json().catch(()=>null);throw new Error(detail?.error||"The local download service did not return a valid segment")}
     if(response.headers.get('Content-Range')!==`bytes ${start}-${end}/${info.bytes}`)throw new Error("The download segment range does not match");
     chunk=await response.arrayBuffer();if(chunk.byteLength!==end-start+1)throw new Error("The download segment is incomplete");
     break;
    }catch(error){chunk=null;lastError=error;if(error.message.includes("changed"))break}
    finally{clearTimeout(timer)}
   }
   if(!chunk)throw new Error(lastError?.message||"Download incomplete; please retry");
   chunks.push(chunk);link.textContent=`Saving ${Math.round((end+1)/info.bytes*100)}%`;
  }
  const blob=new Blob(chunks,{type:info.mime}),digest=await crypto.subtle.digest('SHA-256',await blob.arrayBuffer());
  const actual=[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('');
  if(actual!==info.sha256)throw new Error("File integrity check failed; the incomplete file was not saved");
  const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=info.filename;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  toast("File verified and ready to save");
 }finally{activeDownloads.delete(path);link.textContent=label}
}

document.querySelectorAll('a[href^="/download"]').forEach(link=>link.addEventListener('click',event=>{
 event.preventDefault();if(link.classList.contains('disabled'))return;action(()=>saveCheckedDownload(link));
}));

// NEVER DEPLOY: loopback-only dashboard ingress. Test controls are unreachable here.
const http = require('node:http');
const net = require('node:net');
const upstream = Number(process.env.INTERNAL_PORT);
const server = http.createServer((req,res) => {
  if (req.method !== 'GET' || req.url.startsWith('/__test/')) {
    res.writeHead(405, {'content-type':'text/plain'}); res.end('read-only dashboard'); return;
  }
  const proxy = http.request({host:'127.0.0.1',port:upstream,path:req.url,method:req.method,headers:{...req.headers,host:`127.0.0.1:${upstream}`}}, r => {res.writeHead(r.statusCode,r.headers); r.pipe(res);});
  proxy.on('error',()=>{if (!res.headersSent) res.writeHead(502); res.end();});
  req.pipe(proxy);
});
server.on('upgrade',(req,socket,head)=>{
  if (req.method !== 'GET' || !req.url.startsWith('/__spike/socket?')) {socket.destroy(); return;}
  const dest=net.connect(upstream,'127.0.0.1',()=>{
    const headers={...req.headers,host:`127.0.0.1:${upstream}`};
    dest.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`+Object.entries(headers).map(([k,v])=>`${k}: ${v}\r\n`).join('')+'\r\n');
    if (head.length) dest.write(head); socket.pipe(dest).pipe(socket);
  });
  dest.on('error',()=>socket.destroy()); socket.on('error',()=>dest.destroy());
});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));

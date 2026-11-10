"""Isolated Docker Nginx TLS/ACL fixture. No production hosts or published ports."""
import json
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
NETWORK='cia-tls-'+uuid.uuid4().hex[:12]
API=NETWORK+'-api';NGINX=NETWORK+'-nginx'
SUBNET='192.0.2.0/24';API_IP='192.0.2.2';NGINX_IP='192.0.2.3'
ALLOWED='192.0.2.10';DENIED='192.0.2.11'
PYTHON_IMAGE='python:3.12-slim';NGINX_IMAGE='nginx:stable-alpine'

def docker(*args,check=True):
    return subprocess.run(['docker',*args],text=True,capture_output=True,check=check,timeout=180)

API_SOURCE='''
from http.server import HTTPServer,BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'fixture-health')
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')))
  self.send_response(401);self.end_headers();self.wfile.write(b'fixture-signed-auth-required')
 def log_message(self,*args):pass
HTTPServer(('0.0.0.0',8000),Handler).serve_forever()
'''
CLIENT_SOURCE='''
import json,ssl,urllib.request,urllib.error,sys
method,path,trust=sys.argv[1:]
context=ssl.create_default_context(cafile=trust)
request=urllib.request.Request('https://192.0.2.3:8443'+path,method=method,data=b'{}' if method=='POST' else None)
try:
 with urllib.request.urlopen(request,context=context,timeout=5) as response: print(json.dumps({'http':response.status,'body':response.read().decode()}))
except urllib.error.HTTPError as error:print(json.dumps({'http':error.code,'body':error.read().decode()}))
'''

def main():
    with tempfile.TemporaryDirectory(prefix='cia-nginx-tls-') as temp:
        root=Path(temp)
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
            '-keyout',str(root/'service-control.key'),'-out',str(root/'service-control.crt'),
            '-days','1','-subj','/CN=local-nginx-fixture','-addext','subjectAltName=IP:'+NGINX_IP,
            '-addext','basicConstraints=critical,CA:FALSE','-addext','extendedKeyUsage=serverAuth'],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        original=(ROOT/'deploy/nginx.session-control.conf.template').read_text()
        assert 'allow 10.20.20.10;' in original and 'deny all;' in original
        assert 'limit_except POST' in original and 'proxy_pass http://api:8000/health;' in original
        # Substitute ONLY the source address and proxy key for this isolated fixture.
        rendered=original.replace('10.20.20.10',ALLOWED).replace('${PROXY_SECRET}',secrets.token_hex(32))
        (root/'nginx.conf').write_text('events {}\nhttp {\n'+rendered+'\n}\n')
        (root/'api.py').write_text(API_SOURCE);(root/'client.py').write_text(CLIENT_SOURCE)
        created=False
        try:
            docker('pull',PYTHON_IMAGE);docker('pull',NGINX_IMAGE)
            docker('network','create','--internal','--subnet',SUBNET,NETWORK);created=True
            docker('run','-d','--name',API,'--network',NETWORK,'--ip',API_IP,'--network-alias','api',
                   '-v',str(root/'api.py')+':/fixture/api.py:ro',PYTHON_IMAGE,'python','/fixture/api.py')
            docker('run','-d','--name',NGINX,'--network',NETWORK,'--ip',NGINX_IP,
                   '-v',str(root/'nginx.conf')+':/etc/nginx/nginx.conf:ro',
                   '-v',str(root)+':/etc/nginx/session-control:ro',NGINX_IMAGE)
            for _ in range(30):
                result=docker('exec',NGINX,'nginx','-t',check=False)
                if result.returncode==0:break
                time.sleep(.2)
            assert result.returncode==0,result.stderr
            def request(ip,method,path):
                result=docker('run','--rm','--network',NETWORK,'--ip',ip,
                    '-v',str(root/'client.py')+':/fixture/client.py:ro',
                    '-v',str(root/'service-control.crt')+':/fixture/trust.crt:ro',
                    PYTHON_IMAGE,'python','/fixture/client.py',method,path,'/fixture/trust.crt')
                return json.loads(result.stdout)
            health='/internal/security/health';control='/internal/security/revoke-test-sessions'
            for _ in range(20):
                result=request(ALLOWED,'GET',health)
                if result.get('http')==200:break
                time.sleep(.2)
            assert result=={'http':200,'body':'fixture-health'},result
            assert request(DENIED,'GET',health)['http']==403
            assert request(ALLOWED,'POST',health)['http']==403
            result=request(ALLOWED,'POST',control)
            assert result=={'http':401,'body':'fixture-signed-auth-required'},result
            assert request(DENIED,'POST',control)['http']==403
            assert request(ALLOWED,'GET',control)['http']==403
            assert request(ALLOWED,'GET','/api/employees')['http']==404
            # The proposed ordinary HTTP listener must not expose the control action.
            ordinary=(ROOT/'deploy/nginx.conf.template').read_text()
            assert 'location = /internal/security/revoke-test-sessions' not in ordinary
            print(json.dumps({'fixture_only':True,'nginx_syntax':'passed','checks':8,
                'allowed_health':200,'denied_health':403,'allowed_unsigned_action':401,
                'denied_action':403,'method_limits':'passed','ordinary_http_control_absent':True,
                'production_acl_verified':False}))
        finally:
            docker('rm','-f',NGINX,API,check=False)
            if created:docker('network','rm',NETWORK,check=False)

if __name__=='__main__':main()

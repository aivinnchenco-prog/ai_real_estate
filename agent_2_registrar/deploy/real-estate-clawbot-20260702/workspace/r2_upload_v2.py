#!/usr/bin/env python3
import os, sys, hashlib, hmac
from datetime import datetime
from urllib.request import Request, urlopen

ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
BUCKET = os.environ.get("CLOUDFLARE_BUCKET", "real-estate-propertiess")
ACCESS_KEY = os.environ.get("CLOUDFLARE_ACCESS_KEY_ID", "")
SECRET_KEY = os.environ.get("CLOUDFLARE_SECRET_ACCESS_KEY", "")

def upload(obj_id, filepath, filename):
    key = f"{obj_id}/{filename}"
    with open(filepath, 'rb') as f:
        payload = f.read()
    
    now = datetime.utcnow()
    amz_date = now.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = now.strftime('%Y%m%d')
    payload_hash = hashlib.sha256(payload).hexdigest()
    
    canonical_uri = f"/{BUCKET}/{key}"
    host = f"{ACCOUNT_ID}.r2.cloudflarestorage.com"
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = f"PUT\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    
    canonical_request_hash = hashlib.sha256(canonical_request.encode()).hexdigest()
    credential_scope = f"{date_stamp}/auto/s3/aws4_request"
    string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{canonical_request_hash}"
    
    kDate = hmac.new(f"AWS4{SECRET_KEY}".encode(), date_stamp.encode(), hashlib.sha256).digest()
    kRegion = hmac.new(kDate, b"auto", hashlib.sha256).digest()
    kService = hmac.new(kRegion, b"s3", hashlib.sha256).digest()
    kSigning = hmac.new(kService, b"aws4_request", hashlib.sha256).digest()
    signature = hmac.new(kSigning, string_to_sign.encode(), hashlib.sha256).hexdigest()
    
    auth = f"AWS4-HMAC-SHA256 Credential={ACCESS_KEY}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    
    url = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com/{BUCKET}/{key}"
    headers = {
        "Authorization": auth,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Content-Type": "image/jpeg",
        "Content-Length": str(len(payload))
    }
    
    try:
        req = Request(url, data=payload, headers=headers, method="PUT")
        with urlopen(req) as r:
            return r.status == 200
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False

if __name__ == '__main__':
    obj_id, filepath, filename = sys.argv[1:4]
    result = upload(obj_id, filepath, filename)
    print("✓" if result else "✗")

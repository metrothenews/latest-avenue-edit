# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
A small S3 client (AWS Signature V4, path-style), standard library only: enough for the
editor's storage (put, get, list, delete, make the bucket). Works with SeaweedFS on the PC now
and with Google Cloud Storage's S3 interface (HMAC keys) or AWS S3 later, unchanged.

Settings from the environment:

``S3_ENDPOINT``    e.g. http://s3:8333 (SeaweedFS in compose), https://storage.googleapis.com
``S3_BUCKET``      e.g. latest-avenue
``S3_ACCESS_KEY``, ``S3_SECRET_KEY``
``S3_REGION``      default us-east-1 (GCS: auto)

Objects are stored per user: ``users/<uid>/<path inside the data folder>``; local disk is a
cache (``mirror_up`` after work, ``ensure_local`` before).
"""

import datetime
import hashlib
import hmac
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class S3Error(Exception):
    pass


def configured():
    return bool(os.environ.get("S3_ENDPOINT") and os.environ.get("S3_BUCKET"))


def _cfg():
    return (os.environ["S3_ENDPOINT"].rstrip("/"), os.environ["S3_BUCKET"],
            os.environ.get("S3_ACCESS_KEY", ""), os.environ.get("S3_SECRET_KEY", ""),
            os.environ.get("S3_REGION", "us-east-1"))


def _sign(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _request(method, key="", query=None, body=b"", payload_hash=None, headers=None, stream_to=None):
    endpoint, bucket, access, secret, region = _cfg()
    url = urllib.parse.urlsplit(endpoint)
    path = "/" + bucket + ("/" + urllib.parse.quote(key, safe="/~") if key else "")
    q = sorted((query or {}).items())
    canonical_query = "&".join("{:s}={:s}".format(urllib.parse.quote(k, safe="~"), urllib.parse.quote(str(v), safe="~"))
                               for k, v in q)
    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload_hash = payload_hash or hashlib.sha256(body if isinstance(body, bytes) else b"").hexdigest()
    hdrs = {"host": url.netloc, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    hdrs.update({k.lower(): v for k, v in (headers or {}).items()})
    signed = ";".join(sorted(hdrs))
    canonical = "\n".join([method, path, canonical_query,
                           "".join("{:s}:{:s}\n".format(k, str(hdrs[k]).strip()) for k in sorted(hdrs)),
                           signed, payload_hash])
    scope = "{:s}/{:s}/s3/aws4_request".format(date, region)
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(("AWS4" + secret).encode(), date), region), "s3"), "aws4_request")
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    hdrs["authorization"] = "AWS4-HMAC-SHA256 Credential={:s}/{:s}, SignedHeaders={:s}, Signature={:s}".format(
        access, scope, signed, signature)
    full = endpoint + path + ("?" + canonical_query if canonical_query else "")
    req = urllib.request.Request(full, data=body if method in ("PUT", "POST") else None, method=method,
                                 headers={k: v for k, v in hdrs.items() if k != "host"})
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as ex:
        raise S3Error("{:s} {:s}: {:d} {:s}".format(method, key or bucket, ex.code,
                                                    ex.read()[:300].decode("utf-8", "replace"))) from None
    except urllib.error.URLError as ex:
        raise S3Error("storage not reachable at {:s}: {!s}".format(endpoint, ex.reason)) from None
    with resp:
        if stream_to:
            with open(stream_to, "wb") as fh:
                while True:
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    fh.write(block)
            return None
        return resp.read()


def ensure_bucket():
    try:
        _request("HEAD")
    except S3Error:
        _request("PUT")


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def put_file(key, path):
    with open(path, "rb") as fh:
        data = fh.read()        # media files here are proxies / clips / renders; fine in memory
    _request("PUT", key, body=data, payload_hash=hashlib.sha256(data).hexdigest(),
             headers={"content-length": str(len(data))})
    return {"key": key, "bytes": len(data)}


def get_file(key, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".s3part"
    _request("GET", key, stream_to=tmp)
    os.replace(tmp, path)
    return path


def delete(key):
    _request("DELETE", key)


def list_keys(prefix):
    out, token = [], None
    while True:
        q = {"list-type": "2", "prefix": prefix}
        if token:
            q["continuation-token"] = token
        root = ET.fromstring(_request("GET", query=q))
        ns = {"s": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
        find = (lambda el, tag: el.findall("s:" + tag, ns)) if ns else (lambda el, tag: el.findall(tag))
        for c in find(root, "Contents"):
            key = c.find("s:Key", ns) if ns else c.find("Key")
            size = c.find("s:Size", ns) if ns else c.find("Size")
            out.append({"key": key.text, "bytes": int(size.text)})
        trunc = root.find("s:IsTruncated", ns) if ns else root.find("IsTruncated")
        nxt = root.find("s:NextContinuationToken", ns) if ns else root.find("NextContinuationToken")
        if trunc is not None and trunc.text == "true" and nxt is not None:
            token = nxt.text
        else:
            return out


# -----------------------------------------------------------------------------
# The data folder <-> per-user storage.

def user_key(uid, data_root, path):
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(data_root))
    if rel.startswith(".."):
        raise S3Error("{:s} is outside the data folder".format(path))
    return "users/{:s}/{:s}".format(uid, rel.replace(os.sep, "/"))


def mirror_up(uid, data_root, paths):
    """Upload files (or every file under folders) to the user's storage. Skips files whose
    size already matches. -> list of keys written."""
    if not configured():
        return []
    done = []
    existing = None
    for p in paths:
        files = [p] if os.path.isfile(p) else [os.path.join(dp, f) for dp, _d, fs in os.walk(p) for f in fs
                                                if not f.endswith((".part", ".tmp", ".s3part"))]
        for f in files:
            key = user_key(uid, data_root, f)
            if existing is None:
                existing = {o["key"]: o["bytes"] for o in list_keys("users/{:s}/".format(uid))}
            if existing.get(key) == os.path.getsize(f):
                continue
            put_file(key, f)
            done.append(key)
    return done


def ensure_local(uid, data_root, path):
    """Bring a file or folder back from storage if the local cache lost it. -> files fetched."""
    if not configured():
        return []
    prefix = user_key(uid, data_root, path)
    fetched = []
    for o in list_keys(prefix):
        rel = o["key"][len("users/{:s}/".format(uid)):]
        local = os.path.join(data_root, *rel.split("/"))
        if not os.path.exists(local) or os.path.getsize(local) != o["bytes"]:
            get_file(o["key"], local)
            fetched.append(local)
    return fetched

"""
Render the showcase: each protected document as seen by each role, via the live
/api/kustodyan/document/<id>/ endpoint. Run: python manage.py shell < demo/render_showcase.py
"""
import json
import os
import urllib.error
import urllib.request

from rest_framework.authtoken.models import Token

from documents.models import Document

# paperless rejects a pod-IP/localhost Host (DisallowedHost) → send an allowed host
PAPERLESS_HOST = os.environ.get("PAPERLESS_HOST", "localhost")

USERS = [
    ("steward", "DATA_STEWARD  (full)"),
    ("worker", "CASE_WORKER   (masked)"),
    ("clerk", "STAFF         (read token)"),
    ("nobody", "no role       (denied)"),
]
tokens = {u: Token.objects.get(user__username=u).key for u, _ in USERS}
docs = list(Document.objects.exclude(content="").order_by("id"))


def view(doc_id, token):
    req = urllib.request.Request(
        f"http://localhost:8000/api/kustodyan/document/{doc_id}/",
        headers={"Authorization": "Token " + token, "Host": PAPERLESS_HOST,
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:120]
    except urllib.error.URLError as e:
        return None, str(e)


def short(s, n=78):
    s = str(s)
    return s if len(s) <= n else s[:n] + "…"


for doc in docs:
    print("\n" + "=" * 96)
    print(f"DOCUMENT #{doc.id}")
    print(f"  AT REST  title:   {short(doc.title, 50)}")
    print(f"  AT REST  content: {short(doc.content)}")
    print(f"  AT REST  tags:    {[short(t.name, 36) for t in doc.tags.all()]}")
    print("-" * 96)
    for u, label in USERS:
        st, body = view(doc.id, tokens[u])
        if st != 200:
            print(f"  {label:26} -> HTTP {st}  {body}")
        else:
            print(f"  {label:26} title:   {short(body['title'], 50)}")
            print(f"  {'':26} content: {short(body['content'])}")
            print(f"  {'':26} tags:    {[short(t, 36) for t in body['tags']]}")

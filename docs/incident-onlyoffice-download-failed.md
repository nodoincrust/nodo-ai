# OnlyOffice "Download failed" — root cause and fix

**Date:** 1 Sep 2026
**Systems:** nodo-ai backend (systemd `nodo-backend`), OnlyOffice Document Server (docker `onlyoffice`)

| Area | Status |
|---|---|
| Root cause | Identified |
| Opening documents | Fixed and verified |
| Saving edits | Fix applied, not yet tested |

---

## What was happening

Opening any document in the editor produced a generic **"Download failed"** dialog.

The backend looked healthy the whole time: `/details` returned 200, the presigned URL was
generated without error, and nothing appeared in the application log. The failure was
entirely on the Document Server side, and it affected every document — going back to at
least 31 August.

From `docservice/out.log` inside the container:

```
[ERROR] nodeJS - error downloadFile:url=https://nodo-ai-dev-private.s3.ap-south-1.amazonaws.com/companies/28/documents/255/… ;attempt=3;code:ERR_BAD_REQUEST
AxiosError: Request failed with status code 400
```

---

## The cause

The Document Server runs with `JWT_ENABLED=true`. In that mode it attaches an
`Authorization: Bearer <jwt>` header to its **outbound** requests — including the request it
makes to fetch the document itself.

Our document URLs are S3 presigned URLs, which carry their authentication in the query
string. S3 refuses to accept two authentication mechanisms on one request and answers **400**:

```xml
<Error>
  <Code>InvalidArgument</Code>
  <Message>Only one auth mechanism allowed; only the X-Amz-Algorithm query parameter,
           Signature query string parameter or the Authorization header should be specified</Message>
  <ArgumentName>Authorization</ArgumentName>
</Error>
```

`JWT_HEADER` was never set on the container, so OnlyOffice used its default header name —
`Authorization` — which is the one name S3 treats as an auth mechanism.

---

## Why it took a while

Every layer looked correct in isolation, and the error message pointed at none of them.
These were each checked and eliminated — recorded here so nobody repeats the work:

| Checked | Result |
|---|---|
| Bucket region | Correct. `get_bucket_location` returned `ap-south-1`, matching the signature scope. |
| Credentials | Valid. Authenticated S3 API calls succeeded from the backend host. |
| Clock skew | None. Container and host clocks matched to the second. |
| Container network | Fine. `curl` from inside the container downloaded the exact file OnlyOffice could not. |
| **The request OnlyOffice made** | **The problem.** Same URL, same container, same second — one extra header, and S3 rejected it. |

### Two things that would have saved hours

1. The download error is logged in `docservice/out.log`, **not** `converter/out.log`. The
   converter never runs if the download fails, so its log stays empty and looks fine.
2. S3 puts the real reason in the response **body**. A status code alone
   (`curl -o /dev/null -w "%{http_code}"`) tells you almost nothing. Drop the `-o /dev/null`
   and S3 names the problem outright.

---

## What changed

Four changes went in. Only the first was the actual fix; the rest are real bugs found along
the way. Note which live in git and which are server-side only.

| Change | Why | Lives in | Status |
|---|---|---|---|
| `JWT_HEADER=AuthorizationJwt` on the DS container | Stops OnlyOffice sending `Authorization` on the S3 fetch. **The fix.** | container env | Verified |
| Pin the S3 regional endpoint in `app/AIhelpers/s3_storage.py` (also fails fast when `AWS_REGION` is missing) | URLs were being signed against the legacy global endpoint. Hardening, not the cause. | commit `3cdfd15` | Deployed |
| CORS `allow_origins=["*"]` in `app/main.py` | Preflights returned 400 "Disallowed CORS origin", so OTP login failed until users retried. | commit `8f7e684` | Deployed |
| `BACKEND_BASE_URL` corrected — `http://15.206.84.64/:8000` → `http://172.17.0.1:8000` | Stray slash before the port, and it named a different host, so save callbacks went nowhere. | server `.env` | **Needs testing** |

### The container command

The DS container was recreated with the new variable. Secret value unchanged — take it from
the server `.env`:

```bash
docker rm -f onlyoffice
docker run -d --name onlyoffice --restart always \
  -p 8080:80 \
  -e JWT_ENABLED=true \
  -e JWT_SECRET='<ONLYOFFICE_SECRET from server .env>' \
  -e JWT_HEADER=AuthorizationJwt \
  onlyoffice/documentserver:latest
```

### Why `172.17.0.1`

`BACKEND_BASE_URL` is used in exactly one place — building `callbackUrl` in the editor config
(`app/helpers.py`). OnlyOffice resolves that URL **server-side, from inside its container**,
when it posts the saved file back. So it has to be an address the container can reach:
`172.17.0.1` is the docker0 bridge gateway, and the backend listens on `0.0.0.0:8000` on that
host. `localhost` would hit the container itself, where OnlyOffice's own service listens on
port 8000.

---

## Confirming it yourself

Two requests, same URL, same container. The only difference is the header — and that
difference is the whole bug.

```bash
# downloads the file
docker exec onlyoffice curl -sS "<presigned url>"

# returns the 400 error
docker exec onlyoffice curl -sS -H "Authorization: Bearer test" "<presigned url>"
```

Generate a presigned URL on the backend host with:

```bash
cd /var/www/nodo-ai/backend && source /var/www/nodo-ai/venv/bin/activate
python -c "
from app.AIhelpers.s3_storage import generateSignedUrl
print(generateSignedUrl('<s3 key from document_versions.file_path>'))
"
```

---

## Still open

The first item is the one that can undo this work. The rest were found while reading the
code and are unrelated to this incident.

- **Reconcile `docker-compose.yml`** — *regression risk.* The running container is
  `onlyoffice`, started by hand with no volumes. Compose describes
  `onlyoffice-documentserver` with volumes and no `JWT_HEADER`; bringing it up reintroduces
  the bug. Its `backend` service has also never worked (`CMD` is `uvicorn main:app` instead
  of `app.main:app`, and Redis/Ollama aren't in the file while the code hardcodes them to
  `localhost`).
- **Rotate the AWS access key** — *security.* The dev key was exposed in plaintext during
  debugging.
- **Remove the secret `print`** — *security.* `app/services/document_service.py:42` writes
  `ONLYOFFICE_SECRET` to journald on every boot.
- **Authenticate the AI router** — *security.* No endpoint in `app/controllers/ai_controller.py`
  requires a token; any caller can chat with or summarise any company's documents by ID.
- **OTP is hardcoded to `1234`** — *security.* `app/services/admin_service.py:61`, with email
  sending commented out. Any known address can log in.
- **Doubled filename prefix** — *data hygiene.* Keys read `v1_v1_<name>.xlsx`; the stored
  filename already carries a version prefix, so it compounds on each reupload (`v2_v1_…`).
- **Logging is configured by accident** — *reliability.* Nothing in the app calls
  `basicConfig`; log output exists only because `pdf2docx` sets it on import. Drop that
  import and we go blind.

---

## Testing the save path

Still unverified. It only fires when you edit a document **you uploaded** whose status is
`DRAFT`, `REJECTED`, or `REUPLOADED` — anything else opens read-only and never calls back
(`app/helpers.py`, `build_onlyoffice_editor`).

1. Open such a document, change a cell, close the tab, wait ~10s.
2. Watch `sudo journalctl -u nodo-backend -f | grep -i callback` for
   `POST /nodo/newdocuments/onlyoffice/callback/<id> … 200 OK`.
3. Reopen and confirm the edit persisted.

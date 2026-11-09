# HTTPS public origin for the loopback host

Apply this on deployed commit `a27041552f5bff69e2ec04f1fae1402f4d7ba5f1`. Do not hand-edit `node_modules` or `dist`. Do not print the launch token in chat.

## Cause

Chrome’s `Origin` is `https://7860-cpod-1xhxbe8kv5n4.pod.compshare.cn`. nginx currently forwards `Host: 127.0.0.1:3086`. `@deepseek-ai/dsh-client-connection` returns 403 unless `Origin` matches `Host`. The listener is plain HTTP, so session cookies were not `Secure`. `--public-url` only changes the printed URL. It does not admit the origin.

## Patch

From `/workspace/youdub-deploy/YouDub-webui`:

```sh
git apply --check artifacts/youdub-https-public-origin.patch
git apply artifacts/youdub-https-public-origin.patch
npm run build:framework
```

`npm run build:framework` already uses `registry.npmmirror.com`. No model download.

## Host

Set this only on the process that listens on `127.0.0.1:3086`:

```sh
export YOUDUB_PUBLIC_ORIGIN='https://7860-cpod-1xhxbe8kv5n4.pod.compshare.cn'
```

The same value may be passed as `--public-origin` after `--no-open`. One exact origin. A wildcard, path, or second host is rejected at startup. Unset, localhost behavior is unchanged.

Restart that process in its existing tmux/nohup session. The log line `YouDub web: https://7860-cpod-1xhxbe8kv5n4.pod.compshare.cn/?token=...` is the browser URL.

## nginx

Keep `proxy_pass http://127.0.0.1:3086`. Do not delete or rewrite `Origin` or `Sec-Fetch-*`. The platform already terminated TLS, so `$scheme` inside the pod is `http`. Set the forwarded scheme explicitly:

```nginx
map $http_upgrade $connection_upgrade {
  default upgrade;
  ''      close;
}
server {
  listen 7860;
  server_name 7860-cpod-1xhxbe8kv5n4.pod.compshare.cn;
  location / {
    proxy_pass http://127.0.0.1:3086;
    proxy_http_version 1.1;
    proxy_set_header Host 127.0.0.1:3086;
    proxy_set_header X-Forwarded-Host 7860-cpod-1xhxbe8kv5n4.pod.compshare.cn;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_buffering off;
  }
}
```

`nginx -t && nginx -s reload`

## After the UI works

Write only the full `https://...?token=...` URL to `/workspace/access_link.txt` and `chmod 0600` it. Do not put that token in chat.

## Rollback

```sh
git apply -R artifacts/youdub-https-public-origin.patch
unset YOUDUB_PUBLIC_ORIGIN
npm run build:framework
```

Restore the previous nginx file, reload nginx, and restart the host.

## Already applied the previous patch

If `2706b4d` is already applied and `npm run build:framework` stopped on `TS6133` for unused `isTrusted`, apply only the correction, then rebuild:

```sh
git apply --check artifacts/youdub-https-public-origin-since-2706b4d.patch
git apply artifacts/youdub-https-public-origin-since-2706b4d.patch
npm run build:framework
```

That patch deletes the unused private method. Admission still goes through `viewIsTrusted`. Do not restart the old host until this rebuild finishes.

## What was verified here

`npm run build:framework` completed (`EXIT:0`), including the host TypeScript compile that previously reported `rpc-host.ts` `TS6133`.

`node --import tsx --test apps/harness/test/public-origin-proxy.integration.mjs` (tsx 4.20.6): 4 passing.

Covered: exact origin parse and wildcard rejection; forwarded headers from a non-loopback peer ignored; authenticated `https://7860-cpod-1xhxbe8kv5n4.pod.compshare.cn` `POST /api/settings/describe`, `POST /api/youdub/v2`, and WebSocket `/api/remote.mux` 101 with a frame; missing cookie, wrong cookie, and wrong token 401; foreign Origin and a comma-joined forwarded host 403; direct `http://127.0.0.1:<port>` cookie without `Secure`, API 200, and WebSocket 101.

Not verified here: the deployed Electron process, the real workbench page, file upload, or GPU inference. Those stay with the coordinator.
